"""控えと戻し (seizou.core.backup・命令 seizou_backup・seizou_restore)。

SQLite の道は backup API を本物で、PostgreSQL の道は pg_dump を差し替えて見る。鍵の値が出力に出ない事を見る。
どの試験も BASE_DIR と MEDIA_ROOT を一時のフォルダに替える (リポジトリに backups/ を作らず、ほかの試験と共有の
MEDIA_ROOT を戻しで消さないため)。値を含む字の比べは、失敗の文に値が出ないよう真偽にしてから assert する。
"""

import io
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from seizou.core import backup

sqlite_only = pytest.mark.skipif(
    connection.vendor != "sqlite",
    reason="SQLite の backup API の試験 (PostgreSQL の組では pg_dump の道だけを見る)",
)

FAKE_SECRET_KEY = "fake-secret-key-must-not-appear-0123456789-abcdefghijklmnopqrstuvwxyz"
FAKE_PARTNER_SECRET = "fake-partner-secret-must-not-appear"
FAKE_DB_PASSWORD = "fake-db-password-must-not-appear"


@pytest.fixture
def site(settings, tmp_path):
    site = tmp_path / "site"
    drawings = site / "media" / "drawings"
    drawings.mkdir(parents=True)
    (drawings / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (site / "answers.yaml").write_text("id: sample_co\nname: 見本の会社\nport: 8010\n", encoding="utf-8")
    settings.BASE_DIR = site
    settings.MEDIA_ROOT = site / "media"
    settings.SEIZOU = {"id": "sample_co", "port": 8010}
    return site


def run(name, *args, **opts) -> str:
    out, err = io.StringIO(), io.StringIO()
    call_command(name, *args, stdout=out, stderr=err, **opts)
    return out.getvalue() + err.getvalue()


def users(backup_db: Path) -> set[str]:
    """控えの db.sqlite3 の利用者の名前 (試験の中なので sqlite3 で直に読む)。"""
    con = sqlite3.connect(str(backup_db))
    try:
        return {row[0] for row in con.execute("SELECT username FROM core_user")}
    finally:
        con.close()


def folders(parent: Path) -> list[Path]:
    if not parent.is_dir():
        return []
    return sorted(path for path in parent.iterdir() if path.is_dir())


def plain_backups(parent: Path) -> list[Path]:
    return [path for path in folders(parent) if not path.name.endswith(backup.RETREAT_SUFFIX)]


def retreats(parent: Path) -> list[Path]:
    return [path for path in folders(parent) if path.name.endswith(backup.RETREAT_SUFFIX)]


def make_user(name):
    return get_user_model().objects.create_user(name, password="pass-for-tests-only")


def user_exists(name) -> bool:
    return get_user_model().objects.filter(username=name).exists()


def hand_made_backup(parent: Path, name: str, **changes) -> Path:
    """backup.json と空の DB の写しだけを持つ、手で作った控え (今の DB の種類に合わせる)。"""
    kind = connection.vendor
    manifest = {
        "format": backup.FORMAT,
        "created_at": "2026-11-03T02:00:00",
        "database": kind,
        "db_file": backup.DB_FILES.get(kind, "db.sqlite3"),
        "company_id": "sample_co",
        "seizou_version": "",
        "media_files": 0,
        "answers": False,
    }
    manifest.update(changes)
    folder = parent / name
    folder.mkdir(parents=True)
    (folder / backup.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    (folder / str(manifest["db_file"])).write_bytes(b"")
    return folder


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_backup_creates_folder_with_db_media_answers_and_manifest(site):
    make_user("before")

    out = run("seizou_backup")

    made = folders(site / "backups")
    assert len(made) == 1
    folder = made[0]
    assert backup.NAME_PATTERN.match(folder.name)
    assert "before" in users(folder / "db.sqlite3")
    assert (folder / "media" / "drawings" / "a.png").read_bytes() == b"\x89PNG\r\n\x1a\n"
    assert (folder / "answers.yaml").is_file()
    manifest = json.loads((folder / "backup.json").read_text(encoding="utf-8"))
    assert manifest["format"] == 1
    assert manifest["database"] == "sqlite"
    assert manifest["company_id"] == "sample_co"
    assert manifest["media_files"] == 1
    assert manifest["answers"] is True
    assert not (folder / ".env").exists()
    assert "控えを取りました" in out
    assert str(folder.resolve()) in out
    assert ".env" in out


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_backup_to_other_folder_and_without_media(site, settings, tmp_path):
    shutil.rmtree(settings.MEDIA_ROOT)
    elsewhere = tmp_path / "elsewhere"

    out = run("seizou_backup", "--to", str(elsewhere))

    made = folders(elsewhere)
    assert len(made) == 1
    assert not (site / "backups").exists()
    assert not (made[0] / "media").exists()
    manifest = json.loads((made[0] / "backup.json").read_text(encoding="utf-8"))
    assert manifest["media_files"] == 0
    assert "0 個" in out


@pytest.mark.django_db
def test_backup_postgres_guides_when_pg_dump_is_missing(site, monkeypatch):
    monkeypatch.setattr(backup, "database_kind", lambda: "postgresql")
    monkeypatch.setattr(backup, "database_settings", lambda: {"NAME": "sample_db"})
    monkeypatch.setattr(backup.shutil, "which", lambda name: None)

    with pytest.raises(CommandError) as caught:
        run("seizou_backup")

    message = str(caught.value)
    assert "pg_dump" in message
    assert "sample_db" in message
    assert folders(site / "backups") == []


def fake_pg_dump(calls, returncode=0, stderr=""):
    def fake_run(args, **kwargs):
        calls["args"] = list(args)
        calls["env"] = kwargs.get("env") or {}
        if returncode == 0:
            Path(args[args.index("--file") + 1]).write_bytes(b"x")
        return subprocess.CompletedProcess(args, returncode, stdout="", stderr=stderr)

    return fake_run


def pg_settings():
    return {"NAME": "sample_db", "USER": "u", "PASSWORD": FAKE_DB_PASSWORD, "HOST": "localhost", "PORT": "5432"}


@pytest.mark.django_db
def test_backup_postgres_calls_pg_dump_without_printing_password(site, monkeypatch):
    calls = {}
    monkeypatch.setattr(backup, "database_kind", lambda: "postgresql")
    monkeypatch.setattr(backup, "database_settings", pg_settings)
    monkeypatch.setattr(backup.shutil, "which", lambda name: "/usr/bin/pg_dump")
    monkeypatch.setattr(backup.subprocess, "run", fake_pg_dump(calls))

    out = run("seizou_backup")

    assert calls["args"][0] == "/usr/bin/pg_dump"
    assert "--format=custom" in calls["args"]
    assert "sample_db" in calls["args"]
    password_in_env = calls["env"].get("PGPASSWORD") == FAKE_DB_PASSWORD
    assert password_in_env, "pg_dump の env に PGPASSWORD が渡っていない"
    assert calls["env"].get("PGHOST") == "localhost"
    made = folders(site / "backups")
    assert len(made) == 1
    assert (made[0] / "db.pgdump").is_file()
    manifest = json.loads((made[0] / "backup.json").read_text(encoding="utf-8"))
    assert manifest["database"] == "postgresql"
    assert "PostgreSQL" in out
    leaked = FAKE_DB_PASSWORD in out
    assert not leaked, "出力に DB のパスワードが出た"


@pytest.mark.django_db
def test_backup_postgres_failure_leaves_nothing(site, monkeypatch):
    calls = {}
    monkeypatch.setattr(backup, "database_kind", lambda: "postgresql")
    monkeypatch.setattr(backup, "database_settings", pg_settings)
    monkeypatch.setattr(backup.shutil, "which", lambda name: "/usr/bin/pg_dump")
    monkeypatch.setattr(backup.subprocess, "run", fake_pg_dump(calls, returncode=1, stderr="pg_dump: error: fake"))

    with pytest.raises(CommandError) as caught:
        run("seizou_backup")

    message = str(caught.value)
    assert "pg_dump が失敗" in message
    leaked = FAKE_DB_PASSWORD in message
    assert not leaked, "失敗の文に DB のパスワードが出た"
    assert folders(site / "backups") == []


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_restore_puts_back_db_and_media_and_keeps_retreat(site, settings):
    make_user("before")
    run("seizou_backup")
    name = plain_backups(site / "backups")[0].name
    make_user("after")
    media = Path(settings.MEDIA_ROOT)
    (media / "drawings" / "a.png").unlink()
    (media / "drawings" / "b.png").write_bytes(b"new")

    out = run("seizou_restore", name, "--yes")

    assert not user_exists("after")
    assert user_exists("before")
    assert (media / "drawings" / "a.png").is_file()
    assert not (media / "drawings" / "b.png").exists()
    kept = retreats(site / "backups")
    assert len(kept) == 1
    assert "after" in users(kept[0] / "db.sqlite3")
    assert (kept[0] / "media" / "drawings" / "b.png").is_file()
    assert "戻しました" in out
    assert "before-restore" in out
    assert "migrate" in out


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_restore_asks_before_doing_anything(site, monkeypatch):
    run("seizou_backup")
    name = plain_backups(site / "backups")[0].name
    make_user("after")

    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    out = run("seizou_restore", name)
    assert "やめました" in out
    assert user_exists("after")
    assert retreats(site / "backups") == []

    def no_terminal(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_terminal)
    out = run("seizou_restore", name)
    assert "やめました" in out
    assert user_exists("after")
    assert retreats(site / "backups") == []

    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    out = run("seizou_restore", name)
    assert "戻しました" in out
    assert not user_exists("after")
    assert len(retreats(site / "backups")) == 1


@pytest.mark.django_db
def test_restore_refuses_bad_folder_wrong_database_and_company(site, settings):
    parent = site / "backups"
    picture = Path(settings.MEDIA_ROOT) / "drawings" / "a.png"
    other_kind = "postgresql" if connection.vendor == "sqlite" else "sqlite"
    cases = [
        ("20261101-010101", {}, "見つかりません"),
        ("20261102-020202", {"format": 2}, "形が違います"),
        ("20261103-030303", {"database": other_kind}, "合いません"),
        ("20261104-040404", {"company_id": "other_co"}, "別の会社"),
    ]
    for index, (name, changes, expected) in enumerate(cases):
        if index:
            hand_made_backup(parent, name, **changes)
        with pytest.raises(CommandError) as caught:
            run("seizou_restore", name, "--yes")
        assert expected in str(caught.value), name
        assert picture.is_file()
    assert retreats(parent) == []


@sqlite_only
@pytest.mark.django_db
def test_restore_refuses_inside_transaction(site, settings):
    parent = site / "backups"
    hand_made_backup(parent, "20261105-050505")

    with pytest.raises(CommandError) as caught:
        run("seizou_restore", "20261105-050505", "--yes")

    assert "transaction の中では戻せません" in str(caught.value)
    assert (Path(settings.MEDIA_ROOT) / "drawings" / "a.png").is_file()
    assert retreats(parent) == []


@pytest.mark.django_db
def test_restore_list_shows_backups_in_order(site):
    parent = site / "backups"
    out = run("seizou_restore", "--list")
    assert "控えはまだありません" in out

    hand_made_backup(parent, "20261102-020202", created_at="2026-11-02T02:02:02")
    hand_made_backup(parent, "20261101-010101", created_at="2026-11-01T01:01:01")
    hand_made_backup(parent, "notes", created_at="2026-11-03T03:03:03")

    out = run("seizou_restore", "--list")

    assert out.index("20261101-010101") < out.index("20261102-020202")
    assert "notes" not in out
    assert "2026-11-01T01:01:01" in out
    assert connection.vendor in out
    assert "MB" in out


@pytest.mark.django_db
def test_restore_on_postgres_only_guides(site, settings, monkeypatch):
    monkeypatch.setattr(backup, "database_kind", lambda: "postgresql")
    monkeypatch.setattr(backup, "database_settings", lambda: {"NAME": "sample_db"})
    parent = site / "backups"
    hand_made_backup(parent, "20261106-060606", database="postgresql", db_file="db.pgdump")

    with pytest.raises(CommandError) as caught:
        run("seizou_restore", "20261106-060606", "--yes")

    message = str(caught.value)
    assert "pg_restore" in message
    assert "media" in message
    assert "sample_db" in message
    assert (Path(settings.MEDIA_ROOT) / "drawings" / "a.png").is_file()
    assert retreats(parent) == []


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_commands_never_print_secret_values(site, settings, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", FAKE_SECRET_KEY)
    monkeypatch.setenv("SEIZOU_PARTNER_TESTCO_SECRET", FAKE_PARTNER_SECRET)
    monkeypatch.setenv("PGPASSWORD", FAKE_DB_PASSWORD)
    settings.SECRET_KEY = FAKE_SECRET_KEY
    (site / ".env").write_text(f"SECRET_KEY={FAKE_SECRET_KEY}\n", encoding="utf-8")

    outputs = [run("seizou_backup"), run("seizou_restore", "--list")]
    name = plain_backups(site / "backups")[0].name
    outputs.append(run("seizou_restore", name, "--yes"))

    text = "\n".join(outputs)
    for value in (FAKE_SECRET_KEY, FAKE_PARTNER_SECRET, FAKE_DB_PASSWORD):
        leaked = value in text
        assert not leaked, "出力に鍵の値が出た"
    copied_env = [path for path in (site / "backups").rglob(".env")]
    assert copied_env == []
