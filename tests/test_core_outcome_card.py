"""使う人の結果の数字 (N83): 1 行の転記の分の設定と、ダッシュボードの「手で打たずに受けた明細」のカード。

見る所: 設定 transcription_minutes_per_line (既定 1・画面から変えない・点検が良い)。
名前は作り物だけ。
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core import conf, doctor
from seizou.core import services as core_services


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


def test_minutes_setting_is_locked_and_doctor_is_good(client, user, settings):
    settings.SEIZOU = {}

    assert conf.setting("transcription_minutes_per_line") == 1
    assert "transcription_minutes_per_line" in conf.LOCKED_KEYS
    rows = doctor.check_settings()
    assert len(rows) == 1 and rows[0].ok, rows
    assert rows[0].label == "会社の選べる設定"
    with pytest.raises(ValueError):
        core_services.validate_setting("transcription_minutes_per_line", 2)

    client.force_login(user)
    body = client.get(reverse("core:settings")).content.decode()
    assert "1 行の転記にかかる分" in body
