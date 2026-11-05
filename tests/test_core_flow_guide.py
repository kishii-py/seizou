"""ダッシュボードの「はじめての方へ」の枠 (core.dashboard.flow_guide と home.html)。件数は作り物のデータを services で作って数える。

Django の test client で HTML を見る (本物のブラウザは使わない)。発注はつながりの無い協力企業で作り、相手に送らない。
名前は作り物だけ。
"""

import re
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.billing import services as billing_services
from seizou.connect import services as connect_services
from seizou.connect.models import Inbox
from seizou.core import dashboard
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing import services as purchasing_services
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services

SALES = ["受注", "指示書", "出荷", "納品書", "請求書", "入金"]
PURCHASE = ["発注", "注文回答", "出荷の知らせ", "受入", "検収"]
NOT_COUNTED = {"出荷の知らせ", "検収"}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {
            "code": "C001",
            "name": "作り物の得意先",
            "role": "customer",
            "closing_day": 20,
            "payment_month": "next",
            "payment_day": 25,
        },
        user,
    )


@pytest.fixture
def partner(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor", "rounding": "round"}, user
    )


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def cutting(user):
    return masters_services.create_process({"code": "P01", "name": "作り物の切削", "sort_order": 1}, user)


def make_order(user, customer, item, *, unit_price="100"):
    """今日の受注 (数量 10 の明細 1 行。納期は 3 日後)。"""
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal(unit_price), "due_date": timezone.localdate() + timedelta(days=3)}],
        user,
        order_date=timezone.localdate(),
    )


def flow_of(key):
    guide = dashboard.flow_guide()
    return next(flow for flow in guide["flows"] if flow["key"] == key)


def counts(key):
    return {step["label"]: step["count"] for step in flow_of(key)["steps"]}


def home(client):
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    return response.content.decode()


class _NoParts:
    """部品が 1 つも入っていない会社の apps の代わり。"""

    def get_model(self, app_label, name):
        raise LookupError(f"{app_label}.{name}")


@pytest.mark.django_db
def test_flow_guide_lists_both_flows_in_order():
    guide = dashboard.flow_guide()
    assert [flow["key"] for flow in guide["flows"]] == ["sales", "purchase"]
    assert [flow["title"] for flow in guide["flows"]] == ["受注から入金まで", "発注から検収まで"]
    for flow, labels in zip(guide["flows"], (SALES, PURCHASE)):
        assert [step["label"] for step in flow["steps"]] == labels
        for step in flow["steps"]:
            assert step["url"] == reverse(step["url_name"])
            if step["label"] in NOT_COUNTED:
                assert step["count"] is None, step["label"]
            else:
                assert step["count"] == 0, step["label"]
        assert flow["next"] is None


@pytest.mark.django_db
def test_flow_guide_counts_open_order_and_next(user, customer, shaft, cutting):
    order = make_order(user, customer, shaft)
    sales = counts("sales")
    assert sales["受注"] == 0
    assert sales["指示書"] == 1
    assert sales["出荷"] == 1
    first = flow_of("sales")["next"]
    assert first["label"] == "指示書"  # 受信した注文が 0 なので受注の段は飛ぶ
    assert first["url"] == reverse("orders:orderline_list")

    # 指示書を出した明細は数えない (出荷の段は残る)
    workorders_services.issue_workorder(
        order.lines.get(), Decimal("10"), [{"process": cutting, "planned_qty": Decimal("10")}], user
    )
    assert counts("sales")["指示書"] == 0
    assert counts("sales")["出荷"] == 1

    # 受信した注文 (人が受け入れていない物) があれば、受注の段が次にすることになる
    Inbox.objects.create(message_id=uuid.uuid4(), partner=customer, kind="order", payload={})
    assert counts("sales")["受注"] == 1
    assert flow_of("sales")["next"]["label"] == "受注"

    orders_services.cancel_order(order, user)
    sales = counts("sales")
    assert sales["指示書"] == 0
    assert sales["出荷"] == 0


@pytest.mark.django_db
def test_flow_guide_counts_delivery_and_invoice(user, customer, shaft):
    order = make_order(user, customer, shaft, unit_price="1500")
    line = order.lines.get()
    shipment = shipping_services.ship(line, Decimal("10"), timezone.localdate(), user)
    sales = counts("sales")
    assert sales["納品書"] == 1
    assert sales["請求書"] == 0

    shipping_services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    sales = counts("sales")
    assert sales["納品書"] == 0
    assert sales["請求書"] == 1

    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=timezone.localdate())
    sales = counts("sales")
    assert sales["請求書"] == 0
    assert sales["入金"] == 1

    half = invoice.total / 2
    billing_services.record_payment(invoice, timezone.localdate(), half, user)
    assert counts("sales")["入金"] == 1  # 一部入金は残る
    billing_services.record_payment(invoice, timezone.localdate(), invoice.total - half, user)
    assert counts("sales")["入金"] == 0


