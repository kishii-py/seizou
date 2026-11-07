"""命令の控えの確かめと作り込みの写しと世代、点検の「最後の控え」「ディスクの空き」と、点検の直すのメール (依頼書 N358)。

置き場は一時のフォルダに作り (BASE_DIR と MEDIA_ROOT を替える)、作り物のデータだけを使う。
控えの試験は tests/test_core_backup.py の fixture と helper を借り、SQLite の backup API を本物で回す (PostgreSQL の組では skip の印)。
置き場を作った時刻の代わり (django_migrations の一番早い applied) とディスクの空き (shutil.disk_usage) は差し替える
(試験の機械の時計とディスクに頼らない)。メールは pytest-django の locmem のメール箱で読む。
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta

import pytest
from django.core.management.base import CommandError

from seizou.core import backup
from tests import test_core_backup
from tests.test_core_backup import folders, hand_made_backup, run, sqlite_only

# 控えの試験の置き場 (BASE_DIR = 一時のフォルダの site/。media/drawings/a.png と answers.yaml がある)
site = test_core_backup.site

#: 写した DB の確かめが通らないときの、作り物の integrity_check の行
BROKEN_ROWS = ["*** in database main ***", "Page 3: btreeInitPage() returns error code 11"]


def put_site_files(site):
    """置き場に会社の作り込み (作り物) と .env と __pycache__ を置く。"""
    (site / "fields").mkdir()
    (site / "fields" / "models.py").write_text("# 作り物の会社の項目\n", encoding="utf-8")
    (site / "fields" / "__pycache__").mkdir()
    (site / "fields" / "__pycache__" / "models.cpython-312.pyc").write_bytes(b"\x00")
    (site / "templates").mkdir()
    (site / "templates" / "x.html").write_text("<p>作り物の帳票</p>\n", encoding="utf-8")
    (site / "settings.py").write_text("# 作り物の settings\n", encoding="utf-8")
    (site / ".env").write_text("SECRET_KEY=fake-env-value-for-tests-only\n", encoding="utf-8")


def manifest_of(folder) -> dict:
    return json.loads((folder / backup.MANIFEST).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 1. 命令の道の確かめと作り込みの写し


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_command_backup_copies_site_and_records_hashes(site):
    """(a) 命令の控えは作り込みを site/ に写し (.env・media・__pycache__ は写さない)、backup.json に sha256 と migration を足す。"""
    put_site_files(site)

    run("seizou_backup")

    made = folders(site / "backups")
    assert len(made) == 1
    folder = made[0]
    saved = folder / backup.SITE_DIR
    assert (saved / "fields" / "models.py").is_file()
    assert (saved / "templates" / "x.html").is_file()
    assert (saved / "settings.py").is_file()
    assert not (saved / ".env").exists()
    assert not (saved / "media").exists()
    assert not (saved / "fields" / "__pycache__").exists()
    assert not (saved / "backups").exists()
    manifest = manifest_of(folder)
    assert manifest["format"] == backup.FORMAT == 1
    db_hash = hashlib.sha256((folder / "db.sqlite3").read_bytes()).hexdigest()
    assert manifest["sha256"]["db.sqlite3"] == db_hash
    assert "site/fields/models.py" in manifest["sha256"]
    assert "media/drawings/a.png" in manifest["sha256"]
    assert "answers.yaml" in manifest["sha256"]
    assert backup.MANIFEST not in manifest["sha256"]
    assert not any(".env" in name for name in manifest["sha256"])
    assert "core" in manifest["migrations"]
    assert manifest["site_files"] == 3


@sqlite_only
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("broken", ["rows", "error"])
def test_command_backup_stops_when_integrity_check_fails(site, monkeypatch, broken):
    """(b) 写した DB の確かめが通らない (ok でない行・sqlite3 の誤り) と、命令は止まり、作りかけのフォルダを残さない。"""

    def fake_integrity(path):
        if broken == "error":
            raise sqlite3.DatabaseError("file is not a database")
        return BROKEN_ROWS

    monkeypatch.setattr(backup, "sqlite_integrity", fake_integrity)

    with pytest.raises(CommandError) as caught:
        run("seizou_backup")

    assert "integrity_check" in str(caught.value)
    assert "控えは残していません" in str(caught.value)
    assert folders(site / "backups") == []


@sqlite_only
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("suffix", [backup.RETREAT_SUFFIX, ""])
def test_retreat_and_practice_copies_stay_as_before(site, monkeypatch, suffix):
    """(c) 戻す前の退避と練習モードの写し (full を渡さない) は、作り込みを写さず、sha256 の鍵を持たず、確かめもしない。"""
    put_site_files(site)

    def must_not_run(path):
        raise AssertionError("full を渡さない控えで integrity_check を呼んだ")

    monkeypatch.setattr(backup, "sqlite_integrity", must_not_run)
    parent = site / "backups"

    result = backup.take_backup(parent, suffix=suffix)

    folder = result["folder"]
    assert not (folder / backup.SITE_DIR).exists()
    manifest = manifest_of(folder)
    assert "sha256" not in manifest
    assert "migrations" not in manifest
    assert "site_files" not in manifest


# ---------------------------------------------------------------- 2. 世代を保つ (--keep)


def backup_name(days_ago: float, suffix: str = "") -> str:
    """名前の日時が days_ago 日前の控えの名前 (置き場の時計の現地時刻)。"""
    return (datetime.now() - timedelta(days=days_ago)).strftime(backup.NAME_TIME_FORMAT) + suffix


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_keep_removes_only_old_backups_after_new_one(site):
    """(d) --keep 7 は、名前の日時が 7 日より古い控えだけを消す (1 日前・退避・今の控え・控えでない物は残す)。"""
    parent = site / "backups"
    old = hand_made_backup(parent, backup_name(8))
    recent = hand_made_backup(parent, backup_name(1))
    old_retreat = hand_made_backup(parent, backup_name(8, backup.RETREAT_SUFFIX))
    not_backup = parent / backup_name(9)
    not_backup.mkdir()
    other = parent / "notes"
    other.mkdir()

    out = run("seizou_backup", "--keep", "7")

    assert not old.exists()
    assert recent.is_dir()
    assert old_retreat.is_dir()
    assert not_backup.is_dir()
    assert other.is_dir()
    assert f"古い控えを消しました: {old.name}" in out
    assert out.count("古い控えを消しました") == 1
    new = [path for path in folders(parent) if path not in (recent, old_retreat, not_backup, other)]
    assert len(new) == 1
    assert (new[0] / backup.SITE_DIR).is_dir()


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_keep_removes_nothing_when_new_backup_fails(site, monkeypatch):
    """(e) 新しい控えの確かめが通らなければ、--keep でも古い控えを消さない。"""
    parent = site / "backups"
    old = hand_made_backup(parent, backup_name(8))
    monkeypatch.setattr(backup, "sqlite_integrity", lambda path: BROKEN_ROWS)

    with pytest.raises(CommandError):
        run("seizou_backup", "--keep", "7")

    assert old.is_dir()
    assert folders(parent) == [old]


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["0", "-1", "x"])
def test_keep_needs_positive_days(site, value):
    with pytest.raises(CommandError) as caught:
        run("seizou_backup", "--keep", value)

    assert "1 以上" in str(caught.value)
    assert folders(site / "backups") == []


def test_newest_backup_skips_retreat(tmp_path):
    """(f) newest_backup は退避 (-before-restore) を飛ばし、控えが無ければ None。"""
    parent = tmp_path / "backups"
    assert backup.newest_backup(parent) is None

    hand_made_backup(parent, backup_name(0, backup.RETREAT_SUFFIX))
    assert backup.newest_backup(parent) is None

    hand_made_backup(parent, backup_name(3))
    second = hand_made_backup(parent, backup_name(2))
    hand_made_backup(parent, backup_name(1, backup.RETREAT_SUFFIX))

    newest = backup.newest_backup(parent)
    assert newest is not None
    assert newest["name"] == second.name
    assert newest["size"] >= 0


def test_prune_keeps_names_passed_in_keep(tmp_path):
    parent = tmp_path / "backups"
    old = hand_made_backup(parent, backup_name(10))
    older = hand_made_backup(parent, backup_name(20))

    removed = backup.prune_backups(parent, 7, keep=(old.name,))

    assert removed == [older.name]
    assert old.is_dir()
    assert not older.exists()
    assert backup.prune_backups(tmp_path / "missing", 7) == []
