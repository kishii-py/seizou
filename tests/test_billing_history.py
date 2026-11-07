"""請求書と明細と入金の変更の履歴 (決め HIST-1 の伝票の「請求書」「入金」。C106 の その 7)。

請求の書く関数 (create_invoice・cancel_invoice・reissue_invoice・record_payment・record_outside_payment・cancel_payment) が、
請求書の詳細の「履歴」に作った・取り消したを残すこと、請求書の外で受け取った入金 (決め C107-3c) は出荷の詳細の「履歴」に出て
どの請求書の履歴にも出ないこと、金額の閲覧の無い人に入金の金額が「(非表示)」と出ること、詳細の問い合わせの数が履歴の行の数で
増えないことを見る。データは作り物だけ。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.billing import services as billing_services
from seizou.core.models import ChangeLog
from seizou.core.services import record_history
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

ISSUED_ON = date(2026, 11, 30)
PAID_ON = date(2026, 12, 10)
MONEY_ON = {"department": "off", "write": "off", "money": "on"}
PASSWORD = "kumo-tests-2611"


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def line(user, customer):
    item = masters_services.create_item({"part_no": "H-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1375")}], user, order_date=date(2026, 11, 2)
    )
    return order.lines.get()


@pytest.fixture
def invoice(user, customer, line):
    return billing_services.create_invoice(customer, [shipped(user, customer, line, "3")], user, issued_on=ISSUED_ON)


def _rows(obj):
    return list(ChangeLog.objects.filter(owner_model=obj._meta.label_lower, owner_pk=obj.pk))


def _history_part(client, url_name, obj):
    body = client.get(reverse(url_name, args=[obj.pk])).content.decode()
    start = body.index('aria-label="履歴"')
    return body[start : body.index("data-last-change", start)]


def _account(login_id):
    accounts_services.issue_account(login_id, PASSWORD, [], [], {}, None)
    return get_user_model().objects.get(username=login_id)


@pytest.mark.django_db
def test_create_invoice_records_invoice_and_lines(invoice):
    rows = _rows(invoice)
    assert sorted((row.model, row.action) for row in rows) == [
        ("billing.invoice", "created"),
        ("billing.invoiceline", "created"),
    ]
    own = next(row for row in rows if row.model == "billing.invoice")
    assert {"field": "total", "label": "合計", "type": "money", "before": "", "after": str(invoice.total.normalize())} in own.changes


@pytest.mark.django_db
def test_payment_created_and_cancelled_on_invoice(settings, client, logged_in, user, invoice):
    payment = billing_services.record_payment(invoice, PAID_ON, Decimal("1000"), user)

    row = _rows(invoice)[0]
    assert (row.model, row.action, row.object_text) == ("billing.payment", "created", str(payment))
    part = _history_part(logged_in, "billing:invoice_detail", invoice)
    assert f"{payment} を作った" in part
    assert "¥1,000" in part

    billing_services.cancel_payment(payment, user)

    row = _rows(invoice)[0]
    assert row.action == "cancelled"
    assert row.changes == [{"field": "status", "label": "状態", "type": "text", "before": "記録", "after": "取消"}]
    assert f"{payment} を取り消した" in _history_part(logged_in, "billing:invoice_detail", invoice)

    settings.SEIZOU = {"access_control": dict(MONEY_ON)}
    client.force_login(_account("genba"))
    part = _history_part(client, "billing:invoice_detail", invoice)
    assert "金額: (非表示)" in part
    assert "1,000" not in part


@pytest.mark.django_db
def test_cancel_invoice_records_cancelled(logged_in, user, invoice):
    billing_services.cancel_invoice(invoice, user)

    row = _rows(invoice)[0]
    assert (row.model, row.action) == ("billing.invoice", "cancelled")
    assert "取り消した" in _history_part(logged_in, "billing:invoice_detail", invoice)


@pytest.mark.django_db
def test_reissue_cancels_old_and_creates_new(user, customer, line, invoice):
    other = shipped(user, customer, line, "2")

    created = billing_services.reissue_invoice(invoice, user, issued_on=ISSUED_ON)

    assert _rows(invoice)[0].action == "cancelled"
    assert sorted(row.model for row in _rows(created)) == ["billing.invoice", "billing.invoiceline", "billing.invoiceline"]
    assert other.pk in set(created.lines.values_list("shipment_id", flat=True))


@pytest.mark.django_db
def test_outside_payment_goes_to_shipment_history(logged_in, user, customer, line, invoice):
    shipment = shipped(user, customer, line, "2")
    count = ChangeLog.objects.filter(owner_model="billing.invoice").count()

    payment = billing_services.record_outside_payment(shipment, PAID_ON, Decimal("2750"), user)

    rows = [row for row in _rows(shipment) if row.model == "billing.payment"]
    assert [(row.action, row.object_text) for row in rows] == [("created", str(payment))]
    assert ChangeLog.objects.filter(owner_model="billing.invoice").count() == count
    assert f"{payment} を作った" in _history_part(logged_in, "shipping:shipment_detail", shipment)

    billing_services.cancel_payment(payment, user)
    assert [row.action for row in _rows(shipment) if row.model == "billing.payment"] == ["cancelled", "created"]


def _queries(client, url) -> int:
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200
    return len(ctx.captured_queries)


@pytest.mark.django_db
def test_detail_queries_do_not_grow_with_history_rows(logged_in, user, customer, line):
    few = billing_services.create_invoice(customer, [shipped(user, customer, line, "1")], user, issued_on=ISSUED_ON)
    other_customer = masters_services.create_partner({"code": "C002", "name": "作り物の得意先 2", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "H-200", "name": "作り物の板", "unit": "枚"}, user)
    other_line = orders_services.create_order(
        other_customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1375")}], user, order_date=date(2026, 11, 2)
    ).lines.get()
    many = billing_services.create_invoice(
        other_customer, [shipped(user, other_customer, other_line, "1")], user, issued_on=ISSUED_ON
    )
    for n in range(10):  # 状態を変えずに、履歴の行 (金額の項目も) だけを増やす
        record_history(many, user, changes=[{"field": "total", "label": "合計", "type": "money", "before": str(n), "after": str(n + 1)}])
    assert len(_rows(many)) > len(_rows(few)) + 5

    few_url = reverse("billing:invoice_detail", args=[few.pk])
    many_url = reverse("billing:invoice_detail", args=[many.pk])
    for url in (few_url, many_url):  # 最初の 1 回の控えを外す
        assert logged_in.get(url).status_code == 200

    assert _queries(logged_in, many_url) == _queries(logged_in, few_url)
