"""出荷の画面 (02 の出荷。core の土台の上)。Django の test client で HTML と表の状態を見る。

受注より多い出荷のポカよけ (R01) の画面のテストは tests/test_r01_over_shipment.py にある。
ブラウザでの確かめ (受注の明細の選ぶ欄の候補) は朝の E2E。
"""

import csv
import io
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.models import Order
from seizou.shipping import services as shipping_services
from seizou.shipping.models import Shipment
from tests.test_detail_header import _actions

ORDER_DATE = date(2026, 11, 2)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def order(user):
    """数量 10 の明細 1 行の受注。"""
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )


def _post(line, qty, shipped_at="2026-11-05", note=""):
    return {"order_line": line.pk, "qty": str(qty), "shipped_at": shipped_at, "note": note}


@pytest.mark.django_db
def test_shipment_list_requires_login(client, order):
    for name in ("shipping:shipment_list", "shipping:shipment_create"):
        response = client.get(reverse(name))
        assert response.status_code == 302
        assert reverse("core:login") in response["Location"]


@pytest.mark.django_db
def test_ship_partial_four_and_six_passes(logged_in, order):
    line = order.lines.get()
    form_page = logged_in.get(reverse("shipping:shipment_create"))
    assert form_page.status_code == 200
    body = form_page.content.decode()
    assert f'data-select-url="{reverse("orders:orderline_select")}"' in body
    assert "受注の明細" in body and "出荷日" in body

    first = logged_in.post(reverse("shipping:shipment_create"), _post(line, 4, note="作り物の備考"))
    shipment = Shipment.objects.get()
    assert first.status_code == 302
    assert first["Location"] == reverse("shipping:shipment_detail", args=[shipment.pk])
    assert shipment.note == "作り物の備考"
    order.refresh_from_db()
    assert order.status == Order.STATUS_PARTIAL

    detail = logged_in.get(first["Location"])
    detail_body = detail.content.decode()
    assert detail.status_code == 200
    assert shipment.shipment_no in detail_body and "作り物の備考" in detail_body

    second = logged_in.post(reverse("shipping:shipment_create"), _post(line, 6))
    assert second.status_code == 302
    second_body = logged_in.get(second["Location"]).content.decode()
    assert 'data-notice="check"' not in second_body  # 合計がちょうど 10 なので知らせない
    order.refresh_from_db()
    assert order.status == Order.STATUS_SHIPPED
    assert orders_services.remaining_qty(line) == Decimal("0")
    assert Shipment.objects.count() == 2


@pytest.mark.django_db
def test_ship_to_cancelled_order_is_r05_red(logged_in, user, order):
    line = order.lines.get()
    orders_services.cancel_order(order, user)
    response = logged_in.post(reverse("shipping:shipment_create"), _post(line, 4))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body and "R05" in body
    assert Shipment.objects.count() == 0


@pytest.mark.django_db
def test_shipment_list_shows_order_and_item(logged_in, order):
    line = order.lines.get()
    logged_in.post(reverse("shipping:shipment_create"), _post(line, 4))
    shipment = Shipment.objects.get()

    response = logged_in.get(reverse("shipping:shipment_list"))
    assert response.status_code == 200
    body = response.content.decode()
    assert shipment.shipment_no in body
    assert order.order_no in body
    assert "T-100" in body and "作り物の得意先" in body
    assert reverse("shipping:shipment_create") in body  # 「新しく入れる」

    found = logged_in.get(reverse("shipping:shipment_list"), {"q": "T-100"})
    assert [row["obj"] for row in found.context["rows"]] == [shipment]
    missing = logged_in.get(reverse("shipping:shipment_list"), {"q": "作り物でない"})
    assert missing.context["rows"] == []


@pytest.mark.django_db
def test_shipment_list_finds_item_name(logged_in, user, order):
    """出荷の一覧の「部品」の列に出ている品名の一部でも当たる (初見テスト R5 の U162。候補 C36 の 4)。

    品名で 0 件になり、同じ画面の上の帯の横断の検索では当たる食い違いがあった。
    """
    shipment = shipping_services.ship(order.lines.get(), Decimal("4"), timezone.localdate(), user)
    url = reverse("shipping:shipment_list")

    for word in ("軸", "T-100", "作り物の得意先"):
        found = logged_in.get(url, {"q": word})
        assert [row["obj"] for row in found.context["rows"]] == [shipment], word
    assert logged_in.get(url, {"q": "作り物でない"}).context["rows"] == []
    # 検索の欄の案内に入る (前後の字は比べない = 探す項目の並びをほかの直しが変えても、この試験は作り直さない)
    assert "品名" in logged_in.get(url).context["search_hint"].split("・")


@pytest.mark.django_db
def test_shipment_list_hint_says_customer_name(logged_in, user, order):
    """出荷の一覧の案内は「名前」でなく「得意先の名前」と書き、その名前で今と同じく当たる (初見テスト R5 の U162。候補 C36 の 7)。"""
    shipment = shipping_services.ship(order.lines.get(), Decimal("4"), timezone.localdate(), user)
    url = reverse("shipping:shipment_list")

    labels = logged_in.get(url).context["search_hint"].split("・")
    assert "得意先の名前" in labels
    assert "名前" not in labels
    found = logged_in.get(url, {"q": "作り物の得意先"})
    assert [row["obj"] for row in found.context["rows"]] == [shipment]


