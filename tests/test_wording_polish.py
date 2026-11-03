"""初見テスト 3 回目 (R3) の言葉と小さな動きの直し。Django の test client で HTML と表の状態を見る。

既にある試験のファイルから import せず、fixture は手本の試験の形を写す。品番と会社名は作り物。
"""

import base64
import json
import re
import secrets
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.connect import signing
from seizou.connect.models import Inbox
from seizou.core.exceptions import RuleViolation
from seizou.core.screens import all_screens
from seizou.masters import services as masters_services
from seizou.masters.forms import PartnerForm
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from seizou.shipping.models import Shipment
from seizou.workorders.models import WorkOrder

RECEIVE_ENV = "SEIZOU_PARTNER_POLISHCO_SECRET"
ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)

ROUNDING_HELP = "納品書と請求書の金額の端数 (四捨五入・切上・切捨)。空なら会社の既定に従います。"
TAX_HELP = "消費税の端数 (トータル・各行四捨五入・各行切捨)。空なら会社の既定に従います。"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


def text(response):
    return response.content.decode()


# --- 取引先の入力の端数の説明 ---


@pytest.mark.django_db
def test_partner_form_help_texts_for_rounding():
    fields = PartnerForm().fields
    assert fields["rounding"].help_text == ROUNDING_HELP
    assert fields["tax_mode"].help_text == TAX_HELP
    # 今の 2 行は字のまま
    assert fields["closing_day"].help_text == "1〜31。月末締めは 31"
    assert fields["payment_day"].help_text == "1〜31。月末は 31"


@pytest.mark.django_db
def test_partner_create_shows_rounding_help(client_logged_in):
    response = client_logged_in.get(reverse("masters:partner_create"))
    assert response.status_code == 200
    body = text(response)
    assert ROUNDING_HELP in body and TAX_HELP in body
    # 取引先の種類の字は「協力企業」(古い呼び名は tests/test_wording.py が全部のファイルで見るので、ここには書かない)
    assert "協力企業" in body


# --- 受注と注文残の一覧の切り替えの字 ---


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["orders:order_list", "orders:orderline_list"])
def test_order_lists_toggle_says_shipping_remaining(client_logged_in, url_name):
    response = client_logged_in.get(reverse(url_name))
    assert response.status_code == 200
    body = text(response)
    assert 'aria-current="true">出荷の残りがある物だけ</span>' in body
    assert "残のある物だけ" not in body

    everything = text(client_logged_in.get(reverse(url_name), {"all": "1"}))
    assert ">出荷の残りがある物だけ</a>" in everything
    assert 'aria-current="true">すべて</span>' in everything
    assert "残のある物だけ" not in everything


# --- 届いた注文の詳細の「承認」(形は tests/test_connect_inbox_views.py の receive_order を写した) ---


def _new_secret() -> str:
    """公開の仕様の形 (接頭辞つきの base64) の鍵を実行時に作る。"""
    return "whsec_" + base64.b64encode(secrets.token_bytes(32)).decode()


def _order_payload(msg_id, part_no):
    return {
        "id": msg_id,
        "kind": "order",
        "sent_at": "2026-11-02T10:00:00+09:00",
        "order_no": "PO-0001",
        "order_date": "2026-11-02",
        "buyer": {"id": "a-company", "name": "作り物の発注する会社"},
        "supplier": {"code": "PL01", "name": "作り物の受ける会社"},
        "note": "",
        "lines": [
            {
                "line_no": 1,
                "part_no": part_no,
                "part_name": f"作り物の部品 {part_no}",
                "qty": "10.00",
                "unit": "個",
                "unit_price": "250.00",
                "due_date": "2026-11-20",
                "note": "",
            }
        ],
    }


@pytest.fixture
def receive_secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(RECEIVE_ENV, value)
    return value


@pytest.fixture
def receive_connection(user, receive_secret):
    partner = masters_services.create_partner({"code": "PC01", "name": "作り物の得意先", "role": "customer"}, user)
    return connect_services.register_connection(partner, "", RECEIVE_ENV, "receive", user)


@pytest.fixture
def inbox(receive_connection, receive_secret):
    msg_id = str(uuid.uuid4())
    body = json.dumps(_order_payload(msg_id, "X-100"), ensure_ascii=False).encode("utf-8")
    result = connect_services.receive(body, signing.headers_for(receive_secret, msg_id, body))
    assert isinstance(result, Inbox)
    return result


@pytest.mark.django_db
def test_inbox_detail_button_says_approve(client_logged_in, inbox):
    assert inbox.status == "received"
    response = client_logged_in.get(reverse("connect:inbox_detail", args=[inbox.pk]))
    assert response.status_code == 200
    body = text(response)
    assert f'href="{reverse("connect:inbox_accept", args=[inbox.pk])}"' in body
    assert "承認</a>" in body
    assert "受け入れる</a>" not in body


def test_inbox_detail_usecase_says_approve():
    usecases = {screen.url_name: screen.usecase for screen in all_screens()}
    assert "次にすること: 「承認」か「断る」へ" in usecases["connect:inbox_detail"]


# --- 取引先との接続の一覧の先頭の列 (向き) が詳細への link ---


