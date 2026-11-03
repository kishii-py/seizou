"""控えと戻しと点検の、MEDIA_ROOT が置き場そのものかその親を指すときの断り (N90)。

判定 (seizou.core.backup.media_root_problem) は道を resolve してから比べる。置き場の中と外は断らない。
控え (seizou_backup) と戻し (seizou_restore) は、断るときに何も書かず何も消さない事を見る。
値を含む字の比べは、失敗の文に値が出ないよう真偽にしてから assert する。
"""

import io
import os
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from seizou.core import backup
from tests.test_core_backup import hand_made_backup, retreats

ENV_VALUE = "fake-env-value-that-must-not-appear"


@pytest.fixture
def site(settings, tmp_path):
    site = tmp_path / "site"
    (site / "media").mkdir(parents=True)
    css = site / "staticfiles" / "css"
    css.mkdir(parents=True)
    (css / "app.css").write_text("/* 試験の偽の CSS */\n", encoding="utf-8")
    settings.BASE_DIR = site
    settings.MEDIA_ROOT = site / "media"
    settings.STATIC_ROOT = site / "staticfiles"
    settings.SECRET_KEY = "fake-" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_aBcDeFgHiJkLmNoPqRsTu"
    settings.SEIZOU_DEV_AUTO_LOGIN = False
    settings.SEIZOU_FILE_DELIVERY = "django"
    settings.ALLOWED_HOSTS = ["testserver", "example.test"]
    settings.SESSION_COOKIE_SECURE = True
    settings.CSRF_COOKIE_SECURE = True
    settings.SEIZOU = {"id": "sample_co", "port": 8010}
    env = site / ".env"
    if os.name == "nt":
        env.write_text(f"SECRET_KEY={ENV_VALUE}\n", encoding="utf-8")
    else:
        fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"SECRET_KEY={ENV_VALUE}\n")
    return site


def tree(folder: Path) -> list[str]:
    """フォルダの下の道の一覧 (置き場の中が増えず減らない事を見る)。"""
    return sorted(str(path.relative_to(folder)) for path in folder.rglob("*"))


def command_error(name, *args) -> str:
    """命令を打ち、CommandError の文を返す (CommandError が出なければ試験を落とす)。"""
    with pytest.raises(CommandError) as caught:
        call_command(name, *args, stdout=io.StringIO(), stderr=io.StringIO())
    return str(caught.value)


def test_problem_when_media_root_is_the_site(site):
    problem = backup.media_root_problem(site, site)

    assert problem is not None
    names_it = "MEDIA_ROOT" in problem and "置き場" in problem
    assert names_it
    assert backup.media_root_problem(site, site / "media" / "..") is not None


def test_problem_when_media_root_is_a_parent(site):
    for parent in (site.parent, Path(site.anchor), site.parent.parent):
        refused = backup.media_root_problem(site, parent) is not None
        assert refused, "置き場の親を断らなかった"


def test_no_problem_inside_the_site(site):
    assert backup.media_root_problem(site, site / "media") is None
    assert backup.media_root_problem(site, site / "a" / "b") is None


def test_no_problem_outside_the_site(site):
    assert backup.media_root_problem(site, site.parent / "elsewhere-media") is None
    assert backup.media_root_problem(site, site.parent / "site-media") is None
    assert backup.media_root_problem(None, site) is None
    assert backup.media_root_problem(site, "") is None
    assert backup.media_root_problem(site, None) is None


@pytest.mark.django_db
def test_backup_refuses_and_writes_nothing(site, settings, tmp_path):
    settings.MEDIA_ROOT = site
    elsewhere = tmp_path / "elsewhere"
    before = tree(site)

    message = command_error("seizou_backup", "--to", str(elsewhere))

    says_why = "MEDIA_ROOT" in message and "何も書かず" in message
    assert says_why
    assert not elsewhere.exists()

    message += command_error("seizou_backup")

    assert not (site / "backups").exists()
    assert tree(site) == before
    env_value_leaked = ENV_VALUE in message
    assert not env_value_leaked, "断りの文に .env の中身が出た"


@pytest.mark.django_db
def test_restore_refuses_and_deletes_nothing(site, settings, tmp_path):
    settings.MEDIA_ROOT = site
    (site / "keep.txt").write_text("消えてはいけない字\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    saved = hand_made_backup(elsewhere, "20261104-010000")
    before = tree(site)

    message = command_error("seizou_restore", str(saved), "--yes")

    says_why = "MEDIA_ROOT" in message
    assert says_why
    assert (site / "keep.txt").is_file()
    assert (site / ".env").is_file()
    assert (site / "media").is_dir()
    assert tree(site) == before
    assert retreats(site / "backups") == []
    assert retreats(elsewhere) == []
