"""戻し (seizou_restore) で、上げたファイルの入れ替えが途中で止まったときの試験 (依頼書 N394)。

置き場 (BASE_DIR と MEDIA_ROOT) は tests/test_core_backup.py の site で一時のフォルダに作り、作り物のデータだけを使う。
Windows でほかの道具が開いているファイルを消せない形は、backup.shutil.rmtree を偽に差し替えて作る。偽は MEDIA_ROOT のときだけ、
ファイルを 1 つ消してから PermissionError を上げる (半分消えた形)。ほかの道は本物の rmtree に渡す。
"""

import io
import shutil
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

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


@sqlite_only
@pytest.mark.django_db(transaction=True)
def test_restore_guides_when_media_cannot_be_replaced_and_rerun_puts_it_back(site, settings, monkeypatch):  # noqa: F811
    make_user("before")
    run("seizou_backup")
    parent = site / "backups"
    name = plain_backups(parent)[0].name
    make_user("after")
    media = Path(settings.MEDIA_ROOT)
    real_rmtree = shutil.rmtree

    def locked_rmtree(path, *args, **kwargs):
        if Path(path) == media:
            (media / "drawings" / "a.png").unlink()
            raise PermissionError(13, "作り物の開いたファイル")
        return real_rmtree(path, *args, **kwargs)

    # (d) 上げたファイルを消せない → traceback でなく、退避の在りかと打ち直しの案内で止まる
    with monkeypatch.context() as patch:
        patch.setattr(backup.shutil, "rmtree", locked_rmtree)
        code, out = run_code(name, "--yes")

    assert code == 1
    kept = retreats(parent)
    assert len(kept) == 1
    assert kept[0].name in out
    assert "もう 1 度" in out
    assert "作り物の開いたファイル" in out
    assert "データベースは戻しました" in out
    assert not user_exists("after")
    assert user_exists("before")
    assert not (media / "drawings" / "a.png").exists()

    # (e) 偽を外し、同じ控えでもう 1 度打つと控えのとおりに戻る。
    # 同じ秒に 2 回戻すと退避の名前 (<日時>-before-restore) が重なり take_backup が断るので、2 回目の名前を変える
    monkeypatch.setattr(backup, "new_name", lambda suffix="": "20991231-235959" + suffix)
    code, out = run_code(name, "--yes")

    assert code == 0
    assert "戻しました" in out
    assert (media / "drawings" / "a.png").is_file()
    assert not user_exists("after")
    assert user_exists("before")
    assert len(retreats(parent)) == 2
