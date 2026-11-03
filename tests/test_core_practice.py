"""練習モード (seizou.core.practice・命令 seizou_practice・帯と題・雛形の settings の塊)。

process は本当に立てず、practice の 5 つの関数 (port_is_open・verify_settings・launch・wait_ready・terminate) を差し替える。
どの試験も BASE_DIR と MEDIA_ROOT を一時のフォルダに替える (リポジトリに practice/ を作らず、ほかの試験と共有の
MEDIA_ROOT を写さないため)。
"""

import importlib.util
from pathlib import Path

from seizou import cli


def load_settings(path: Path, name: str):
    """描いた settings.py を読む (sys.modules に入れない)。"""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def same_path(a, b) -> bool:
    return Path(a).resolve() == Path(b).resolve()


def test_scaffold_settings_reads_practice_env(tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    text = cli.render("settings.py.tmpl", id="sample_co", seizou_items="    'id': 'sample_co',\n    'port': 8010,")
    (site / "settings.py").write_text(text, encoding="utf-8")
    copy = tmp_path / "copy"
    monkeypatch.setenv("SECRET_KEY", "fake-practice-test-key")
    monkeypatch.delenv("SEIZOU_DB", raising=False)
    monkeypatch.setenv("SEIZOU_PRACTICE_DB", str(copy / "db.sqlite3"))
    monkeypatch.setenv("SEIZOU_PRACTICE_MEDIA", str(copy / "media"))

    practice = load_settings(site / "settings.py", "practice_settings_with_env")
    assert same_path(str(practice.DATABASES["default"]["NAME"]), copy / "db.sqlite3")
    assert same_path(str(practice.MEDIA_ROOT), copy / "media")
    assert practice.SESSION_COOKIE_NAME == "seizou_sample_co_session_practice"
    assert practice.CSRF_COOKIE_NAME == "seizou_sample_co_csrf_practice"

    monkeypatch.delenv("SEIZOU_PRACTICE_DB")
    monkeypatch.delenv("SEIZOU_PRACTICE_MEDIA")
    plain = load_settings(site / "settings.py", "practice_settings_without_env")
    assert same_path(str(plain.DATABASES["default"]["NAME"]), site / "db.sqlite3")
    assert same_path(str(plain.MEDIA_ROOT), site / "media")
    assert plain.SESSION_COOKIE_NAME == "seizou_sample_co_session"
    assert plain.CSRF_COOKIE_NAME == "seizou_sample_co_csrf"
    assert "_practice" not in plain.SESSION_COOKIE_NAME + plain.CSRF_COOKIE_NAME
