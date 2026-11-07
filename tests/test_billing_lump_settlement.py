"""人が金額を決めて一式の行を請求書に載せる (決め PRICE-1d・作者の決め C107-1・C107-2・C107-3)。

一式の行 = 出荷に結び付かない請求書の明細。create_invoice の lumps = (受注の明細, 金額) で載せ、R09 の見方
(_refuse_unbillable_lump) と金額の検査 (0 より大きい数) を通す。一式の行だけの請求書の期間は発行日の締め期間
(都度の得意先は発行日の 1 日)。作り直しは取り消した請求書の一式の行を同じ金額で写し、画面で足した一式の行も受ける。
作り物のデータだけを使う。
"""

import html
import re
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services
from seizou.billing.models import Invoice
from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders import timeline
from seizou.shipping import services as shipping_services
from tests.billing_helpers import shipped

OCT_10 = date(2026, 10, 10)
NOV_05 = date(2026, 11, 5)
NOV_30 = date(2026, 11, 30)
#: 金額の閲覧だけを効かせる (tests/test_core_line_totals.py と同じ)
NO_MONEY = {"department": "off", "write": "off", "money": "on"}


def _customer(user, code="C001", **terms):
    return masters_services.create_partner(
        {"code": code, "name": f"作り物の得意先 {code}", "role": "customer", **terms}, user
    )


@pytest.fixture
def customer(user):
    return _customer(user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _line(user, customer, item, qty="300", price_basis="lump", price="50000"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal(price), "price_basis": price_basis}],
        user,
        order_date=date(2026, 10, 1),
    )
    return order.lines.get()


def _cancelled_lump(user, customer, item):
    """一式 (300・50000) を 100 出荷して納品書を発行し、受注を取り消した明細 (出し切らずに終えた一式)。"""
    line = _line(user, customer, item)
    shipped(user, customer, line, "100", OCT_10)
    orders_services.cancel_order(line.order, user)
    line.refresh_from_db()
    return line


def _post(client, customer, data):
    return client.post(
        reverse("billing:invoice_create"), {"customer": customer.pk, "issued_on": NOV_30.isoformat(), **data}
    )


# ---------------------------------------------------------------- (a) 一式の行だけの請求書


@pytest.mark.django_db
def test_lump_only_invoice(user, customer, item):
    line = _cancelled_lump(user, customer, item)

    invoice = services.create_invoice(customer, [], user, issued_on=NOV_30, lumps=[(line, Decimal("16667"))])

    row = invoice.lines.get()
    assert row.shipment_id is None
    assert row.order_line_id == line.pk
    assert row.qty == Decimal("100")
    assert row.unit_price == Decimal("16667") and row.amount == Decimal("16667")
    assert row.price_basis == "lump"
    assert row.last_shipped_at == OCT_10
    assert invoice.subtotal == Decimal("16667")
    assert invoice.tax == services._tax(customer, [Decimal("16667")])
    assert invoice.total == invoice.subtotal + invoice.tax
    # 期間は発行日の締め期間 (作者の決め C107-1)。空にしない。
    assert (invoice.period_from, invoice.period_to) == services.closing_period(customer, NOV_30)
    assert invoice.due_on == services.payment_due_date(customer, invoice.period_to)


@pytest.mark.django_db
def test_lump_only_invoice_per_delivery_period_is_issued_day(user, item):
    customer = _customer(user, code="C010", billing_method="per_delivery")
    line = _cancelled_lump(user, customer, item)

    invoice = services.create_invoice(customer, [], user, issued_on=NOV_30, lumps=[(line, "16667")])

    assert (invoice.period_from, invoice.period_to) == (NOV_30, NOV_30)


# ---------------------------------------------------------------- (b) 出荷の行と一式の行を混ぜた請求書


@pytest.mark.django_db
def test_mixed_invoice_period_follows_shipments(user, customer, item):
    lump = _cancelled_lump(user, customer, item)
    unit = _line(user, customer, item, qty="10", price_basis="unit", price="1000")
    shipment = shipped(user, customer, unit, "4", NOV_05)

    invoice = services.create_invoice(customer, [shipment], user, issued_on=date(2026, 12, 15), lumps=[(lump, "20000")])

    rows = list(invoice.lines.order_by("pk"))
    assert [row.shipment_id for row in rows] == [shipment.pk, None]
    assert invoice.subtotal == Decimal("24000")
    # 一式の行の最後の出荷日 (10-10) と発行日 (12-15) は期間を動かさない。
    assert (invoice.period_from, invoice.period_to) == services.closing_period(customer, NOV_05)