@pytest.mark.django_db
def test_flow_guide_counts_purchase_draft_and_sent(user, partner, shaft):
    purchase_order = purchasing_services.create_purchase_order(
        partner,
        [{"item": shaft, "qty": Decimal("100"), "unit_price": Decimal("45")}],
        user,
        order_date=date(2026, 10, 1),
        send=False,
    )
    purchase = counts("purchase")
    assert purchase["発注"] == 1
    assert purchase["注文回答"] == 0
    assert flow_of("purchase")["next"]["label"] == "発注"

    purchasing_services.mark_sent(purchase_order, user)
    purchase = counts("purchase")
    assert purchase["発注"] == 0
    assert purchase["注文回答"] == 1

    # 出荷の知らせのある明細は、受入の残がある間だけ受入の段に数える
    line = purchase_order.lines.get()
    connect_services.record_shipping_notice(
        line, Decimal("100"), date(2026, 10, 10), None, message_id=uuid.uuid4(), shipment_no="SN-0001"
    )
    assert counts("purchase")["受入"] == 1
    purchasing_services.receive(line, Decimal("40"), date(2026, 10, 11), user)
    assert counts("purchase")["受入"] == 1
    purchasing_services.receive(line, Decimal("60"), date(2026, 10, 12), user)
    assert counts("purchase")["受入"] == 0


@pytest.mark.django_db
def test_flow_guide_one_query_per_counted_step():
    with CaptureQueriesContext(connection) as captured:
        guide = dashboard.flow_guide()
    counted = [step for flow in guide["flows"] for step in flow["steps"] if step["count"] is not None]
    assert len(counted) == 9
    assert len(captured.captured_queries) == len(counted)


def test_flow_guide_none_without_parts(monkeypatch):
    monkeypatch.setattr(dashboard, "apps", _NoParts())
    assert dashboard.flow_guide() is None
    assert dashboard.flow_neighbors("shipping:shipment_list") is None


def test_flow_neighbors_prev_and_next():
    shipping = dashboard.flow_neighbors("shipping:shipment_list")
    assert shipping["flow"] == "受注から入金まで"
    assert shipping["label"] == "出荷"
    assert shipping["prev"] == {"label": "指示書", "url": reverse("orders:orderline_list")}
    assert shipping["next"] == {"label": "納品書", "url": reverse("shipping:deliverynote_list")}

    purchase = dashboard.flow_neighbors("purchasing:purchaseorder_list")
    assert purchase["flow"] == "発注から検収まで"
    assert purchase["label"] == "発注・注文回答・出荷の知らせ"
    assert purchase["prev"] is None
    assert purchase["next"] == {"label": "受入", "url": reverse("purchasing:receipt_list")}

    billing = dashboard.flow_neighbors("billing:invoice_list")
    assert billing["label"] == "請求書・入金"
    assert billing["prev"]["label"] == "納品書"
    assert billing["next"] is None

    assert dashboard.flow_neighbors("masters:item_list") is None


@pytest.mark.django_db
def test_home_shows_today_tasks_after_cards(logged_in, user, customer, shaft, monkeypatch):
    make_order(user, customer, shaft)
    body = home(logged_in)
    frame = body.index('aria-labelledby="flow-guide-title"')
    stats = body.index('data-tour="stats"')
    shipping = body.index('aria-labelledby="shipping-title"')
    # 数字のカード (会社の KPI) → 今日やること (個人のタスク) → 出荷と生産の図 (本人の決め 11-05 09:4x)
    assert stats < frame < shipping
    section = re.search(r'<section[^>]*aria-labelledby="flow-guide-title".*?</section>', body, re.S).group(0)
    # 題は h2 の 1 回 (行の並びの aria-label にも同じ字が入るので、見える字で数える)
    assert section.count('<h2 id="flow-guide-title" class="text-lg font-bold">今日やること</h2>') == 1
    assert "はじめての方へ" not in section
    assert "次にすること:" not in section
    assert "btn-primary" not in section
    for word in ("はじめに", "品目", "注残の明細", "受信箱の未処理"):
        assert word not in body, word

    # 枠の無いときと主色のボタンの数が同じ (枠は主色のボタンを足さない)
    with_frame = body.count("btn-primary")
    monkeypatch.setattr(dashboard, "flow_guide", lambda: None)
    without = home(logged_in)
    assert 'aria-labelledby="flow-guide-title"' not in without
    assert without.count("btn-primary") == with_frame
