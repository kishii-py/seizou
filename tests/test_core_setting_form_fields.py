"""設定の変更の画面の字の欄 (初見テスト R5 の候補 C67)。

主色 (brand_color) と業種 (industry) は空で保存すると既定 (土台の色・左上の seizou) に戻るので、欄を「任意」にする。
form の必須と services の validate_setting が空を通す鍵が食い違わない事、空で保存して「答えの値に戻す」で戻る行き来、
形の合わない主色を今と同じく services が断る事を見る。model の名前の 3 つの欄は必須のまま。
会社の設定は settings の fixture で SEIZOU を替える。名前と値は作り物だけ。
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from seizou.core import conf, services
from seizou.core.forms import SettingForm
from seizou.core.models import SettingOverride
from tests import test_core_settings_edit
from tests.test_core_brand import html_tag, subtitle
from tests.test_core_settings_edit import clear_url, edit_url

# 設定の変更の試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
# manager = システム管理者。manager_client = その人でログインした client。
manager = test_core_settings_edit.manager
manager_client = test_core_settings_edit.manager_client

#: model の名前を字で持つ鍵 (空を services が断るので、欄は必須のまま)
MODEL_KEYS = ("assistant_model", "intake_model", "feedback_model")

#: 必須の欄があるときだけカードの頭に出る行 (core/_form_card.html の has_required)
REQUIRED_LINE = "「必須」の欄を埋めて"


# ---------------------------------------------------------------- form と services


@pytest.mark.parametrize("key", ["brand_color", "industry"])
def test_brand_color_and_industry_fields_are_optional(db, key):
    assert SettingForm(key=key).fields[key].required is False


@pytest.mark.parametrize("key", MODEL_KEYS)
def test_model_name_fields_stay_required(db, key):
    assert SettingForm(key=key).fields[key].required is True


def test_optional_keys_match_what_services_accept_empty():
    # form が空を通す鍵は、services も空を既定として通す (食い違うと空の保存が赤の知らせで止まる)
    assert SettingForm.OPTIONAL_KEYS == ("brand_color", "industry")
    for key in SettingForm.OPTIONAL_KEYS:
        assert services.validate_setting(key, "") == ""
    # model の名前は空を断るので、欄は必須のまま
    for key in MODEL_KEYS:
        with pytest.raises(ValueError):
            services.validate_setting(key, "")


# ---------------------------------------------------------------- 画面


@pytest.mark.parametrize(("key", "value"), [("brand_color", "#15803d"), ("industry", "作り物の業種")])
def test_edit_page_shows_optional_badge_without_required_line(manager_client, settings, key, value):
    settings.SEIZOU = {key: value}

    response = manager_client.get(edit_url(key))

    assert response.status_code == 200
    body = response.content.decode()
    assert ">任意</span>" in body
    assert ">必須</span>" not in body
    assert REQUIRED_LINE not in body


def test_empty_brand_color_saves_and_clear_goes_back_to_answer(manager_client, settings):
    settings.SEIZOU = {"brand_color": "#15803d"}

    response = manager_client.post(edit_url("brand_color"), {"brand_color": ""})

    assert response.status_code == 302 and response["Location"] == reverse("core:settings")
    assert SettingOverride.objects.get(key="brand_color").value == ""
    assert conf.brand_color() == ""
    assert "style=" not in html_tag(manager_client)
    assert conf.setting_source("brand_color") == conf.SOURCE_SCREEN

    # 出所が「画面」になったので、もう一度開くと「答えの値に戻す」が出て、押すと答えの色に戻る
    body = manager_client.get(edit_url("brand_color")).content.decode()
    assert clear_url("brand_color") in body and "答えの値に戻す" in body
    assert manager_client.post(clear_url("brand_color")).status_code == 302
    assert conf.brand_color() == "#15803d"


def test_empty_industry_saves_and_clear_goes_back_to_answer(manager_client, settings):
    settings.SEIZOU = {"industry": "作り物の業種"}

    response = manager_client.post(edit_url("industry"), {"industry": ""})

    assert response.status_code == 302
    assert subtitle(manager_client) == "seizou"

    assert manager_client.post(clear_url("industry")).status_code == 302
    assert subtitle(manager_client) == "作り物の業種"


def test_bad_brand_color_is_refused_as_before(manager_client, settings):
    # 形の合わない字は今と同じく services が断り、土台の form_valid が同じ画面を描き直す (知らせの出る所は字で見ない)
    settings.SEIZOU = {"brand_color": "#15803d"}

    response = manager_client.post(edit_url("brand_color"), {"brand_color": "red"})

    assert response.status_code == 200
    assert not SettingOverride.objects.exists()
    assert conf.brand_color() == "#15803d"