# ---------------------------------------------------------------- (c) 同じ一式を 2 枚に入れない (C107-2)


@pytest.mark.django_db
def test_same_lump_twice_is_r09(user, customer, item):
    line = _cancelled_lump(user, customer, item)
    first = services.create_invoice(customer, [], user, issued_on=NOV_30, lumps=[(line, "16667")])

    with pytest.raises(RuleViolation) as caught:
        services.create_invoice(customer, [], user, issued_on=date(2026, 12, 31), lumps=[(line, "16667")])

    assert caught.value.code == "R09"
    assert caught.value.message == (
        f"発行できません。請求書の明細は、出荷済みの物だけ。受注 {line.order.order_no} の 1 行目の一式は"
        f"請求書 {first.invoice_no} に入っているので、外してください。"
    )
    assert Invoice.objects.count() == 1
    services.cancel_invoice(first, user)
    again = services.create_invoice(customer, [], user, issued_on=date(2026, 12, 31), lumps=[(line, "16667")])
    assert again.lines.get().order_line_id == line.pk


# ---------------------------------------------------------------- (d) 出し切らずに終えた一式でない明細


@pytest.mark.django_db
def test_not_unbilled_lump_is_value_error(user, customer, item):
    done = _line(user, customer, item)
    shipped(user, customer, done, "300", OCT_10)  # 全部を出し終えた出荷 (印) がある
    unit = _line(user, customer, item, price_basis="unit")
    shipped(user, customer, unit, "100", OCT_10)
    orders_services.cancel_order(unit.order, user)
    still_open = _line(user, customer, item)
    shipped(user, customer, still_open, "100", OCT_10)

    for line in (done, unit, still_open):
        with pytest.raises(ValueError) as caught:
            services.create_invoice(customer, [], user, issued_on=NOV_30, lumps=[(line, "1000")])
        assert not isinstance(caught.value, RuleViolation)
        assert "は、出し切らずに終えた一式の明細ではありません。出荷の行で請求してください。" in str(caught.value)
    assert Invoice.objects.count() == 0


# ---------------------------------------------------------------- (e) 納品書と得意先の R09


@pytest.mark.django_db
def test_lump_without_issued_note_or_other_customer_is_r09(user, customer, item):
    no_note = _line(user, customer, item)
    shipping_services.ship(no_note, Decimal("100"), OCT_10, user)
    orders_services.cancel_order(no_note.order, user)
    other = _customer(user, code="C002")
    theirs = _cancelled_lump(user, other, item)

    with pytest.raises(RuleViolation) as caught:
        services.create_invoice(customer, [], user, issued_on=NOV_30, lumps=[(no_note, "1000")])
    assert "1 行目の出荷が発行の納品書に入っていないので、外してください。" in str(caught.value)
    with pytest.raises(RuleViolation) as caught:
        services.create_invoice(customer, [], user, issued_on=NOV_30, lumps=[(theirs, "1000")])
    assert "請求書は得意先ごとに作るので、受注" in str(caught.value)
    assert Invoice.objects.count() == 0


# ---------------------------------------------------------------- (f) 金額は 0 より大きい数 (C107-3)


@pytest.mark.django_db
@pytest.mark.parametrize("amount", ["-1", "0", "0.4", "abc"])
def test_lump_amount_must_be_positive(user, customer, item, amount):
    line = _cancelled_lump(user, customer, item)

    with pytest.raises(ValueError) as caught:
        services.create_invoice(customer, [], user, issued_on=NOV_30, lumps=[(line, amount)])

    assert str(caught.value) == (
        f"発行できません。一式の金額が 0 以下か、数として読めません (受注 {line.order.order_no} の 1 行目)。"
        "0 より大きい数を入れてください。"
    )
    assert Invoice.objects.count() == 0


@pytest.mark.django_db
def test_post_zero_amount_stops_with_field_error(client, user, customer, item):
    line = _cancelled_lump(user, customer, item)
    client.force_login(user)

    response = _post(client, customer, {f"lump-{line.pk}-pick": "on", f"lump-{line.pk}-amount": "0"})

    body = response.content.decode()
    assert response.status_code == 200
    assert "一式の金額は 0 より大きい数にしてください。" in body
    assert 'data-notice="stop"' in body
    assert Invoice.objects.count() == 0


