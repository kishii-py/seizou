"""知らせの帯 (選べる設定 site_notice・seizou.core.conf の check_site_notice と site_notice・core/_site_notice_band.html)。

依頼書 N398 の試験 (a)〜(e)。値は答えの YAML の settings と同じ道 (settings.SEIZOU) で入れる。作り物の字だけ。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.core import conf
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services

NOTICE = "デモです。入れたデータは毎晩消えます。本物の情報を入れないでください。"
BAND = (
    '<div class="bg-warning text-warning-content px-4 py-2 text-center text-sm font-bold" role="status" data-site-notice>'
    f"{NOTICE}</div>"
)
PRACTICE_BAND = "練習モード (ここでの登録は本番に残りません。取引先へは送りません。閉じるときは seizou_practice stop)"


@pytest.fixture
def notice(settings):
    """答えの YAML の settings に site_notice を書いた置き場 (settings.SEIZOU に入る)。値を変えるには返す関数を呼ぶ。"""

    def put(value):
        settings.SEIZOU = {**(getattr(settings, "SEIZOU", {}) or {}), "site_notice": value}

    put(NOTICE)
    return put


def _get(client, url_name, *args):
    response = client.get(reverse(url_name, args=args))
    assert response.status_code == 200, url_name
    return response, response.content.decode()


@pytest.mark.django_db
def test_no_band_by_default(client, user):
    """(a) 既定 (空) では、ログインの画面にもダッシュボードにも帯が出ない。"""
    assert conf.DEFAULTS["site_notice"] == ""
    assert conf.site_notice() == ""

    response, html = _get(client, "core:login")
    assert response.context["seizou"]["site_notice"] == ""
    assert 'name="username"' in html
    assert "data-site-notice" not in html

    client.force_login(user)
    response, html = _get(client, "core:home")
    assert response.context["seizou"]["site_notice"] == ""
    assert '<div class="drawer' in html
    assert "data-site-notice" not in html


@pytest.mark.django_db
def test_band_on_dashboard_and_login(client, user, notice, monkeypatch):
    """(b) site_notice に字を入れると、ログインの画面とダッシュボードの <body> の直後に帯が出る。練習モードの帯があるときはその下。"""
    monkeypatch.delenv("SEIZOU_PRACTICE", raising=False)
    _response, html = _get(client, "core:login")
    assert html.count(BAND) == 1
    assert html.index("<body") < html.index(BAND) < html.index('name="username"')

    client.force_login(user)
    _response, html = _get(client, "core:home")
    assert html.count(BAND) == 1
    assert html.index("<body") < html.index(BAND) < html.index('<div class="drawer')

    monkeypatch.setenv("SEIZOU_PRACTICE", "1")
    _response, html = _get(client, "core:home")
    assert html.index("<body") < html.index(PRACTICE_BAND) < html.index(BAND) < html.index('<div class="drawer')


@pytest.mark.django_db
def test_band_text_is_escaped(client, notice):
    """(c) 字に <b> を入れても、HTML として通さずに字のまま出す。前後の空白は落とす。"""
    notice("  <b>棚卸しのため 12/29 は入力を止めます</b>  ")
    _response, html = _get(client, "core:login")
    assert "data-site-notice>&lt;b&gt;棚卸しのため 12/29 は入力を止めます&lt;/b&gt;</div>" in html
    assert "<b>棚卸し" not in html


@pytest.fixture
def delivery_note(user):
    """作り物の得意先と部品で、受注 1 件・出荷 1 件・納品書 1 枚を作る。"""
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    line = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("2"), "unit_price": Decimal("1500")}],
        user,
        order_date=date(2026, 11, 2),
    ).lines.get()
    shipment = shipping_services.ship(line, Decimal("2"), date(2026, 11, 5), user)
    return shipping_services.create_delivery_note(customer, [shipment], user)


@pytest.mark.django_db
def test_band_not_on_delivery_note_print(logged_in, notice, delivery_note):
    """(d) 納品書の印刷用の画面 (帳票の土台 core/report_base.html) には、帯を出さない。"""
    _response, html = _get(logged_in, "shipping:deliverynote_print", delivery_note.pk)
    assert delivery_note.note_no in html
    assert "data-site-notice" not in html
    assert NOTICE not in html


def test_check_refuses_over_200_chars_in_japanese():
    """(e) 200 字を超える値と字でない値は、ほかの鍵の検査 (services の validate_setting) と同じ形の日本語の文で断る。"""
    assert conf.SITE_NOTICE_MAX == 200
    assert conf.check_site_notice("あ" * 200) == "あ" * 200
    assert conf.check_site_notice(" " + "あ" * 200 + " ") == "あ" * 200
    assert conf.check_site_notice("") == ""

    with pytest.raises(ValueError) as raised:
        conf.check_site_notice("あ" * 201)
    message = str(raised.value)
    assert message.startswith("保存できません。site_notice (知らせの帯) は 200 字までの字にしてください。")
    assert "入れた字数: 201" in message
    assert "あ" * 201 not in message

    for value in (None, 2026, True, ["字"]):
        with pytest.raises(ValueError) as raised:
            conf.check_site_notice(value)
        assert str(raised.value).startswith("保存できません。site_notice (知らせの帯) は字にしてください。"), value


@pytest.mark.django_db
def test_refused_value_shows_no_band(client, notice):
    """(e) 断られる値 (201 字・字でない値) を答えに書いた置き場は、画面を 500 にせず、帯を出さない。"""
    for value in ("あ" * 201, 2026, None):
        notice(value)
        response, html = _get(client, "core:login")
        assert response.context["seizou"]["site_notice"] == "", value
        assert "data-site-notice" not in html, value

    notice("あ" * 200)
    _response, html = _get(client, "core:login")
    assert f"data-site-notice>{'あ' * 200}</div>" in html
