"""点検の「settings.py の塊」(seizou.core.doctor の check_settings_blocks)。

置き場を一時のフォルダに作る (tests/test_core_doctor.py の fixture site を借りる。site は LOGGING と
HtmxLoginMiddleware の行を置く = 本番の条件で直す物の無い形)。名前と値は作り物。
"""

import pytest

from seizou.core import doctor
from seizou.core.logging_conf import default_logging
from tests import test_core_doctor

# fixture を借りる (pytest は module の名前で fixture を見つける)
site = test_core_doctor.site

HTMX = "seizou.core.middleware.HtmxLoginMiddleware"
BLOCKS = "settings.py の塊"


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
