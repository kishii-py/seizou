"""受注の詳細の「段の帯」(案 4-A。N148)。段の読み方 (orders.timeline.stage_of・stage_band) と、詳細の画面の帯。

作り物のデータは全部 services で作る。段の読み方は BoardRow (明細ごとに 1 回だけ計算する) を通るので、
指示書や実績や出荷を足した後は、明細を取り直してから段を読む (_line)。
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from seizou.billing import services as billing_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.timeline import STAGES, stage_band, stage_of
from seizou.progress import services as progress_services
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def cut(user):
    return masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)


def _order(user, customer, item, lines=1):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")} for _ in range(lines)],
        user,
        order_date=timezone.localdate() - timedelta(days=3),
    )


def _line(order, line_no=1):
    """段を読み直すために明細を取り直す (BoardRow の値は明細ごとに 1 回だけ計算するため)。"""
    return order.lines.get(line_no=line_no)


def _issue(user, line, process, qty="10"):
    return workorders_services.issue_workorder(
        line, Decimal(qty), [{"process": process, "planned_qty": Decimal(qty)}], user
    )


def _detail(client, order):
    response = client.get(reverse("orders:order_detail", args=[order.pk]))
    assert response.status_code == 200
    return response, response.content.decode()


def _band(body):
    start = body.index('<div id="order-stages"')
    return body[start : body.index("</div>", start)]


@pytest.mark.django_db
def test_order_alone_is_at_the_first_stage(user, customer, item):
    order = _order(user, customer, item)

    assert stage_of(_line(order)) == 1
    band = stage_band(order)
    assert (band.current, band.current_name, band.summary) == (1, "受注", None)
    assert [(s.no, s.name, s.done, s.current) for s in band.stages] == [
        (no, name, False, no == 1) for no, name in enumerate(STAGES, start=1)
    ]


@pytest.mark.django_db
def test_stages_advance_from_workorder_to_payment(user, customer, item, cut):
    order = _order(user, customer, item)
    today = timezone.localdate()

    workorder = _issue(user, _line(order), cut)
    assert stage_of(_line(order)) == 2
    workorders_services.accept_workorder(workorder, user)
    assert stage_of(_line(order)) == 3
    progress_services.record_progress(workorder.steps.get(), Decimal("4"), timezone.now(), user)
    assert stage_of(_line(order)) == 3
    progress_services.record_progress(workorder.steps.get(), Decimal("6"), timezone.now(), user)
    assert stage_of(_line(order)) == 4
    shipment = shipping_services.ship(_line(order), Decimal("10"), today, user)
    assert stage_of(_line(order)) == 5
    shipping_services.create_delivery_note(customer, [shipment], user)
    assert stage_of(_line(order)) == 6
    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=today)
    assert stage_of(_line(order)) == 7
    billing_services.record_payment(invoice, today, invoice.total, user)
    assert stage_of(_line(order)) == 8
    assert (stage_band(order).current, stage_band(order).current_name) == (8, "入金")


@pytest.mark.django_db
def test_cancelled_workorder_does_not_count_and_shipment_alone_reaches_shipped(user, customer, item, cut):
    order = _order(user, customer, item)
    workorder = _issue(user, _line(order), cut)
    workorders_services.cancel_workorder(workorder, user, reason="作り物の取消")
    assert stage_of(_line(order)) == 1

    shipping_services.ship(_line(order), Decimal("4"), timezone.localdate(), user)
    assert stage_of(_line(order)) == 5


@pytest.mark.django_db
def test_cancelled_invoice_does_not_count(user, customer, item):
    order = _order(user, customer, item)
    today = timezone.localdate()
    shipment = shipping_services.ship(_line(order), Decimal("10"), today, user)
    shipping_services.create_delivery_note(customer, [shipment], user)
    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=today)
    assert stage_of(_line(order)) == 7

    billing_services.cancel_invoice(invoice, user)

    assert stage_of(_line(order)) == 6


@pytest.mark.django_db
def test_order_stage_is_the_slowest_line_with_a_summary(user, customer, item, cut):
    order = _order(user, customer, item, lines=3)
    shipping_services.ship(_line(order, 1), Decimal("10"), timezone.localdate(), user)
    workorders_services.accept_workorder(_issue(user, _line(order, 2), cut), user)

    band = stage_band(order)

    assert [stage_of(_line(order, no)) for no in (1, 2, 3)] == [5, 3, 1]
    assert (band.current, band.current_name) == (1, "受注")
    assert band.summary == "明細 3 行のうち、出荷 1 行・着手 1 行・受注 1 行"
    assert [s.done for s in band.stages] == [False] * 8


@pytest.mark.django_db
def test_cancelled_order_has_no_band(user, logged_in, customer, item):
    order = orders_services.cancel_order(_order(user, customer, item), user, reason="作り物の取消")

    assert stage_band(order) is None
    assert 'id="order-stages"' not in _detail(logged_in, order)[1]


@pytest.mark.django_db
def test_detail_shows_band_between_heading_and_tabs(user, logged_in, customer, item):
    order = _order(user, customer, item)
    shipment = shipping_services.ship(_line(order), Decimal("10"), timezone.localdate(), user)
    shipping_services.create_delivery_note(customer, [shipment], user)

    response, body = _detail(logged_in, order)

    assert "core/_stage_band.html" in [template.name for template in response.templates]
    assert body.index('<h1 class="truncate') < body.index('<div id="order-stages"') < body.index('role="tablist"')
    band = _band(body)
    assert 'data-stage-current="6"' in band
    assert band.count('data-content="✓"') == 5
    assert band.count("step-primary") == 6
    assert '<li class="step step-primary" aria-current="step">納品</li>' in band
    assert '<li class="step">請求</li>' in band
    assert "明細 " not in band


@pytest.mark.django_db
def test_summary_appears_when_lines_differ(user, logged_in, customer, item):
    order = _order(user, customer, item, lines=2)
    shipping_services.ship(_line(order, 1), Decimal("10"), timezone.localdate(), user)

    band = _band(_detail(logged_in, order)[1])

    assert 'data-stage-current="1"' in band
    assert '<p class="mt-1 text-xs text-base-content/70">明細 2 行のうち、出荷 1 行・受注 1 行</p>' in band
