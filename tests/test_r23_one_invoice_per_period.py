"""R23: 同じ得意先の同じ締め期間の請求書は 1 枚だけ (請求の仕方が「都度」の得意先は除く)。billing.services.create_invoice が止める。

同じ締め期間 = 期間の初めと終わりが同じ。2 枚目は止めて、止めの例外に作り直しの画面 (billing:invoice_reissue) の link を付ける。
1 枚目を取り消した後・別の締め期間・別の得意先・都度の得意先は作れる。同じ出荷を 2 回入れたときは今のまま R09 (R09 を先に見る)。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.billing import services
from seizou.billing.models import Invoice, InvoiceLine
from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped as _shipped

ORDER_DATE = date(2026, 11, 2)
ISSUED_ON = date(2026, 11, 30)
R23_RULE = "同じ得意先の同じ締め期間の請求書は 1 枚だけ。"
NOVEMBER = "2026-11-01〜2026-11-30"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _customer(user, code, name, **extra):
    data = {"code": code, "name": name, "role": "customer", "closing_day": 31, **extra}
    return masters_services.create_partner(data, user)


def _line(user, customer, item, qty="100"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.fixture
def customer(user):
    return _customer(user, "C001", "作り物の得意先")


@pytest.fixture
def line(user, customer, item):
    return _line(user, customer, item)


@pytest.mark.django_db
def test_r23_second_invoice_in_same_period_is_refused(user, customer, line):
    first_shipment = _shipped(user, customer, line, "4", date(2026, 11, 5))
    second_shipment = _shipped(user, customer, line, "6", date(2026, 11, 10))
    first = services.create_invoice(customer, [first_shipment], user, issued_on=ISSUED_ON)

    with pytest.raises(RuleViolation) as caught:
        services.create_invoice(customer, [second_shipment], user, issued_on=ISSUED_ON)

    assert caught.value.code == "R23"
    assert caught.value.message.startswith("発行できません。" + R23_RULE)
    assert first.invoice_no in caught.value.message
    assert NOVEMBER in caught.value.message
    assert customer.name in caught.value.message
    assert "¥" not in caught.value.message
    assert Invoice.objects.count() == 1
    assert not InvoiceLine.objects.filter(shipment=second_shipment).exists()


@pytest.mark.django_db
def test_r23_refusal_links_to_reissue_of_first(user, customer, line):
    first_shipment = _shipped(user, customer, line, "4", date(2026, 11, 5))
    second_shipment = _shipped(user, customer, line, "6", date(2026, 11, 10))
    first = services.create_invoice(customer, [first_shipment], user, issued_on=ISSUED_ON)

    with pytest.raises(RuleViolation) as caught:
        services.create_invoice(customer, [second_shipment], user, issued_on=ISSUED_ON)

    assert caught.value.link == (
        "billing:invoice_reissue",
        first.pk,
        f"{first.invoice_no} を取り消して、この期間の全部の出荷で作り直す",
    )


@pytest.mark.django_db
def test_r23_lists_every_invoice_already_in_period(user, customer, line):
    """決まりの前に作った 2 枚 (同じ期間) があれば、番号を「・」で並べ、link は pk の小さい方。"""
    a = _shipped(user, customer, line, "1", date(2026, 11, 5))
    b = _shipped(user, customer, line, "1", date(2026, 12, 5))
    c = _shipped(user, customer, line, "1", date(2026, 11, 20))
    first = services.create_invoice(customer, [a], user, issued_on=ISSUED_ON)
    second = services.create_invoice(customer, [b], user, issued_on=date(2026, 12, 31))
    Invoice.objects.filter(pk=second.pk).update(period_from=first.period_from, period_to=first.period_to)

    with pytest.raises(RuleViolation) as caught:
        services.create_invoice(customer, [c], user, issued_on=ISSUED_ON)

    assert f"{first.invoice_no}・{second.invoice_no}" in caught.value.message
    assert caught.value.link[1] == first.pk


@pytest.mark.django_db
def test_r23_after_cancel_can_create(user, customer, line):
    first_shipment = _shipped(user, customer, line, "4", date(2026, 11, 5))
    second_shipment = _shipped(user, customer, line, "6", date(2026, 11, 10))
    first = services.create_invoice(customer, [first_shipment], user, issued_on=ISSUED_ON)
    services.cancel_invoice(first, user)

    again = services.create_invoice(customer, [second_shipment], user, issued_on=ISSUED_ON)

    assert again.status == Invoice.STATUS_ISSUED
    assert (again.period_from, again.period_to) == (first.period_from, first.period_to)


@pytest.mark.django_db
def test_r23_other_period_can_create(user, customer, line):
    november = _shipped(user, customer, line, "4", date(2026, 11, 5))
    december = _shipped(user, customer, line, "6", date(2026, 12, 3))
    services.create_invoice(customer, [november], user, issued_on=ISSUED_ON)

    later = services.create_invoice(customer, [december], user, issued_on=date(2026, 12, 31))

    assert (later.period_from, later.period_to) == (date(2026, 12, 1), date(2026, 12, 31))
    assert Invoice.objects.filter(status=Invoice.STATUS_ISSUED).count() == 2


@pytest.mark.django_db
def test_r23_other_customer_same_period_can_create(user, customer, item, line):
    other = _customer(user, "C002", "作り物の別の得意先")
    other_line = _line(user, other, item)
    mine = _shipped(user, customer, line, "4", date(2026, 11, 5))
    theirs = _shipped(user, other, other_line, "3", date(2026, 11, 5))
    services.create_invoice(customer, [mine], user, issued_on=ISSUED_ON)

    created = services.create_invoice(other, [theirs], user, issued_on=ISSUED_ON)

    assert created.customer_id == other.pk


@pytest.mark.django_db
def test_r23_per_delivery_customer_is_not_refused(user, item):
    cash = _customer(user, "C003", "作り物の都度の得意先", billing_method="per_delivery", closing_day=None)
    cash_line = _line(user, cash, item)
    first_shipment = _shipped(user, cash, cash_line, "1", date(2026, 11, 5))
    second_shipment = _shipped(user, cash, cash_line, "1", date(2026, 11, 5))
    first = services.create_invoice(cash, [first_shipment], user, issued_on=ISSUED_ON)

    second = services.create_invoice(cash, [second_shipment], user, issued_on=ISSUED_ON)

    assert (first.period_from, first.period_to) == (second.period_from, second.period_to)
    assert Invoice.objects.filter(customer=cash, status=Invoice.STATUS_ISSUED).count() == 2


@pytest.mark.django_db
def test_r23_same_shipment_twice_is_still_r09(user, customer, line):
    """同じ出荷を 2 回入れたときは R09 で断る (R09 を先に見る。R23 の番号にしない)。"""
    first_shipment = _shipped(user, customer, line, "4", date(2026, 11, 5))
    second_shipment = _shipped(user, customer, line, "6", date(2026, 11, 10))
    services.create_invoice(customer, [first_shipment], user, issued_on=ISSUED_ON)

    with pytest.raises(RuleViolation) as caught:
        services.create_invoice(customer, [first_shipment, second_shipment], user, issued_on=ISSUED_ON)

    assert caught.value.code == "R09"


def test_r23_refuser_states_its_reason():
    doc = services._refuse_second_in_period.__doc__
    reason = [row.strip() for row in doc.splitlines() if row.strip().startswith("訳:")]
    assert reason and "出所 =" in reason[0] and "R23" in reason[0]


# ---------------------------------------------------------------- 作り直し (止めの link の先。services.reissue_invoice)


@pytest.mark.django_db
def test_reissue_merges_first_and_left_shipments(user, customer, item, line):
    """1 枚目を作り直すと、1 枚目は取消になり、1 枚目の出荷と期間の中で残っていた出荷で 1 枚になる。期間は 1 枚目と同じ。"""
    first_shipment = _shipped(user, customer, line, "4", date(2026, 11, 5))
    left = _shipped(user, customer, line, "6", date(2026, 11, 10))
    december = _shipped(user, customer, line, "1", date(2026, 12, 3))
    other = _customer(user, "C002", "作り物の別の得意先")
    theirs = _shipped(user, other, _line(user, other, item), "3", date(2026, 11, 7))
    first = services.create_invoice(customer, [first_shipment], user, issued_on=ISSUED_ON)

    created = services.reissue_invoice(first, user, issued_on=ISSUED_ON)

    first.refresh_from_db()
    assert first.status == Invoice.STATUS_CANCELLED
    assert created.status == Invoice.STATUS_ISSUED
    assert {row.shipment_id for row in created.lines.all()} == {first_shipment.pk, left.pk}
    assert december.pk not in {row.shipment_id for row in created.lines.all()}
    assert theirs.pk not in {row.shipment_id for row in created.lines.all()}
    assert (created.period_from, created.period_to) == (first.period_from, first.period_to)
    assert created.saved_message == f"{first.invoice_no} を取り消し、この期間の出荷 2 件で {created.invoice_no} を作りました。"
    assert Invoice.objects.filter(customer=customer, status=Invoice.STATUS_ISSUED).count() == 1


@pytest.mark.django_db
def test_reissue_keeps_earlier_period_shipment_of_first(user, customer, line):
    """1 枚目に前の締め期間の日付の出荷が入っていても (31 締めで 10-28 と 11-05 の 11 月分)、作り直しで落ちない。期間は 11 月のまま。"""
    october = _shipped(user, customer, line, "1", date(2026, 10, 28))
    november = _shipped(user, customer, line, "1", date(2026, 11, 5))
    left = _shipped(user, customer, line, "1", date(2026, 11, 10))
    first = services.create_invoice(customer, [october, november], user, issued_on=ISSUED_ON)
    assert (first.period_from, first.period_to) == (date(2026, 11, 1), date(2026, 11, 30))

    created = services.reissue_invoice(first, user, issued_on=ISSUED_ON)

    assert {row.shipment_id for row in created.lines.all()} == {october.pk, november.pk, left.pk}
    assert (created.period_from, created.period_to) == (date(2026, 11, 1), date(2026, 11, 30))


@pytest.mark.django_db
def test_reissue_merges_every_invoice_in_period(user, customer, line):
    """決まりの前に作った同じ期間の 2 枚 (作り物) は、どちらから作り直しても 2 枚とも取消になり 1 枚にまとまる。"""
    a = _shipped(user, customer, line, "1", date(2026, 11, 5))
    b = _shipped(user, customer, line, "1", date(2026, 10, 5))
    c = _shipped(user, customer, line, "1", date(2026, 11, 20))
    first = services.create_invoice(customer, [a], user, issued_on=ISSUED_ON)
    second = services.create_invoice(customer, [b], user, issued_on=ISSUED_ON)
    Invoice.objects.filter(pk=second.pk).update(period_from=first.period_from, period_to=first.period_to)
    second.refresh_from_db()

    created = services.reissue_invoice(second, user, issued_on=ISSUED_ON)

    assert set(Invoice.objects.filter(pk__in=[first.pk, second.pk]).values_list("status", flat=True)) == {
        Invoice.STATUS_CANCELLED
    }
    assert {row.shipment_id for row in created.lines.all()} == {a.pk, b.pk, c.pk}
    assert (created.period_from, created.period_to) == (first.period_from, first.period_to)
    assert created.saved_message.startswith(f"{first.invoice_no}・{second.invoice_no} を取り消し、この期間の出荷 3 件で ")


@pytest.mark.django_db
def test_reissue_refused_while_payment_remains(user, customer, line):
    """どれかの請求書に取消でない入金が残っていれば ValueError (字は cancel_refusal と同じ)。何も変わらない。"""
    first_shipment = _shipped(user, customer, line, "4", date(2026, 11, 5))
    _shipped(user, customer, line, "6", date(2026, 11, 10))
    first = services.create_invoice(customer, [first_shipment], user, issued_on=ISSUED_ON)
    services.record_payment(first, date(2026, 12, 1), Decimal("100"), user)

    with pytest.raises(ValueError) as caught:
        services.reissue_invoice(first, user, issued_on=ISSUED_ON)

    assert str(caught.value) == services.cancel_refusal(Invoice.objects.get(pk=first.pk))
    assert Invoice.objects.count() == 1
    assert Invoice.objects.get(pk=first.pk).status == Invoice.STATUS_ISSUED


@pytest.mark.django_db
def test_reissue_plan_lists_cancelled_and_left_shipments(user, customer, line):
    """作り直しの中身 (画面が読む): 取り消す請求書・入る出荷 (1 枚目の出荷も)・理由の文。金額の閲覧の無い人の理由は件数だけ。"""
    first_shipment = _shipped(user, customer, line, "4", date(2026, 11, 5))
    left = _shipped(user, customer, line, "6", date(2026, 11, 10))
    first = services.create_invoice(customer, [first_shipment], user, issued_on=ISSUED_ON)

    plan = services.reissue_plan(first)
    assert [row.pk for row in plan["invoices"]] == [first.pk]
    assert [row.pk for row in plan["shipments"]] == [first_shipment.pk, left.pk]
    assert plan["refusal"] == "" and plan["refused"] is None

    services.record_payment(first, date(2026, 12, 1), Decimal("100"), user)
    hidden = services.reissue_plan(first, hide_money=True)
    assert hidden["refusal"] == services.cancel_refusal(first, hide_money=True)
    assert "合計" not in hidden["refusal"]
    assert hidden["refused"].pk == first.pk
