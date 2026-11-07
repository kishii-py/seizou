"""控えから戻せるかの確かめ (seizou_restore --verify・seizou.core.backup.verify_backup) の試験 (依頼書 N355)。

置き場 (BASE_DIR と MEDIA_ROOT) は tests/test_core_backup.py の site で一時のフォルダに作り、作り物のデータだけを使う。
控えを壊す試験は、控えの DB の写しの中ほどの page を別の bytes で上書きする (先頭の page は残す)。
確かめの記録 (控えの置き場の restore-checks.jsonl) は、行の数と成否を見る。
"""

import io
import json
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from seizou.core import backup
from tests.test_core_backup import (  # noqa: F401 - fixture を借りる
    hand_made_backup,
    make_user,
    plain_backups,
    retreats,
    run,
    site,
    sqlite_only,
    user_exists,
)


def run_code(*args) -> tuple[int, str]:
    """seizou_restore を打ち、(終わりの番号, 出力と CommandError の文) を返す。"""
    out, err = io.StringIO(), io.StringIO()
    try:
        call_command("seizou_restore", *args, stdout=out, stderr=err)
        code = 0
    except CommandError as exc:
        err.write(str(exc))
        code = exc.returncode
    return code, out.getvalue() + err.getvalue()


def records(parent: Path) -> list[dict]:
    """控えの置き場の restore-checks.jsonl の行 (無ければ [])。"""
    path = parent / backup.RESTORE_CHECKS
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def files(root: Path) -> dict[str, bytes]:
    """フォルダの下のファイルの道と中身 (変わらない事を見る)。"""
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def break_db(db_file: Path) -> None:
    """DB の写しの中ほどの page (全体の 1/4 から 3/4) を別の bytes で上書きする。先頭の page (DB の頭) は残す。"""
    data = bytearray(db_file.read_bytes())
    page = int.from_bytes(data[16:18], "big")
    page = 65536 if page == 1 else page
    count = len(data) // page
    for index in range(max(1, count // 4), max(2, count * 3 // 4)):
        data[index * page : (index + 1) * page] = b"\xab" * page
    db_file.write_bytes(bytes(data))


def user_count() -> int:
    return get_user_model().objects.count()


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_verify_checks_backup_without_touching_live_data(site, settings):  # noqa: F811
    make_user("before")
    run("seizou_backup")
    parent = site / "backups"
    folder = plain_backups(parent)[0]
    make_user("after")
    users_before = user_count()
    media = Path(settings.MEDIA_ROOT)
    media_before = files(media)
    backup_before = files(folder)

    code, out = run_code(folder.name, "--verify")

    assert code == 0, out
    assert user_exists("after")
    assert user_count() == users_before
    assert files(media) == media_before
    assert files(folder) == backup_before
    assert retreats(parent) == []
    assert "開けます (quick_check: ok)" in out
    assert "migration: 控えと今で同じ" in out
    assert "上げたファイル: 1 個 (backup.json と同じ)" in out
    assert "確かめました" in out
    rows = records(parent)
    assert len(rows) == 1
    assert set(rows[0]) == {"checked_at", "backup", "seconds", "ok"}
    assert rows[0]["backup"] == folder.name
    assert rows[0]["ok"] is True

    code, out = run_code(folder.name, "--verify", "--yes")

    assert code == 0, out
    assert user_exists("after")
    assert retreats(parent) == []
    assert "戻しました" not in out
    assert len(records(parent)) == 2
    assert [row["name"] for row in backup.list_backups(parent)] == [folder.name]


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_verify_fails_on_broken_db_and_records_it(site, settings):  # noqa: F811
    run("seizou_backup")
    parent = site / "backups"
    folder = plain_backups(parent)[0]
    break_db(folder / "db.sqlite3")
    make_user("after")
    users_before = user_count()
    media_before = files(Path(settings.MEDIA_ROOT))

    code, out = run_code(folder.name, "--verify")

    assert code == 1
    assert "壊れています" in out
    assert "控えから戻せない所があります" in out
    assert user_exists("after")
    assert user_count() == users_before
    assert files(Path(settings.MEDIA_ROOT)) == media_before
    assert retreats(parent) == []
    rows = records(parent)
    assert len(rows) == 1
    assert rows[0]["ok"] is False


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_verify_shows_media_count_difference(site):  # noqa: F811
    run("seizou_backup")
    parent = site / "backups"
    folder = plain_backups(parent)[0]
    (folder / "media" / "drawings" / "a.png").unlink()

    code, out = run_code(folder.name, "--verify")

    assert code == 1
    assert "上げたファイルの数が違います: 控えの media/ に 0 個、backup.json は 1 個" in out
    assert records(parent)[-1]["ok"] is False


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_verify_says_backup_has_no_sha256(site):  # noqa: F811
    run("seizou_backup")
    folder = plain_backups(site / "backups")[0]
    manifest = json.loads((folder / backup.MANIFEST).read_text(encoding="utf-8"))
    assert "sha256" not in manifest

    code, out = run_code(folder.name, "--verify")

    assert code == 0, out
    assert "sha256 無し (中身の印を比べていない)" in out


@pytest.mark.django_db
def test_verify_refuses_postgres_and_records_nothing(site, monkeypatch):  # noqa: F811
    monkeypatch.setattr(backup, "database_kind", lambda: "postgresql")
    parent = site / "backups"
    hand_made_backup(parent, "20261106-060606", database="postgresql", db_file="db.pgdump")

    code, out = run_code("20261106-060606", "--verify")

    assert code == 1
    assert "この命令では確かめられません" in out
    assert records(parent) == []


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_restore_refuses_broken_backup_and_changes_nothing(site, settings):  # noqa: F811
    run("seizou_backup")
    parent = site / "backups"
    folder = plain_backups(parent)[0]
    break_db(folder / "db.sqlite3")
    make_user("after")
    users_before = user_count()
    media = Path(settings.MEDIA_ROOT)
    (media / "drawings" / "b.png").write_bytes(b"new")
    media_before = files(media)

    code, out = run_code(folder.name, "--yes")

    assert code == 1
    assert "壊れています" in out
    assert "何も変えていません" in out
    assert retreats(parent) == []
    assert user_exists("after")
    assert user_count() == users_before
    assert files(media) == media_before


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_restore_still_puts_back_a_sound_backup(site, settings):  # noqa: F811
    make_user("before")
    run("seizou_backup")
    parent = site / "backups"
    folder = plain_backups(parent)[0]
    make_user("after")

    code, out = run_code(folder.name, "--verify")
    assert code == 0, out
    code, out = run_code(folder.name, "--yes")

    assert code == 0, out
    assert "戻しました" in out
    assert user_exists("before")
    assert not user_exists("after")
    assert len(retreats(parent)) == 1