@pytest.mark.django_db
def test_connection_list_first_column_links_to_detail(client_logged_in, receive_connection):
    response = client_logged_in.get(reverse("connect:connection_list"))
    assert response.status_code == 200
    detail_url = reverse("connect:connection_detail", args=[receive_connection.pk])
    links = re.findall(rf'<a class="link link-hover[^"]*" href="{re.escape(detail_url)}"[^>]*>(.*?)</a>', text(response))
    assert links == ["受ける"]


# --- 分納を許さない会社の出荷の知らせ (形は tests/test_shipping_partial.py と tests/test_shipping_views.py を写した) ---

PARTIAL_SHIP_MESSAGE = "この会社は分納を許していません (設定「分納」)。残りの数 10 をまとめて出荷してください。"
PARTIAL_UPDATE_MESSAGE = "この会社は分納を許していません (設定「分納」)。この出荷を除いた残りの数 10 をまとめて出荷してください。"


@pytest.fixture
def order_line(user):
    """数量 10 の明細 1 行の受注の明細。"""
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.mark.django_db
def test_partial_shipment_notice_is_japanese(settings, user, order_line):
    settings.SEIZOU = {"partial_shipment": False}
    with pytest.raises(RuleViolation) as caught:
        shipping_services.ship(order_line, Decimal("4"), SHIPPED_AT, user)
    assert caught.value.code == "分納"
    assert caught.value.message == PARTIAL_SHIP_MESSAGE
    assert Shipment.objects.count() == 0


@pytest.mark.django_db
def test_partial_shipment_update_notice_is_japanese(settings, user, order_line):
    shipment = shipping_services.ship(order_line, Decimal("10"), SHIPPED_AT, user)
    settings.SEIZOU = {"partial_shipment": False}
    with pytest.raises(RuleViolation) as caught:
        shipping_services.update_shipment(shipment, Decimal("4"), SHIPPED_AT, user)
    assert caught.value.code == "分納"
    assert caught.value.message == PARTIAL_UPDATE_MESSAGE
    shipment.refresh_from_db()
    assert shipment.qty == Decimal("10")


@pytest.mark.django_db
def test_partial_shipment_screen_has_no_internal_name(settings, client_logged_in, order_line):
    settings.SEIZOU = {"partial_shipment": False}
    response = client_logged_in.post(
        reverse("shipping:shipment_create"),
        {"order_line": order_line.pk, "qty": "4", "shipped_at": "2026-11-05", "note": ""},
    )
    assert response.status_code == 200
    body = text(response)
    alert = re.search(r'<div class="alert alert-error[^"]*"[^>]*>(.*?)</div>', body, re.S)
    assert alert is not None
    assert f">分納</span> {PARTIAL_SHIP_MESSAGE}</span>" in alert.group(1)
    assert "10.00" not in alert.group(1)
    assert "partial_shipment" not in body
    assert Shipment.objects.count() == 0


def test_qty_text_drops_trailing_zeros():
    assert shipping_services._qty_text(Decimal("4.50")) == "4.5"
    assert shipping_services._qty_text(Decimal("10.00")) == "10"
    assert shipping_services._qty_text(Decimal("0.25")) == "0.25"


# --- 取消した受注の明細を選んだ指示書 (形は tests/test_workorders_views.py を写した) ---


@pytest.fixture
def cancelled_line(user, order_line):
    """取り消した受注の明細 (数量 5)。order_line (数量 10) は取り消していない。"""
    order = orders_services.create_order(
        order_line.order.customer,
        [{"item": order_line.item, "qty": Decimal("5"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    orders_services.cancel_order(order, user)
    return order.lines.get()


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="切断", sort_order=1)


@pytest.mark.django_db
def test_issue_with_cancelled_line_is_r05_red_and_keeps_choice(client_logged_in, cancelled_line, cut):
    response = client_logged_in.post(
        reverse("workorders:workorder_create"),
        {"order_line": cancelled_line.pk, "qty": "5", "processes": [cut.pk]},
    )
    assert response.status_code == 200
    body = text(response)
    assert "alert-error" in body and "R05" in body
    assert "取消していない受注の明細を選んでください。" in body
    assert "正しく選択してください" not in body
    assert f'<option value="{cancelled_line.pk}" selected>' in body
    assert WorkOrder.objects.count() == 0


@pytest.mark.django_db
def test_issue_form_keeps_cancelled_line_from_link(client_logged_in, order_line, cancelled_line):
    response = client_logged_in.get(reverse("workorders:workorder_create"), {"order_line": cancelled_line.pk})
    assert response.status_code == 200
    assert f'<option value="{cancelled_line.pk}" selected>' in text(response)

    # 何も選ばない画面の選択肢には、取消の明細を出さない (今のまま)
    plain = client_logged_in.get(reverse("workorders:workorder_create"))
    choices = [value for value, _label in plain.context["form"].fields["order_line"].choices if value]
    assert [int(str(value)) for value in choices] == [order_line.pk]
    # 数でない ?order_line= は飛ばす (選択肢は今のまま)
    odd = client_logged_in.get(reverse("workorders:workorder_create"), {"order_line": "abc"})
    choices = [value for value, _label in odd.context["form"].fields["order_line"].choices if value]
    assert [int(str(value)) for value in choices] == [order_line.pk]
