"""金額の閲覧の無い人が、金額に関わる主な入力へ正しい中身で直に送った時の断りと描き直し (依頼書 N436 の試験 2)。

背景は tests/test_money_forms_hide_on_post.py の頭 (本人の決め 11-12 07:2x = 開いた時点で断る・11-12 14:0x = 金額を隠す分かれを守りとして残す)。
入力ごとに見る物:
- 応答の本文に、その操作の断りの字 (core.access.write_refusal の文。form_valid の頭の access.require_write が赤の知らせで出す) が出る。
- 何も保存されない (その表の行の数と、変える記録の値が、送る前と同じ)。
- 描き直した本文に、金額の項目の入力の欄と、作り物の記録の単価と金額の字と、data-fill-amount のボタンが無い
  (人が自分で入れる欄 = 入金と調整の amount・発注の新規登録の unit_price は、試験 1 の TYPED_MONEY_FIELDS の訳のとおり出てよい。
  送った金額の字 5,000・-660・6,100 は人が入れた字なので見ない)。金額に関わらない欄 (数量・日付・備考) は出てよい (出るかどうかは見ない)。
- 同じ人が同じ画面を開くと 403 で、断りの字が同じ文。
記録は試験 1 の World で作る (金額の判定だけが on の会社。会社・人・品番は作り物)。
"""

from decimal import Decimal
from functools import cached_property

import pytest
from django.urls import reverse

from seizou.billing.models import Adjustment, Invoice, Payment
from seizou.core import access
from seizou.masters import services as masters_services
from seizou.masters.models import ItemPrice
from seizou.orders import services as orders_services
from seizou.orders.models import Order
from seizou.purchasing.models import PurchaseOrder
from tests.test_money_forms_hide_on_post import (
    AMOUNT,
    PRICE,
    TYPED_MONEY_FIELDS,
    World,
    found_texts,
    money_field_names,
    money_form_screens,
    money_on,  # noqa: F401 (fixture)
    money_texts,
    no_money_person,
    record_money,
)

HEADING = "この画面は開けません"


def _management(total, initial):
    return {
        "lines-TOTAL_FORMS": str(total),
        "lines-INITIAL_FORMS": str(initial),
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
    }


class DirectWorld(World):
    """試験 1 の記録に、発注の新規登録の取引先 (つながりの無い協力企業) と、部品の発注の単価の行 (7,331) を足す。"""

    @cached_property
    def supplier(self):
        return masters_services.create_partner({"code": "S701", "name": "作り物の協力企業", "role": "subcontractor"}, self.owner)

    @cached_property
    def purchase_price(self):
        return masters_services.add_item_price(
            {"item": self.item, "kind": ItemPrice.KIND_PURCHASE, "unit_price": PRICE}, self.owner
        )


def _invoice_create(w):
    shipment = w.noted_shipment
    data = {"customer": w.customer.pk, "period": "", "issued_on": "2026-11-30", "shipments": [shipment.pk]}
    return reverse("billing:invoice_create"), data, lambda: Invoice.objects.count(), [PRICE, AMOUNT]


def _invoice_closing(w):
    w.noted_shipment
    w.freeze_today()
    data = {"month": "2026-11", "closing_day": "20", "issued_on": "2026-11-20"}
    return reverse("billing:invoice_closing"), data, lambda: Invoice.objects.count(), [PRICE, AMOUNT]


def _invoice_reissue(w):
    invoice = w.invoice

    def state():
        invoice.refresh_from_db()
        return Invoice.objects.count(), invoice.status

    url = reverse("billing:invoice_reissue", args=[invoice.pk])
    return url, {"issued_on": "2026-11-30"}, state, [PRICE, AMOUNT] + record_money(invoice)


def _payment_create(w):
    invoice = w.invoice
    data = {"received_on": "2026-12-25", "amount": "5000", "method": "transfer", "note": "作り物の入金"}
    url = reverse("billing:payment_create", args=[invoice.pk])
    return url, data, lambda: Payment.objects.count(), [PRICE, AMOUNT] + record_money(invoice)


def _shipment_payment_create(w):
    shipment = w.noted_shipment
    data = {"received_on": "2026-12-25", "amount": "5000", "method": "cash", "note": ""}
    url = reverse("billing:shipment_payment_create", args=[shipment.pk])
    return url, data, lambda: Payment.objects.count(), [PRICE, AMOUNT]


def _adjustment_create(w):
    invoice = w.invoice
    data = {"adjusted_on": "2026-12-26", "amount": "-660", "reason": Adjustment.REASON_FEE, "note": ""}
    url = reverse("billing:adjustment_create", args=[invoice.pk])
    return url, data, lambda: Adjustment.objects.count(), [PRICE, AMOUNT] + record_money(invoice)


def _order_create(w):
    w.item_price
    data = {
        "customer": w.customer.pk,
        "order_date": "2026-11-02",
        "customer_order_no": "",
        **_management(1, 0),
        "lines-0-item": w.item.pk,
        "lines-0-customer_part_no": "",
        "lines-0-qty": "3",
        "lines-0-unit": "",
        "lines-0-due_date": "",
        "lines-0-note": "",
    }
    return reverse("orders:order_create"), data, lambda: Order.objects.count(), [PRICE, AMOUNT]


