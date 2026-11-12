"""点検の「上書きしたテンプレート」と「つながりの受け口の URL が自社を指すか」の行と、配り方と権限の判定の字 (seizou.core.doctor)。

見る物: 置き場の templates/ で部品のテンプレートを上書きした数と、部品の側のファイルが上書きより新しい数の良いの行 (上書きが無ければ行を出さない)・
nginx に渡す形の良いの字が、nginx の動きを見ていない事を言うか・権限の判定の直し方の字が画面の字「有効」と記録の見場所を言うか・
送る向きのつながりの受け口の URL が自社を指すときの直すの行 (指さなければ行を出さない)。
出所 = 精査 Q4 (2026-11-10) の番号 7・D3・D5、精査 Q3 の T1、本体の決め 11-11 14:2x (開発の記録 docs/nights/20261111/N420.md)。
置き場を一時のフォルダに作る (tests/test_core_doctor.py の fixture site を借りる。site は直す物の無い形)。部品の側のファイルは読むだけで、時刻も変えない。
名前と値は作り物。
"""

import os
from pathlib import Path

import pytest
from django.apps import apps

from seizou.core import doctor
from tests import test_core_doctor
from tests.test_core_doctor import fix_lines, make_send_connection, run_doctor

# fixture を借りる (pytest は module の名前で fixture を見つける)
site = test_core_doctor.site

OVERRIDES = "上書きしたテンプレート"
#: 上書きする部品のテンプレート (core の部品の templates/ の下に実在する道)
PART_TEMPLATE = "core/base.html"
ALL_LOG = {"department": "log", "write": "log", "money": "log"}
SELF_DETAIL = "自社の受け口を指している"


def _override_rows(production):
    return [row for row in doctor.run(production=production) if row.label == OVERRIDES]


def _part_file():
    """部品の側のテンプレートのファイル (読むだけ)。"""
    return Path(apps.get_app_config("core").path) / "templates" / PART_TEMPLATE


def _put_override(site, relative):
    path = site / "templates" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{# 試験の作り物の上書き #}\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- (a)(b)(c) 上書きしたテンプレート


@pytest.mark.django_db
def test_no_override_rows_when_templates_dir_is_empty(site):
    """(a) 置き場の templates/ が無いときも、空のときも、上書きの行を出さない (良いの数を変えない)。"""
    assert _override_rows(True) == []
    (site / "templates").mkdir()
    assert _override_rows(True) == []
    assert _override_rows(False) == []


@pytest.mark.django_db
def test_override_of_part_template_is_one_good_row(site):
    """(b) 部品と同じ相対の道のファイルは上書きと数え、部品に無い道のファイル (会社が足したテンプレート) は数えない。"""
    assert _part_file().is_file()
    _put_override(site, PART_TEMPLATE)
    _put_override(site, "sample_co/own_page.html")
    pycache = site / "templates" / "core" / "__pycache__"
    pycache.mkdir()
    (pycache / "base.html").write_text("作り物\n", encoding="utf-8")

    for production in (True, False):
        rows = _override_rows(production)
        assert len(rows) == 1, rows
        assert rows[0].ok and rows[0].fix == ""
        assert "上書き 1 件" in rows[0].detail
        assert "見比べる" in rows[0].detail


@pytest.mark.django_db
def test_override_counts_part_files_newer_than_override(site):
    """(c) 上書きが部品の側より古ければ新しい物 1 件と最初の 1 件の道、新しければ 0 件。どちらも直すにならない。"""
    part = _part_file()
    part_time = part.stat().st_mtime
    override = _put_override(site, PART_TEMPLATE)

    os.utime(override, (part_time - 3600, part_time - 3600))
    [row] = _override_rows(True)
    assert row.ok
    assert "新しい物 1 件" in row.detail
    assert f"最初の 1 件: templates/{PART_TEMPLATE}" in row.detail
    code, out = run_doctor()
    assert code == 0, fix_lines(out)

    os.utime(override, (part_time + 3600, part_time + 3600))
    [row] = _override_rows(True)
    assert row.ok
    assert "新しい物 0 件" in row.detail
    assert "最初の 1 件" not in row.detail
    code, out = run_doctor()
    assert code == 0, fix_lines(out)

    assert part.stat().st_mtime == part_time


# ---------------------------------------------------------------- (d) 上げたファイルの配り方


def test_nginx_delivery_good_says_nginx_is_not_checked(site, settings):
    """(d) nginx に渡す形の良いの字は、nginx が動いているかと断片を読み込んだかを見ていない事を言う。判定は変えない。"""
    settings.SEIZOU_FILE_DELIVERY = "nginx"
    deploy = site / "deploy"
    deploy.mkdir()
    media_root = (site / "media").resolve().as_posix()
    (deploy / "nginx-files.conf").write_text(
        f'location /seizou-files/ {{\n    internal;\n    alias "{media_root}/";\n}}\n',
        encoding="utf-8",
    )

    [row] = doctor.check_file_delivery()
    assert row.ok
    assert "nginx に渡す" in row.detail and "MEDIA_ROOT と同じ" in row.detail
    assert "見ていない" in row.detail

    settings.SEIZOU_FILE_DELIVERY = "django"
    [row] = doctor.check_file_delivery()
    assert row.ok and row.detail == "Django が送る"


# ---------------------------------------------------------------- (e) 権限の判定の直し方の字


def test_access_control_fix_says_enable_and_where_the_log_is(site, settings):
    """(e) 記録だけのときの本番の条件の直し方は、設定の画面の字「有効」と、記録の見場所 (runserver の窓と journalctl) を言う。"""
    settings.SEIZOU = {**settings.SEIZOU, "access_control": ALL_LOG}
    [row] = doctor.check_access_control(True)
    assert not row.ok
    assert "「有効」にする" in row.fix
    assert "journalctl" in row.fix and "runserver" in row.fix
    assert "seizou.access" in row.fix


# ---------------------------------------------------------------- (f) 受け口の URL が自社を指す


def _self_rows(production):
    return [row for row in doctor.run(production=production) if SELF_DETAIL in row.detail]


@pytest.mark.django_db
def test_send_url_pointing_to_self_is_fix(site, settings):
    """(f) 送る向きのつながりの受け口の URL が自社 (手元の番地と自社のポート) を指すと直すの行が 1 つ出て、指さないと行が出ない。"""
    make_send_connection()  # 受け口の URL は http://127.0.0.1:8002/connect/receive/order/

    settings.SEIZOU = {**settings.SEIZOU, "port": 8002}
    for production in (True, False):
        rows = _self_rows(production)
        assert len(rows) == 1, rows
        assert not rows[0].ok
        assert "相手に届かない" in rows[0].detail
        assert "相手の会社の受け口" in rows[0].fix
    # 鍵が無い・http の直すの行も出るので、終わりの番号でなく、命令の出力の直すの行に自社を指す行がある事を見る
    code, out = run_doctor()
    assert any(SELF_DETAIL in line for line in fix_lines(out))

    settings.SEIZOU = {**settings.SEIZOU, "port": 8001}
    assert _self_rows(True) == []
    # 開発の条件では受け口の URL の https の行も出ないので、受け口の URL の行は 1 つも出ない (良いの数を変えない)
    assert not [row for row in doctor.run(production=False) if "受け口の URL" in row.label]
