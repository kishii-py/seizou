"""作り物の利用者 3 人の公開の合言葉 (決め 11-04 15:0x。依頼書 N116)。

点検 (seizou_doctor) が公開の合言葉のままの人を出し、変えると消えること。
種 (accounts.demo_seed.load) が合言葉を受けること。
seizou new --demo --demo-password random が 3 人に別々の乱数を作って 1 人 1 行で出し、置き場の 3 人がその値で入れること。
合言葉と利用者は作り物 (公開の合言葉は docs/getting-started.md に書いてある字)。
"""

import io
import json
import os
import re
import stat
from pathlib import Path

import pytest
from django.contrib.auth import authenticate
from django.core.management import call_command
from django.core.management.base import CommandError

from seizou import cli
from seizou.accounts import demo_seed, services
from seizou.accounts.models import Access
from seizou.core import doctor

COMPANIES_DIR = Path(__file__).resolve().parent.parent / "examples" / "companies"
LABEL = "作り物の利用者の合言葉"
#: 試験で渡す合言葉 (作り物。アカウントの 4 つの確かめに通る字)
GIVEN = "kari-no-aikotoba-n116"
CHANGED = "kaeta-aikotoba-n116"


def _demo_rows(production):
    return [row for row in doctor.run(production=production) if row.label == LABEL]


def _change(login_id):
    services.update_account(Access.objects.get(user__username=login_id), {}, None, password=CHANGED)


@pytest.mark.django_db
def test_doctor_lists_public_password_until_changed(monkeypatch):
    monkeypatch.delenv(demo_seed.PASSWORDS_ENV, raising=False)
    demo_seed.load(None)

    # 本番の条件では直す。文は「作り物の利用者 <ID> が公開の合言葉のままです」と「アカウント管理で変えてください。」
    rows = _demo_rows(True)
    assert len(rows) == 1 and rows[0].ok is False
    assert rows[0].detail == "作り物の利用者 genba・jimu・keiri が公開の合言葉のままです"
    assert rows[0].fix == "アカウント管理で変えてください。"
    out = io.StringIO()
    with pytest.raises(CommandError):
        call_command("seizou_doctor", "--production", stdout=out)
    line = "直す  作り物の利用者の合言葉: 作り物の利用者 genba・jimu・keiri が公開の合言葉のままです → 直し方: アカウント管理で変えてください。"
    assert line in out.getvalue().splitlines()

    # 開発の設定では良いの行に注意の文 (直すに数えない)
    rows = _demo_rows(False)
    assert len(rows) == 1 and rows[0].ok is True
    assert rows[0].detail.startswith("作り物の利用者 genba・jimu・keiri が公開の合言葉のままです。")

    # 変えた人は行から消え、3 人とも変えると良いの行になる
    _change("keiri")
    assert _demo_rows(True)[0].detail == "作り物の利用者 genba・jimu が公開の合言葉のままです"
    _change("genba")
    _change("jimu")
    assert _demo_rows(True) == [doctor.Row(True, LABEL, "公開の合言葉のままの作り物の利用者はいない")]

    # 点検の行に合言葉の値を出さない
    shown = "\n".join(f"{row.detail} {row.fix}" for row in doctor.run(production=True))
    leaked = demo_seed.DEMO_PASSWORD in shown or CHANGED in shown
    assert not leaked, "点検の行に合言葉の値が出た"


@pytest.mark.django_db
def test_load_takes_password(monkeypatch):
    monkeypatch.delenv(demo_seed.PASSWORDS_ENV, raising=False)

    first = demo_seed.load(None, password=GIVEN)

    assert first["アカウント"] == {"入れた": 3, "飛ばした": 0}
    for login_id in ("genba", "jimu", "keiri"):
        assert authenticate(username=login_id, password=GIVEN) is not None, login_id
        assert authenticate(username=login_id, password=demo_seed.DEMO_PASSWORD) is None, login_id

    # 2 回目は飛ばし、既にある人の合言葉は変えない
    again = demo_seed.load(None, password=CHANGED)

    assert again["アカウント"] == {"入れた": 0, "飛ばした": 3}
    assert authenticate(username="genba", password=GIVEN) is not None


def test_seizou_new_demo_password_random(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path))
    monkeypatch.delenv(demo_seed.PASSWORDS_ENV, raising=False)
    # cli は Django を読まないので、ログイン ID と環境変数の名前を写している (種と同じであること)
    assert cli.DEMO_ACCOUNT_IDS == tuple(row[0] for row in demo_seed.ACCOUNTS)
    assert cli.DEMO_PASSWORDS_ENV == demo_seed.PASSWORDS_ENV

    code = cli.main(["new", str(COMPANIES_DIR / "a_machining.yaml"), "--demo", "--demo-password", "random"])

    out = capsys.readouterr().out
    assert code == 0, out
    shown = dict(re.findall(r"^作り物の利用者 (\w+) のパスワード: (\S+)$", out, re.M))
    assert list(shown) == ["genba", "jimu", "keiri"]
    assert len(set(shown.values())) == 3
    assert demo_seed.DEMO_PASSWORD not in shown.values()

    # 置き場のデータベースで、出た値なら入れて、公開の合言葉では入れない
    site = tmp_path / "a_machining"
    check = (
        "import importlib, json; authenticate = importlib.import_module('django.contrib.auth').authenticate; "
        "shown = json.loads({shown!r}); "
        "print('LOGIN', json.dumps([[authenticate(username=k, password=v) is not None, "
        "authenticate(username=k, password={public!r}) is not None] for k, v in shown.items()]))"
    ).format(shown=json.dumps(shown), public=demo_seed.DEMO_PASSWORD)
    result = cli.run_manage(site, ["shell", "--no-imports", "-c", check])
    assert result.returncode == 0, result.stderr
    login = [line for line in result.stdout.splitlines() if line.startswith("LOGIN ")]
    assert json.loads(login[0].split(" ", 1)[1]) == [[True, False]] * 3

    # 置き場の点検 (本番の条件) で、作り物の利用者の行は良い
    result = cli.run_manage(site, ["seizou_doctor", "--production"])
    rows = [line for line in result.stdout.splitlines() if LABEL in line]
    assert rows == [f"良い  {LABEL}: 公開の合言葉のままの作り物の利用者はいない"]


def test_seizou_new_demo_password_random_to_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path / "sites"))
    monkeypatch.delenv(demo_seed.PASSWORDS_ENV, raising=False)
    secret = tmp_path / "passwords.txt"

    code = cli.main(
        ["new", str(COMPANIES_DIR / "b_plating.yaml"), "--demo", "--demo-password", "random", "--password-file", str(secret)]
    )

    out = capsys.readouterr().out
    assert code == 0, out
    lines = secret.read_text(encoding="utf-8").splitlines()
    assert [line.split(":", 1)[0] for line in lines] == ["admin", "genba", "jimu", "keiri"]
    assert not re.search(r"^作り物の利用者 \w+ のパスワード: ", out, re.M)
    leaked = any(line.split(":", 1)[1] in out for line in lines)
    assert not leaked, "画面にパスワードの値が出た"
    if os.name != "nt":
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600
