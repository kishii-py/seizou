"""外の置き場 (VPS などの https の置き場) のための seizou new の口 (src/seizou/cli.py と src/seizou/core/demo.py)。

- --demo のつながりの種の受け口の URL の頭 (http://127.0.0.1:800x) を、答えの partner_base_urls か環境変数
  SEIZOU_PARTNER_<相手の id の大文字>_URL の頭に置き換える (環境変数が勝つ。無ければ種のまま)。
- 管理者の ID とパスワードを「できた物」の後に 1 行ずつ出す。--password-file なら、パスワードを画面に出さず、
  持ち主だけが読めるファイルに admin:<値> の 1 行で書く。

置き場は一時のフォルダ (環境変数 SEIZOU_SITES_DIR)。URL は作り物 (example.jp)。
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from seizou import cli
from seizou.core import demo
from tests.test_cli_new import FIXTURE, run_new

COMPANIES_DIR = Path(__file__).resolve().parent.parent / "examples" / "companies"
RECEIVE_PATH = "/connect/receive/order/"
# 置き場のつながりを「CONN 取引先コード 向き 受け口の URL」の 1 行ずつ出す (読むだけ)
SHOW_CONNECTIONS = (
    "import importlib; Connection = importlib.import_module('seizou.connect.models').Connection; "
    "[print('CONN', c.partner.code, c.direction, c.url or '-') for c in Connection.objects.order_by('pk')]"
)
# 置き場の管理者が、環境変数 SEIZOU_TEST_PASSWORD のパスワードで入れるか (読むだけ)
CHECK_PASSWORD = (
    "import importlib, os; User = importlib.import_module('django.contrib.auth').get_user_model(); "
    "print('PASSWORD', User.objects.get(username='admin').check_password(os.environ['SEIZOU_TEST_PASSWORD']))"
)
PASSWORD_LINE = re.compile(r"^管理者のパスワード: (\S+)$")


def _password_works(site: Path, value: str) -> bool:
    result = cli.run_manage(site, ["shell", "--no-imports", "-c", CHECK_PASSWORD], {"SEIZOU_TEST_PASSWORD": value})
    assert result.returncode == 0, result.stderr
    return "PASSWORD True" in result.stdout


def test_partner_base_urls_rebase_seed_connections(tmp_path, monkeypatch):
    # 答えの partner_base_urls で、A 社の種の「送る」の受け口が B 社の外の置き場の URL になる (「受ける」の空の URL はそのまま)
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path / "sites"))
    for name in [name for name in os.environ if cli.PARTNER_URL_ENV.fullmatch(name)]:
        monkeypatch.delenv(name)  # 回す人の環境の頭 (外の置き場の作り直しのスクリプトが置く物) を読まない
    shutil.copytree(COMPANIES_DIR / "a_machining", tmp_path / "a_machining")
    answers = tmp_path / "a_machining.yaml"
    text = (COMPANIES_DIR / "a_machining.yaml").read_text(encoding="utf-8")
    answers.write_text(text + "\npartner_base_urls:\n  b_plating: https://b.example.jp/\n", encoding="utf-8")

    code, out, err = run_new(["new", str(answers), "--demo"])

    assert code == 0, err
    assert "つながりの種の受け口の URL の頭を、相手の置き場の URL にします: b_plating → https://b.example.jp" in out
    result = cli.run_manage(tmp_path / "sites" / "a_machining", ["shell", "--no-imports", "-c", SHOW_CONNECTIONS])
    assert result.returncode == 0, result.stderr
    rows = sorted(line.split()[1:] for line in result.stdout.splitlines() if line.startswith("CONN "))
    assert rows == [["S001", "receive", "-"], ["S001", "send", "https://b.example.jp" + RECEIVE_PATH]]

    # 渡さない相手・空の URL・形の違う鍵の置き場の名前は、種の URL のまま (無ければ今までどおり)
    seed = "http://127.0.0.1:8002" + RECEIVE_PATH
    assert demo._rebase_url(seed, "SEIZOU_PARTNER_B_PLATING_SECRET", None) == seed
    assert demo._rebase_url(seed, "SEIZOU_PARTNER_B_PLATING_SECRET", {"a_machining": "https://a.example.jp"}) == seed
    assert demo._rebase_url("", "SEIZOU_PARTNER_B_PLATING_SECRET", {"b_plating": "https://b.example.jp"}) == ""
    assert demo._rebase_url(seed, "B_PLATING_KEY", {"b_plating": "https://b.example.jp"}) == seed
    # 頭に道があれば、その後に種の道をつなぐ
    assert demo._rebase_url(seed, "SEIZOU_PARTNER_B_PLATING_SECRET", {"b_plating": "https://x.example.jp/b"}) == (
        "https://x.example.jp/b" + RECEIVE_PATH
    )


def test_partner_url_env_wins_over_answers(tmp_path, monkeypatch):
    answers = {"partner_base_urls": {"b_plating": "https://yaml.example.jp", "c_plain": "http://c.example.jp:8443/"}}
    environ = {
        "SEIZOU_PARTNER_B_PLATING_URL": "https://env.example.jp/",
        "SEIZOU_PARTNER_B_PLATING_SECRET": "作り物の鍵",
        "SEIZOU_PARTNER_D_EMPTY_URL": " ",
    }
    assert cli.partner_base_urls(answers, environ) == {"b_plating": "https://env.example.jp", "c_plain": "http://c.example.jp:8443"}
    assert cli.partner_base_urls({}, {}) == {}

    # 形の違う頭は、置き場を作る前に止まる (終わりの番号 2)。環境変数は --demo のときだけ読む
    sites = tmp_path / "sites"
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(sites))
    monkeypatch.setenv("SEIZOU_PARTNER_B_PLATING_URL", "b.example.jp")
    code, _out, err = run_new(["new", str(FIXTURE), "--demo"])
    assert code == 2
    assert "SEIZOU_PARTNER_B_PLATING_URL" in err
    monkeypatch.delenv("SEIZOU_PARTNER_B_PLATING_URL")
    for text in ("partner_base_urls:\n  b_plating: ftp://b.example.jp\n", "partner_base_urls:\n  B-plating: https://b.example.jp\n", "partner_base_urls: https://b.example.jp\n"):
        bad = tmp_path / "answers.yaml"
        bad.write_text(FIXTURE.read_text(encoding="utf-8") + text, encoding="utf-8")
        code, _out, err = run_new(["new", str(bad)])
        assert code == 2, text
        assert "partner_base_urls" in err
    assert not sites.exists()


def test_admin_password_lines_and_password_file(tmp_path, monkeypatch):
    # 画面には ID とパスワードを「できた物」の後に 1 行ずつ出す (行の頭で拾える)。前の「ID: … / パスワード: …」の形は出さない
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path / "shown"))
    code, out, err = run_new(["new", str(FIXTURE)])
    assert code == 0, err
    lines = out.splitlines()
    made = next(number for number, line in enumerate(lines) if line.startswith("できた物: "))
    assert lines[made + 1] == "管理者の ID: admin"
    shown = [match.group(1) for match in map(PASSWORD_LINE.match, lines) if match]
    assert len(shown) == 1
    assert PASSWORD_LINE.match(lines[made + 2])
    assert "/ パスワード:" not in out
    assert _password_works(tmp_path / "shown" / "sample_co", shown[0])

    # --password-file: 値は画面に出さず、持ち主だけが読めるファイルに admin:<値> の 1 行で書く
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path / "filed"))
    target = tmp_path / "secrets" / "admin.txt"
    target.parent.mkdir()
    code, out, err = run_new(["new", str(FIXTURE), "--password-file", str(target)])
    assert code == 0, err
    text = target.read_text(encoding="utf-8")
    assert re.fullmatch(r"admin:\S+\n", text)
    value = text.strip().split(":", 1)[1]
    assert value not in out + err
    assert "管理者の ID: admin" in out.splitlines()
    assert not [line for line in out.splitlines() if line.startswith("管理者のパスワード: ")]
    assert str(target) in out
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600
    assert _password_works(tmp_path / "filed" / "sample_co", value)

    # ファイルのフォルダが無ければ、置き場を作る前に止まる (終わりの番号 2)
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path / "missing"))
    code, _out, err = run_new(["new", str(FIXTURE), "--password-file", str(tmp_path / "no_such" / "admin.txt")])
    assert code == 2
    assert "--password-file" in err
    assert not (tmp_path / "missing").exists()
