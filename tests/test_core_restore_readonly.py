"""読むだけの場所に置いた控えから戻す試験。

訳: 2026-11-14 に、作者が Linux の VPS の置き場で、root の物で置き場の持ち主には読むだけの場所 (置き場の backups/ の外) に置いた控えを
seizou_restore に道で渡すと、「控えの DB を写せませんでした (attempt to write a readonly database)」で戻せなかった。
WAL の形の DB の写しは、開くと隣に -shm と -wal を作るので、書けないフォルダでは開けない。WAL で動く置き場で取った控えは、この形になる。
試験の DB の控えは WAL の形にならないので、控えの写しを WAL の形に直してから試す (DB の頭の 18・19 バイト目が 2)。
読むだけにするのは、控えのフォルダと、その親と、DB の写しだけ (上げたファイルの写しの権限は変えない)。
Windows はフォルダを読むだけにする形が違うので、ここの試験は POSIX だけで回す (Windows は未確認)。root で回すと読むだけの印が働かないので飛ばす。
"""

import os
import shutil
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path

import pytest

from seizou.core import backup
from tests.test_core_backup import (  # noqa: F401 - fixture を借りる
    make_user,
    plain_backups,
    retreats,
    run,
    site,
    sqlite_only,
    user_exists,
)
from tests.test_core_restore_verify import files, run_code

posix_not_root = pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="フォルダを読むだけにして確かめる試験 (Windows は形が違い、root には読むだけの印が働かない)",
)


def to_wal(db_file: Path) -> None:
    """DB の写しを WAL の形に直す (WAL で動く置き場で取った控えと同じ形)。隣に -wal と -shm を残さない。"""
    con = sqlite3.connect(str(db_file))
    try:
        assert con.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    finally:
        con.close()
    assert db_file.read_bytes()[18:20] == b"\x02\x02"
    assert sorted(path.name for path in db_file.parent.glob(db_file.name + "-*")) == []


@contextmanager
def read_only(*paths: Path):
    """渡した道から書く権限を外し、終わりに戻す (試験の後片づけが消せるように)。"""
    saved = [(path, stat.S_IMODE(path.stat().st_mode)) for path in paths]
    try:
        for path, mode in saved:
            os.chmod(path, mode & ~0o222)
        yield
    finally:
        for path, mode in reversed(saved):
            os.chmod(path, mode)


def seed_outside(site_dir: Path, tmp_path: Path) -> Path:
    """置き場の backups/ の外に、WAL の形の控えを 1 つ置いて、そのフォルダを返す (<一時>/seed/sample_co/<日時>)。"""
    run("seizou_backup")
    taken = plain_backups(site_dir / "backups")[0]
    parent = tmp_path / "seed" / "sample_co"
    parent.mkdir(parents=True)
    seed = parent / taken.name
    shutil.copytree(taken, seed)
    to_wal(seed / backup.DB_FILES["sqlite"])
    return seed


@posix_not_root
@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_restore_from_read_only_place_outside_backups(site, tmp_path):  # noqa: F811
    make_user("before")
    seed = seed_outside(site, tmp_path)
    make_user("after")
    before = files(seed)
    names = sorted(path.name for path in seed.iterdir())

    with read_only(seed.parent, seed, seed / backup.DB_FILES["sqlite"]):
        code, out = run_code(str(seed), "--yes")

    assert code == 0, out
    assert "戻しました" in out
    assert user_exists("before")
    assert not user_exists("after")
    # 控えの中身は変わらない (-wal と -shm も増えない)
    assert files(seed) == before
    assert sorted(path.name for path in seed.iterdir()) == names
    # 戻す前の退避は、置き場の backups/ にできる (控えの隣ではない)
    assert len(retreats(site / "backups")) == 1
    assert retreats(seed.parent) == []


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_restore_does_not_open_the_backup_db_in_place(site, tmp_path, monkeypatch):  # noqa: F811
    """書ける場所の控えでも、戻しは控えのフォルダの中で DB を開かない (開くのは一時のフォルダの写しだけ)。"""
    make_user("before")
    seed = seed_outside(site, tmp_path)
    make_user("after")
    db_file = seed / backup.DB_FILES["sqlite"]
    opened = []
    real_connect = sqlite3.connect

    def spy(database, *args, **kwargs):
        opened.append(str(database))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(backup.sqlite3, "connect", spy)
    code, out = run_code(str(seed), "--yes")

    assert code == 0, out
    assert user_exists("before")
    assert not user_exists("after")
    in_place = [text for text in opened if str(db_file) in text or db_file.as_uri() in text]
    assert in_place == [], in_place


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_restore_says_nothing_changed_when_the_copy_cannot_be_made(site, tmp_path, monkeypatch):  # noqa: F811
    """戻す時の DB の写しが作れなければ、DB と上げたファイルを変えずに止まり、戻す前の退避の在りかを言う。"""
    make_user("before")
    seed = seed_outside(site, tmp_path)
    make_user("after")
    db_file = seed / backup.DB_FILES["sqlite"]
    media_before = files(site / "media")
    calls = {"n": 0}
    real_copyfile = shutil.copyfile

    def flaky(src, dst, *args, **kwargs):
        if Path(src) == db_file:
            calls["n"] += 1
            if calls["n"] == 2:  # 1 回目は戻す前の確かめ (quick_check) の写し。2 回目が戻す時の写し
                raise OSError(28, "No space left on device")
        return real_copyfile(src, dst, *args, **kwargs)

    monkeypatch.setattr(backup.shutil, "copyfile", flaky)
    code, out = run_code(str(seed), "--yes")

    assert code != 0, out
    assert calls["n"] == 2
    assert "データベースと上げたファイルはまだ変えていません" in out
    retreat = retreats(site / "backups")
    assert len(retreat) == 1
    assert str(retreat[0]) in out
    assert user_exists("after")
    assert files(site / "media") == media_before