def _numbered_order(order, user):
    """得意先の注文番号 PO-777 の 2 つ目の受注。得意先と部品は fixture の受注と同じ物を使い回す (取引先コードは重ねられない)。"""
    return orders_services.create_order(
        order.customer,
        [{"item": order.lines.get().item, "qty": Decimal("5"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
        customer_order_no="PO-777",
    )


@pytest.mark.django_db
def test_shipment_detail_has_order_detail_button(logged_in, user, order):
    """出荷の詳細の頭の右に、その出荷の受注の詳細へ移る白の「受注の詳細」が 1 つ (初見テスト R5 の候補 C38 の 2)。

    R5 の起こし直しでは、出荷の詳細に受注へ移る link が 0 だった。並びは PDF の次で、納品書の無い出荷の白の「編集」と
    緑の「納品書作成」の前 (緑は 1 つのまま)。
    基本情報の「受注の明細」も同じ受注へ移る link (候補 C80)。
    """
    shipment = shipping_services.ship(order.lines.get(), Decimal("4"), timezone.localdate(), user)
    response = logged_in.get(reverse("shipping:shipment_detail", args=[shipment.pk]))
    assert response.status_code == 200
    body = response.content.decode()

    href = f'href="{reverse("orders:order_detail", args=[order.pk])}"'
    button = f'<a class="btn btn-outline border-base-300" {href}>'
    assert body.count(button) == 1
    assert body.count(href) == 2
    assert button in body
    assert "受注の詳細" in body[body.index(button) : body.index("</a>", body.index(button))]

    head = _actions(body)
    white_edit = f'<a class="btn btn-outline border-base-300" href="{reverse("shipping:shipment_update", args=[shipment.pk])}'
    green = '<a class="btn btn-primary"'
    assert head.index("PDF</a>") < head.index(button) < head.index(white_edit) < head.index(green)
    assert "納品書作成" in head[head.index(green) :]
    assert head.count("btn-primary") == 1


@pytest.mark.django_db
def test_shipment_list_shows_and_finds_customer_order_no(logged_in, user, order):
    """出荷の一覧に「得意先の注文番号」の列があり、その番号で探せる (初見テスト R5 の V117。候補 C38 の 4)。"""
    numbered = _numbered_order(order, user)
    plain_shipment = shipping_services.ship(order.lines.get(), Decimal("4"), timezone.localdate(), user)
    numbered_shipment = shipping_services.ship(numbered.lines.get(), Decimal("5"), timezone.localdate(), user)
    url = reverse("shipping:shipment_list")

    response = logged_in.get(url)
    labels = [col.label for col in response.context["columns"]]
    assert labels == ["出荷番号", "受注番号", "得意先の注文番号", "得意先", "部品", "数量", "出荷日", "納品書番号"]
    assert {row["obj"] for row in response.context["rows"]} == {plain_shipment, numbered_shipment}
    # 検索の欄の案内に入る (前後の字は比べない = 探す項目の並びをほかの直しが変えても、この試験は作り直さない)
    assert "得意先の注文番号" in response.context["search_hint"].split("・")

    found = logged_in.get(url, {"q": "PO-777"})
    assert [row["obj"] for row in found.context["rows"]] == [numbered_shipment]

    exported = logged_in.get(url, {"format": "csv"})
    header = next(csv.reader(io.StringIO(exported.content.decode("utf-8").lstrip("﻿"))))
    assert "得意先の注文番号" in header


@pytest.mark.django_db
def test_menu_has_shipping_entries(logged_in):
    response = logged_in.get(reverse("core:home"))
    body = response.content.decode()
    assert reverse("shipping:shipment_list") in body
    assert "出荷" in body


SHIPPED_AT_REQUIRED = "出荷日を入れてください。"
SHIPPED_AT_INVALID = "出荷日は 2026-11-04 の形で入れてください。"
DJANGO_REQUIRED = "このフィールドは必須です"  # tests/test_edi_wording.py と同じ字
DJANGO_INVALID_DATE = "日付を正しく入力してください"  # Django の DateField の既定の文 (日本語)


@pytest.mark.django_db
def test_shipped_at_messages_are_japanese(logged_in, order):
    from seizou.shipping.forms import ShipmentUpdateForm

    line = order.lines.get()
    url = reverse("shipping:shipment_create")

    empty = logged_in.post(url, _post(line, 4, shipped_at=""))
    assert empty.status_code == 200
    body = empty.content.decode()
    assert SHIPPED_AT_REQUIRED in body
    assert DJANGO_REQUIRED not in body

    # 月が 13 = どの読み方でも日付にならない字
    unreadable = logged_in.post(url, _post(line, 4, shipped_at="2026-13-01"))
    assert unreadable.status_code == 200
    body = unreadable.content.decode()
    assert SHIPPED_AT_INVALID in body
    assert DJANGO_INVALID_DATE not in body
    assert Shipment.objects.count() == 0

    # 出荷の編集の form も同じ文
    update_empty = ShipmentUpdateForm(data={"qty": "4", "shipped_at": "", "note": ""})
    assert update_empty.errors["shipped_at"] == [SHIPPED_AT_REQUIRED]
    update_unreadable = ShipmentUpdateForm(data={"qty": "4", "shipped_at": "2026-13-01", "note": ""})
    assert update_unreadable.errors["shipped_at"] == [SHIPPED_AT_INVALID]

    # 正しい日付は今までどおり通る
    response = logged_in.post(url, _post(line, 4))
    assert response.status_code == 302
    assert Shipment.objects.count() == 1
