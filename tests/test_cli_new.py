"""命令 seizou new (src/seizou/cli.py) の試験。

置き場は一時のフォルダ (環境変数 SEIZOU_SITES_DIR)。main を直接呼び、子の process は
migrate と管理者と check だけ。試験用の答えは tests/fixtures/companies/sample.yaml (作り物)。
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from seizou import cli
from seizou.cli import main
from seizou.parts import PARTS

FIXTURE = Path(__file__).parent / "fixtures" / "companies" / "sample.yaml"
SITE_FILES = ("manage.py", "settings.py", ".env", "answers.yaml", "README.md", "db.sqlite3", "deploy/nginx-files.conf")
SITE_FOLDERS = ("templates", "static/css", "manual/images", "deploy")


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


def test_fields_app_from_item_fields(built, tmp_path, monkeypatch):
    fields = built["site"] / "fields"
    for name in ("__init__.py", "apps.py", "models.py", "migrations/__init__.py"):
        assert (fields / name).is_file(), name
    models_text = (fields / "models.py").read_text(encoding="utf-8")
    assert "class ItemExt(models.Model):" in models_text
    assert re.search(r"""ext01 = models\.CharField\(['"]材質['"], max_length=100, blank=True\)""", models_text)
    assert re.search(r"""ext02 = models\.CharField\(['"]表面の仕上げ['"], max_length=100, choices=""", models_text)
    assert "めっき" in models_text
    apps_text = (fields / "apps.py").read_text(encoding="utf-8")
    assert "ITEM_FIELDS = [" in apps_text
    assert re.search(r"""['"]name['"]: ['"]ext02['"]""", apps_text)
    # core の会社ごとの項目の仕組み (seizou.core.fields) に ready() で登録する (コメントでなく生きた行)。
    register_lines = [line for line in apps_text.splitlines() if "fields.register(" in line]
    assert register_lines and not any(line.strip().startswith("#") for line in register_lines)
    assert "from seizou.core import fields" in apps_text

    # item_fields が空なら fields/ を作らない。
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path / "sites"))
    text = "id: bare_co\nname: 見本の会社\nindustry: 機械加工\nitem_fields: []\nport: 8012\n"
    answers = write_answers(tmp_path / "answers.yaml", text)
    code, _out, err = run_new(["new", str(answers)])
    assert code == 0, err
    assert not (tmp_path / "sites" / "bare_co" / "fields").exists()


def test_fields_needs_masters_in_parts(built):
    site = built["site"]
    text = (site / "settings.py").read_text(encoding="utf-8")
    assert '"seizou.masters" in PARTS' in text
    assert (site / "fields").is_dir()
    result = manage(site, "check")
    assert result.returncode == 0, result.stdout + result.stderr
    code = "from django.conf import settings; print('fields' in settings.INSTALLED_APPS)"
    result = manage(site, "shell", "--no-imports", "-c", code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str("seizou.masters" in PARTS)


def test_checks_py_is_an_empty_template(built):
    text = (built["site"] / "checks.py").read_text(encoding="utf-8")
    register_lines = [line for line in text.splitlines() if "register(" in line]
    assert register_lines and all(line.strip().startswith("#") for line in register_lines)
    # コードとしては説明の文字列だけ (何も登録しない)。
    body = ast.parse(text).body
    assert len(body) == 1 and isinstance(body[0], ast.Expr)


def test_demo_without_loader_exits_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path))
    real_find_spec = importlib.util.find_spec

    def find_spec(name, *args, **kwargs):
        if name == "seizou.core.demo":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(cli.importlib.util, "find_spec", find_spec)
    code, out, err = run_new(["new", str(FIXTURE), "--demo"])
    assert code == 0, err
    assert "作り物のデータの読み込みは後の版です" in out

    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert "from seizou.core" not in source
    assert not re.search(r"import django|from django", source)
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        assert not [n for n in names if n.startswith(("django", "seizou.core"))], names


def test_model_line_covers_every_type():
    columns = [
        {"name": f"ext0{n}", "label": kind, "type": kind, "required": n % 2 == 0, "choices": ["甲", "乙"]}
        for n, kind in enumerate(cli.FIELD_TYPES, start=1)
    ]
    lines = [cli.model_line(column) for column in columns]
    compile("from django.db import models\n\n\nclass X:\n" + "\n".join(lines) + "\n", "models.py", "exec")
    # 必須は 2 番目と 4 番目 (number・choice)。必須でない数と日付は null と空を許す。
    assert "DecimalField('number', max_digits=14, decimal_places=2)" in lines[1]
    assert "DateField('date', null=True, blank=True)" in lines[2]
    assert "BooleanField('bool', default=False, blank=True)" in lines[4]


def test_company_row_has_answer_name(built):
    # 画面の上の帯に出る会社名は、答えの name で core の Company に 1 行作る。
    result = manage(
        built["site"],
        "shell",
        "--no-imports",
        "-c",
        "from seizou.core.models import Company; print(Company.objects.get().name)",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "見本の会社"
