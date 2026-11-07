"""受注の詳細の「段の帯」(案 4-A。N148)。段の読み方 (orders.timeline.stage_of・stage_band) と、詳細の画面の帯。

作り物のデータは全部 services で作る。段の読み方は BoardRow (明細ごとに 1 回だけ計算する) を通るので、
指示書や実績や出荷を足した後は、明細を取り直してから段を読む (_line)。
"""

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from seizou.billing import services as billing_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.timeline import STAGES, reached_stages, stage_band, stage_of
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
    progress_services.record_progress(workorder.steps.get(), Decimal("4"), timezone.now(), user)
    assert stage_of(_line(order)) == 3
    # 完了 = 最後の工程に完了の印の付いた実績がある (初見テスト R5 の候補 C103 の PROG-1a)
    progress_services.record_progress(workorder.steps.get(), Decimal("6"), timezone.now(), user, completes_step=True)
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
    # 着手 = 実績が 1 件でもある (予定の数に届かない実績。C27)
    progress_services.record_progress(_issue(user, _line(order, 2), cut).steps.get(), Decimal("4"), timezone.now(), user)

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
    # 指示書なしで出荷したので、✓ は通った受注と出荷だけ。指示・着手・完了は横線 (C06)
    assert band.count('data-content="✓"') == 2
    assert band.count('data-content="−"') == 3
    assert band.count("step-primary") == 3
    assert '<li class="step max-sm:min-w-0 step-primary" aria-current="step">納品</li>' in band
    assert '<li class="step max-sm:min-w-0">請求</li>' in band
    assert "明細 " not in band


@pytest.mark.django_db
def test_summary_appears_when_lines_differ(user, logged_in, customer, item):
    order = _order(user, customer, item, lines=2)
    shipping_services.ship(_line(order, 1), Decimal("10"), timezone.localdate(), user)

    band = _band(_detail(logged_in, order)[1])

    assert 'data-stage-current="1"' in band
    assert '<p class="mt-1 text-xs text-base-content/70">明細 2 行のうち、出荷 1 行・受注 1 行</p>' in band


# --- 通っていない段の印 (C06。今の段より前でも、通っていない段は ✓ でなく灰色の丸に横線) ---------------------


def _done(band):
    return [s.done for s in band.stages]


def _skipped(band):
    return [s.skipped for s in band.stages]


def _li(band_html, name):
    """帯の HTML から、段の名前が name の li の字を取る。"""
    match = re.search(rf"<li [^>]*>{name}</li>", band_html)
    assert match is not None, f"段 {name} の li が無い"
    return match.group()


@pytest.mark.django_db
def test_shipment_without_workorder_skips_making_stages(user, customer, item):
    order = _order(user, customer, item)
    shipping_services.ship(_line(order), Decimal("4"), timezone.localdate(), user)

    band = stage_band(order)

    assert reached_stages(_line(order)) == {1, 5}
    assert (band.current, band.current_name) == (5, "出荷")
    assert _done(band) == [True, False, False, False, False, False, False, False]
    assert _skipped(band) == [False, True, True, True, False, False, False, False]


@pytest.mark.django_db
def test_unaccepted_workorder_then_shipment_marks_only_the_issue(user, customer, item, cut):
    order = _order(user, customer, item)
    _issue(user, _line(order), cut)
    shipping_services.ship(_line(order), Decimal("1"), timezone.localdate(), user)

    band = stage_band(order)

    assert reached_stages(_line(order)) == {1, 2, 5}
    assert band.current == 5
    assert _done(band) == [True, True, False, False, False, False, False, False]
    assert _skipped(band) == [False, False, True, True, False, False, False, False]


@pytest.mark.django_db
def test_stage_is_done_only_when_every_line_passed_it(user, customer, item, cut):
    order = _order(user, customer, item, lines=2)
    workorder = _issue(user, _line(order, 1), cut)
    progress_services.record_progress(workorder.steps.get(), Decimal("10"), timezone.now(), user, completes_step=True)
    today = timezone.localdate()
    shipping_services.ship(_line(order, 1), Decimal("10"), today, user)
    shipping_services.ship(_line(order, 2), Decimal("10"), today, user)

    band = stage_band(order)

    assert [reached_stages(_line(order, no)) for no in (1, 2)] == [{1, 2, 3, 4, 5}, {1, 5}]
    assert (band.current, band.summary) == (5, None)
    # 1 行は指示書から完了まで、1 行は指示書なし → 指示・着手・完了は ✓ を控えて横線
    assert _done(band) == [True, False, False, False, False, False, False, False]
    assert _skipped(band) == [False, True, True, True, False, False, False, False]


@pytest.mark.django_db
def test_line_through_every_stage_has_no_skipped(user, customer, item, cut):
    order = _order(user, customer, item)
    workorder = _issue(user, _line(order), cut)
    progress_services.record_progress(workorder.steps.get(), Decimal("10"), timezone.now(), user, completes_step=True)
    shipping_services.ship(_line(order), Decimal("10"), timezone.localdate(), user)

    band = stage_band(order)

    assert band.current == 5
    assert _done(band) == [True, True, True, True, False, False, False, False]
    assert not any(_skipped(band))


@pytest.mark.django_db
def test_skipped_stage_shows_a_grey_dash_without_primary(user, logged_in, customer, item):
    order = _order(user, customer, item)
    shipping_services.ship(_line(order), Decimal("4"), timezone.localdate(), user)

    band = _band(_detail(logged_in, order)[1])

    assert band.count('data-content="−"') == 3
    for name in ("指示", "着手", "完了"):
        li = _li(band, name)
        assert 'data-content="−"' in li
        for absent in ("step-primary", "aria-current", "✓"):
            assert absent not in li, f"{name} の段に {absent}"
    assert 'data-content="✓"' in _li(band, "受注")
    assert 'aria-current="step"' in _li(band, "出荷")


@pytest.mark.django_db
def test_band_fits_the_phone_width(user, logged_in, customer, item):
    """phone の幅 (sm より狭い) では段の最小幅を外し、全部の段を帯の幅に収める (C06-3 = a)。sm から上は daisyUI の幅のまま。"""
    order = _order(user, customer, item)

    band = _band(_detail(logged_in, order)[1])

    assert '<ul class="steps text-xs max-sm:w-full" aria-label="段の帯">' in band
    lis = re.findall(r"<li [^>]*>", band)
    assert len(lis) == len(STAGES)
    assert all(li.startswith('<li class="step max-sm:min-w-0') for li in lis)
    # 帯の箱 (送る形) はそのまま
    assert '<div id="order-stages" class="mb-6 overflow-x-auto"' in band
