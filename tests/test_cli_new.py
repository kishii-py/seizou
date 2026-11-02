"""命令 seizou new (src/seizou/cli.py) の試験。

置き場は一時のフォルダ (環境変数 SEIZOU_SITES_DIR)。main を直接呼び、子の process は
migrate と管理者と check だけ。試験用の答えは tests/fixtures/companies/sample.yaml (作り物)。
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from seizou.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "companies" / "sample.yaml"
SITE_FILES = ("manage.py", "settings.py", ".env", "answers.yaml", "README.md", "db.sqlite3")
SITE_FOLDERS = ("templates", "static/css", "manual/images")


def run_new(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def manage(site: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "settings"}
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=site,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def secret_value(site: Path) -> str:
    lines = [line for line in (site / ".env").read_text(encoding="utf-8").splitlines() if line.startswith("SECRET_KEY=")]
    return lines[0].split("=", 1)[1]


def write_answers(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """試験用の答えで 1 度だけ作った置き場と、そのときの出力 (読むだけの試験で使い回す)。"""
    sites = tmp_path_factory.mktemp("sites")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SEIZOU_SITES_DIR", str(sites))
        code, out, err = run_new(["new", str(FIXTURE)])
    return {"code": code, "out": out, "err": err, "site": sites / "sample_co"}


def test_new_creates_project_files(built):
    assert built["code"] == 0, built["err"]
    site = built["site"]
    for name in SITE_FILES:
        assert (site / name).is_file(), name
    for folder in SITE_FOLDERS:
        assert (site / folder).is_dir(), folder


def test_settings_follow_the_contract(built):
    site = built["site"]
    text = (site / "settings.py").read_text(encoding="utf-8")
    for needle in (
        "seizou_sample_co_session",
        "seizou_sample_co_csrf",
        'AUTH_USER_MODEL = "core.User"',
        'ROOT_URLCONF = "seizou.core.urls"',
        "SessionStorage",
        "from seizou.parts import PARTS",
        "IMMEDIATE",
    ):
        assert needle in text, needle
    block = re.search(r"^SEIZOU = \{\n(.*?)^\}", text, re.S | re.M)
    assert block, "SEIZOU の辞書が無い"
    assert re.search(r"""['"]id['"]: ['"]sample_co['"],""", block.group(1))
    assert re.search(r"""['"]port['"]: 8010,""", block.group(1))
    assert re.search(r"""['"]partial_shipment['"]: True,""", block.group(1))
    secret = secret_value(site)
    assert secret not in text
    assert [line for line in text.splitlines() if "SECRET_KEY" in line] == ['SECRET_KEY = os.environ["SECRET_KEY"]']


def test_env_has_only_generated_secret(built):
    lines = (built["site"] / ".env").read_text(encoding="utf-8").splitlines()
    keys = [line for line in lines if line.startswith("SECRET_KEY=")]
    assert len(keys) == 1
    assert len(keys[0].split("=", 1)[1]) > 0
    assert "DEBUG=1" in lines
    # 取引先の鍵は人が足す。値の例の行 (コメントでない行) は置かない。
    assert not [line for line in lines if line.startswith("SEIZOU_PARTNER_")]


def test_manage_check_and_migrate_pass(built):
    site = built["site"]
    result = manage(site, "check")
    assert result.returncode == 0, result.stdout + result.stderr
    result = manage(site, "migrate", "--check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_admin_user_is_created(built):
    code = "from django.contrib.auth import get_user_model; print(get_user_model().objects.filter(is_superuser=True).count())"
    result = manage(built["site"], "shell", "--no-imports", "-c", code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


def test_output_tells_the_start_line(built):
    out = built["out"]
    assert "manage.py runserver 8010" in out
    assert "管理者の ID: admin" in out
    assert "http://127.0.0.1:8010/login/" in out
    assert secret_value(built["site"]) not in out + built["err"]


@pytest.mark.parametrize("bad_id", ["sample-co", "Sample_co", "1sample"])
def test_invalid_id_is_refused(tmp_path, monkeypatch, bad_id):
    sites = tmp_path / "sites"
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(sites))
    text = FIXTURE.read_text(encoding="utf-8").replace("id: sample_co", f"id: {bad_id}")
    answers = write_answers(tmp_path / "answers.yaml", text)
    code, _out, err = run_new(["new", str(answers)])
    assert code == 2
    assert "id" in err
    assert not sites.exists()


def test_existing_project_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path))
    site = tmp_path / "sample_co"
    site.mkdir()
    (site / "manage.py").write_text("# 前からある物\n", encoding="utf-8")
    (site / "settings.py").write_text("# 前からある物\n", encoding="utf-8")
    code, _out, err = run_new(["new", str(FIXTURE)])
    assert code == 2
    assert "置き場を消して" in err
    assert (site / "settings.py").read_text(encoding="utf-8") == "# 前からある物\n"
    assert not (site / ".env").exists()


def test_answers_already_in_place_is_fine(tmp_path, monkeypatch):
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path))
    original = FIXTURE.read_text(encoding="utf-8")
    answers = write_answers(tmp_path / "sample_co" / "answers.yaml", original)
    code, _out, err = run_new(["new", str(answers)])
    assert code == 0, err
    assert answers.read_text(encoding="utf-8") == original
    assert (tmp_path / "sample_co" / "manage.py").is_file()


def test_missing_settings_use_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path / "sites"))
    answers = write_answers(tmp_path / "answers.yaml", "id: plain_co\nname: 見本の会社\nport: 8011\n")
    code, out, err = run_new(["new", str(answers)])
    assert code == 0, err
    assert "既定を使いました" in out
    text = (tmp_path / "sites" / "plain_co" / "settings.py").read_text(encoding="utf-8")
    assert re.search(r"""['"]over_shipment['"]: ['"]warn['"],""", text)


def test_unknown_field_type_is_refused(tmp_path, monkeypatch):
    sites = tmp_path / "sites"
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(sites))
    text = FIXTURE.read_text(encoding="utf-8").replace("type: text", "type: color")
    answers = write_answers(tmp_path / "answers.yaml", text)
    code, _out, err = run_new(["new", str(answers)])
    assert code == 2
    assert "color" in err
    assert not sites.exists()