# ---------------------------------------------------------------- (g) 一式の表から外れ、取消で戻る


@pytest.mark.django_db
def test_billed_lump_leaves_and_returns(user, customer, item):
    line = _cancelled_lump(user, customer, item)
    invoice = services.create_invoice(customer, [], user, issued_on=NOV_30, lumps=[(line, "16667")])

    assert services.unbilled_lumps() == []
    services.cancel_invoice(invoice, user)
    assert services.unbilled_lumps() == [line]


# ---------------------------------------------------------------- (h) 1 期間 1 枚と作り直し


@pytest.mark.django_db
def test_lump_only_in_same_period_is_r23_and_reissue_adds_lump(user, customer, item):
    lump = _cancelled_lump(user, customer, item)
    unit = _line(user, customer, item, qty="10", price_basis="unit", price="1000")
    shipment = shipped(user, customer, unit, "4", NOV_05)
    first = services.create_invoice(customer, [shipment], user, issued_on=NOV_30)

    with pytest.raises(RuleViolation) as caught:
        services.create_invoice(customer, [], user, issued_on=NOV_30, lumps=[(lump, "16667")])
    assert caught.value.code == "R23"

    plan = services.reissue_plan(first)
    assert plan["shipments"] == [shipment] and plan["lumps"] == []
    second = services.reissue_invoice(first, user, issued_on=NOV_30, lumps=[(lump, Decimal("16667"))])
    rows = list(second.lines.order_by("pk"))
    assert [(row.shipment_id, row.order_line_id, row.amount) for row in rows] == [
        (shipment.pk, unit.pk, Decimal("4000")),
        (None, lump.pk, Decimal("16667")),
    ]
    assert second.saved_message == (
        f"{first.invoice_no} を取り消し、この期間の出荷 1 件と一式の行 1 件で {second.invoice_no} を作りました。"
    )

    # 取り消した請求書に一式の行があっても、同じ金額で写る (一式の行は出荷の集まりに入れない)。
    plan = services.reissue_plan(second)
    assert [row.order_line_id for row in plan["lumps"]] == [lump.pk]
    third = services.reissue_invoice(second, user, issued_on=NOV_30)
    lump_row = third.lines.get(shipment__isnull=True)
    assert (lump_row.order_line_id, lump_row.qty, lump_row.amount, lump_row.last_shipped_at) == (
        lump.pk,
        Decimal("100"),
        Decimal("16667"),
        OCT_10,
    )
    assert third.lines.filter(shipment=shipment).exists()
    assert (third.period_from, third.period_to) == (first.period_from, first.period_to)


@pytest.mark.django_db
def test_reissue_of_lump_only_invoice_keeps_period(user, customer, item):
    lump = _cancelled_lump(user, customer, item)
    first = services.create_invoice(customer, [], user, issued_on=NOV_30, lumps=[(lump, "16667")])

    second = services.reissue_invoice(first, user, issued_on=date(2026, 12, 15))

    assert (second.period_from, second.period_to) == (first.period_from, first.period_to)
    assert second.lines.get().amount == Decimal("16667")


# ---------------------------------------------------------------- (i) 画面


@pytest.mark.django_db
def test_post_with_lump_saves(client, user, customer, item):
    line = _cancelled_lump(user, customer, item)
    client.force_login(user)

    response = _post(client, customer, {f"lump-{line.pk}-pick": "on", f"lump-{line.pk}-amount": "16,667"})

    invoice = Invoice.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("billing:invoice_detail", args=[invoice.pk])
    assert invoice.lines.get().amount == Decimal("16667")


@pytest.mark.django_db
def test_post_pick_without_amount_stops(client, user, customer, item):
    line = _cancelled_lump(user, customer, item)
    client.force_login(user)

    response = _post(client, customer, {f"lump-{line.pk}-pick": "on"})

    body = response.content.decode()
    assert response.status_code == 200
    assert "金額を入れてください。" in body
    assert 'data-notice="stop"' in body
    assert Invoice.objects.count() == 0


@pytest.mark.django_db
def test_post_without_shipments_or_lumps_stops(client, user, customer, item):
    _cancelled_lump(user, customer, item)
    client.force_login(user)

    response = _post(client, customer, {})

    assert response.status_code == 200
    assert response.context["form"].errors["shipments"] == ["出荷か一式の行を 1 つ以上選んでください。"]
    assert Invoice.objects.count() == 0


