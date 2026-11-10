"""点検の「settings.py の塊」と「権限の判定」(seizou.core.doctor の check_settings_blocks・check_access_control)。

置き場を一時のフォルダに作る (tests/test_core_doctor.py の fixture site を借りる。site は権限の判定を 3 つとも on にし、
LOGGING と HtmxLoginMiddleware の行を置く = 本番の条件で直す物の無い形)。名前と値は作り物。
"""

import pytest

from seizou.core import doctor, services
from seizou.core.logging_conf import default_logging
from tests import test_core_doctor, test_core_settings_edit
from tests.test_core_doctor import fix_lines, good_lines, run_doctor

# fixture を借りる (pytest は module の名前で fixture を見つける)。manager = システム管理者
site = test_core_doctor.site
manager = test_core_settings_edit.manager

HTMX = "seizou.core.middleware.HtmxLoginMiddleware"
ALL_ON = {"department": "on", "write": "on", "money": "on"}
ALL_LOG = {"department": "log", "write": "log", "money": "log"}
BLOCKS = "settings.py の塊"
ACCESS = "権限の判定"


# ---------------------------------------------------------------- (3) settings.py の塊


def test_blocks_missing_logging_or_htmx_is_fix(site, settings):
    [row] = doctor.check_settings_blocks()
    assert row.ok and row.label == BLOCKS and row.fix == ""

    settings.LOGGING = {}
    [row] = doctor.check_settings_blocks()
    assert not row.ok
    assert "LOGGING" in row.detail and "LOGGING" in row.fix and "HtmxLoginMiddleware" not in row.fix
    assert "版を上げる手順" in row.fix

    settings.LOGGING = default_logging()
    settings.MIDDLEWARE = [name for name in settings.MIDDLEWARE if name != HTMX]
    [row] = doctor.check_settings_blocks()
    assert not row.ok
    assert "HtmxLoginMiddleware" in row.detail and "HtmxLoginMiddleware" in row.fix and "LOGGING" not in row.fix

    settings.LOGGING = {}
    [row] = doctor.check_settings_blocks()
    assert not row.ok and "LOGGING" in row.fix and "HtmxLoginMiddleware" in row.fix

    settings.LOGGING = default_logging()
    settings.MIDDLEWARE = [*settings.MIDDLEWARE, HTMX]
    [row] = doctor.check_settings_blocks()
    assert row.ok


@pytest.mark.django_db
def test_blocks_row_only_in_production(site):
    assert [row.label for row in doctor.run(production=True)].count(BLOCKS) == 1
    assert BLOCKS not in [row.label for row in doctor.run(production=False)]


# ---------------------------------------------------------------- (4) 権限の判定


def test_access_control_log_is_fix_in_production(site, settings):
    settings.SEIZOU = {"access_control": ALL_LOG}
    [row] = doctor.check_access_control(True)
    assert not row.ok and row.label == ACCESS
    assert "seizou.access" in row.fix and "on にする" in row.fix
    assert "記録だけ: 部署・編集の権限・金額の閲覧" in row.detail and "金額が見える" in row.detail

    # 立ち上げてすぐの既定 (3 つとも記録だけ) も同じ
    settings.SEIZOU = {}
    [row] = doctor.check_access_control(True)
    assert not row.ok and "記録だけ" in row.detail

    settings.SEIZOU = {"access_control": ALL_ON}
    [row] = doctor.check_access_control(True)
    assert row.ok and row.fix == "" and "有効: 部署・編集の権限・金額の閲覧" in row.detail

    # off は 1 人で使う会社が選んだ形として良い。注を付ける
    settings.SEIZOU = {"access_control": {**ALL_ON, "money": "off"}}
    [row] = doctor.check_access_control(True)
    assert row.ok and "使わない: 金額の閲覧" in row.detail and "全員が金額を見る" in row.detail

    settings.SEIZOU = {"access_control": {**ALL_ON, "write": "log"}}
    [row] = doctor.check_access_control(True)
    assert not row.ok and "記録だけ: 編集の権限" in row.detail and "金額が見える" not in row.detail


@pytest.mark.django_db
def test_access_control_reads_screen_override(site, settings, manager):
    settings.SEIZOU = {"access_control": ALL_LOG}
    services.set_setting("access_control", ALL_ON, manager)
    [row] = doctor.check_access_control(True)
    assert row.ok

    services.set_setting("access_control", {**ALL_ON, "money": "log"}, manager)
    [row] = doctor.check_access_control(True)
    assert not row.ok and "記録だけ: 金額の閲覧" in row.detail


def test_access_control_log_is_good_with_note_in_development(site, settings):
    settings.SEIZOU = {"access_control": ALL_LOG}
    [row] = doctor.check_access_control(False)
    assert row.ok and row.fix == ""
    assert "記録だけ" in row.detail and "本番の条件では直す" in row.detail


@pytest.mark.django_db
def test_doctor_exit_code_with_access_control(site, settings):
    code, out = run_doctor()
    assert code == 0, fix_lines(out)
    assert any(ACCESS in line for line in good_lines(out))
    assert any(BLOCKS in line for line in good_lines(out))

    # 記録だけで確かめている間は、本番の点検が「権限の判定」の直す 1 件で終わる (docs/rules.md の 1 句)
    settings.SEIZOU = {"access_control": ALL_LOG}
    code, out = run_doctor()
    assert code == 1
    fixes = fix_lines(out)
    assert len(fixes) == 1 and ACCESS in fixes[0], fixes