def _order_update(w):
    order = w.order
    line = order.lines.get()
    data = {
        "customer": order.customer_id,
        "order_date": order.order_date.isoformat(),
        "customer_order_no": order.customer_order_no,
        "note": order.note,
        "state_token": orders_services.order_state_token(order),
        **_management(1, 1),
        "lines-0-order_line": line.pk,
        "lines-0-item": line.item_id,
        "lines-0-customer_part_no": "",
        "lines-0-qty": "2",
        "lines-0-unit": line.unit_code,
        "lines-0-due_date": "",
        "lines-0-note": "",
    }

    def state():
        line.refresh_from_db()
        return Order.objects.count(), line.qty, line.unit_price, line.amount

    return reverse("orders:order_update", args=[order.pk]), data, state, [PRICE, AMOUNT] + record_money(order)


def _purchaseorder_create(w):
    w.purchase_price
    data = {
        "partner": w.supplier.pk,
        "order_date": "2026-11-02",
        "note": "",
        **_management(1, 0),
        "lines-0-item": w.item.pk,
        "lines-0-qty": "3",
        "lines-0-unit": "",
        "lines-0-unit_price": "6100",
        "lines-0-due_date": "2026-11-20",
        "lines-0-note": "",
    }
    return reverse("purchasing:purchaseorder_create"), data, lambda: PurchaseOrder.objects.count(), [PRICE, AMOUNT]


def _purchaseorder_change(w):
    po = w.purchase_order
    line = po.lines.get()
    data = {
        "note": po.note,
        **_management(1, 1),
        "lines-0-line": line.pk,
        "lines-0-qty": "2",
        "lines-0-due_date": "2026-11-20",
        "lines-0-note": "",
    }

    def state():
        po.refresh_from_db()
        line.refresh_from_db()
        return PurchaseOrder.objects.count(), po.status, po.revision_no, line.qty, line.unit_price

    url = reverse("purchasing:purchaseorder_change", args=[po.pk])
    return url, data, state, [PRICE, AMOUNT] + record_money(po)


def _inbox_accept_conditional(w):
    inbox = w.inbox
    data = {"item_1": w.item.pk, "line_1_qty": "2", "line_1_due_date": "2026-11-20"}

    def state():
        inbox.refresh_from_db()
        return Order.objects.count(), inbox.reply_type, inbox.accepted_at

    url = reverse("connect:inbox_accept_conditional", args=[inbox.pk])
    return url, data, state, [PRICE, AMOUNT]


#: 入力 (URL の名前) → 送る中身を作る helper。返す物は (URL, 送る中身, 保存の有無を見る値の関数, 本文に出てはいけない金額の値)。
#: 依頼書の 10 の入力と、N409b が足した調整の記録 (11 個)。
DIRECT_POSTS = {
    "billing:invoice_create": _invoice_create,
    "billing:invoice_closing": _invoice_closing,
    "billing:invoice_reissue": _invoice_reissue,
    "billing:payment_create": _payment_create,
    "billing:shipment_payment_create": _shipment_payment_create,
    "billing:adjustment_create": _adjustment_create,
    "orders:order_create": _order_create,
    "orders:order_update": _order_update,
    "purchasing:purchaseorder_create": _purchaseorder_create,
    "purchasing:purchaseorder_change": _purchaseorder_change,
    "connect:inbox_accept_conditional": _inbox_accept_conditional,
}


@pytest.fixture
def world(db, monkeypatch):
    return DirectWorld(monkeypatch)


def says(body: str, text: str) -> bool:
    """本文に断りの文の文ごとの字が全部ある (右上の知らせは 1 文目を太字の span に分けて描くので、文ごとに見る)。"""
    sentences = [part + "。" for part in text.split("。") if part]
    return bool(sentences) and all(sentence in body for sentence in sentences)


def test_direct_posts_are_money_screens():
    """個別に見る 11 の入力は、どれも金額に関わる操作の入力の画面 (試験 1 の総当たりの中)。"""
    assert len(DIRECT_POSTS) == 11
    assert set(DIRECT_POSTS) <= set(money_form_screens())


@pytest.mark.django_db
@pytest.mark.parametrize("name", sorted(DIRECT_POSTS))
def test_direct_post_is_refused_and_redraw_hides_money(client, money_on, world, name):  # noqa: F811
    """正しい中身で直に送ると、断りの字が出て何も保存せず、描き直しに金額の欄と字が無い。開くと 403 で同じ文。"""
    url, data, state, values = DIRECT_POSTS[name](world)
    texts = money_texts(values)
    person = no_money_person()
    action = money_form_screens()[name]
    refusal = access.write_refusal(person, action)
    assert refusal, name

    client.force_login(person)
    before = state()
    response = client.post(url, data)
    assert response.status_code == 200, (name, response.status_code)
    body = response.content.decode()
    assert says(body, refusal), name
    assert 'data-notice="stop"' in body, name
    assert state() == before, name
    assert money_field_names(body, TYPED_MONEY_FIELDS.get(name, frozenset())) == [], name
    assert found_texts(body, texts) == [], name
    assert "data-fill-amount" not in body, name

    # 同じ人が同じ画面を開くと、開いた時点で 403。断りの字は送った時と同じ文。
    opened = client.get(url)
    assert opened.status_code == 403, (name, opened.status_code)
    opened_body = opened.content.decode()
    assert HEADING in opened_body and says(opened_body, refusal), name
    assert state() == before, name


def test_amount_is_the_same_number_as_in_test_one():
    """作り物の金額は試験 1 と同じ値 (単価 7,331 × 数量 3)。"""
    assert AMOUNT == Decimal("21993")