@pytest.mark.django_db
def test_totals_count_picked_lump(client, user, customer, item):
    line = _cancelled_lump(user, customer, item)
    client.force_login(user)

    # 発行日を空にして止め、描き直した合計の枠と保存のボタンの字を見る。
    response = client.post(
        reverse("billing:invoice_create"),
        {"customer": customer.pk, "issued_on": "", f"lump-{line.pk}-pick": "on", f"lump-{line.pk}-amount": "16667"},
    )

    body = response.content.decode()
    assert re.search(r"data-line-totals-rows>1</span>", body)
    assert re.search(r"data-line-totals-qty>100</span>", body)
    assert re.search(r"data-line-totals-amount>16,667</span>", body)
    assert "1 行・税抜 ¥16,667 の請求書を作る" in html.unescape(body)
    assert 'data-label-pick-amount="16667"' in body
    assert 'data-label-pick-qty="100"' in body


@pytest.mark.django_db
def test_create_form_has_lump_fields_only_with_money(settings, client, user, customer, item):
    line = _cancelled_lump(user, customer, item)
    client.force_login(user)

    body = client.get(reverse("billing:invoice_create")).content.decode()
    assert f'name="lump-{line.pk}-amount"' in body
    assert f'name="lump-{line.pk}-pick"' in body
    assert 'data-fill="50000"' in body and 'data-fill="16667"' in body
    assert "金額を決めて「載せる」に印を付けると、この請求書に 1 行で載ります。" in body

    settings.SEIZOU = {"access_control": dict(NO_MONEY)}
    body = client.get(reverse("billing:invoice_create")).content.decode()
    assert f"lump-{line.pk}-amount" not in body
    assert f"lump-{line.pk}-pick" not in body
    assert "金額を見られる人が請求に載せます。" in body


# ---------------------------------------------------------------- (j) 詳細と印刷


@pytest.mark.django_db
def test_detail_and_print_show_lump_line(client, user, customer, item):
    line = _cancelled_lump(user, customer, item)
    invoice = services.create_invoice(customer, [], user, issued_on=NOV_30, lumps=[(line, "16667")])
    client.force_login(user)

    detail = html.unescape(client.get(reverse("billing:invoice_detail", args=[invoice.pk])).content.decode())
    text = re.sub(r"<[^>]+>", "", detail)
    assert f"受注 {line.order.order_no}-1 の一式" in text
    assert "2026-10-10" in text
    assert "16,667 / 式" in text

    printed = html.unescape(client.get(reverse("billing:invoice_print", args=[invoice.pk])).content.decode())
    assert f"受注 {line.order.order_no}-1 の一式" in printed
    assert "/ 式" in printed


# ---------------------------------------------------------------- (k) 受注の詳細の請求書のタブと流れ


@pytest.mark.django_db
def test_order_reaches_lump_only_invoice(user, customer, item):
    line = _cancelled_lump(user, customer, item)
    invoice = services.create_invoice(customer, [], user, issued_on=NOV_30, lumps=[(line, "16667")])

    assert list(line.order.invoices) == [invoice]
    titles = [event.title for event in timeline.events_for(line.order)]
    assert any(invoice.invoice_no in title for title in titles)
    assert 7 in timeline.reached_stages(line)


# ---------------------------------------------------------------- (l) 一式だけが残った得意先


@pytest.mark.django_db
def test_customer_with_only_lump_can_be_chosen(client, user, customer, item):
    line = _line(user, customer, item)
    shipment = shipped(user, customer, line, "100", OCT_10)
    services.create_invoice(customer, [shipment], user, issued_on=date(2026, 10, 31))
    orders_services.cancel_order(line.order, user)
    assert not services.billable_shipments(customer).exists()
    client.force_login(user)

    body = client.get(reverse("billing:invoice_create")).content.decode()
    assert f'<option value="{customer.pk}"' in body
    assert f'data-lump-row data-partner="{customer.pk}"' in body

    response = _post(client, customer, {f"lump-{line.pk}-pick": "on", f"lump-{line.pk}-amount": "16667"})
    assert response.status_code == 302
    created = Invoice.objects.order_by("-pk").first()
    assert created.lines.get().order_line_id == line.pk
