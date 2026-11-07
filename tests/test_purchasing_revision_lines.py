"""発注の版の番号と EDI のコードの列 (既定の値) と、取り消した明細 (明細状態コード 2) を数から外すこと。

今は画面から 2 の行を作れないので、表に直に 2 を書いて見る。HTTP は止め、つながりの無い協力企業で発注を作る。
取引先と品目は作り物。日は固定の値を渡す。
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.core import dashboard
from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing import timeline
from seizou.purchasing.models import PurchaseOrder, PurchaseOrderLine
from seizou.purchasing.templatetags import purchasing_tags

ORDER_DATE = date(2026, 10, 28)
DAY = date(2026, 11, 2)


@pytest.fixture
def no_http(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor", "rounding": "round"}, user
    )


@pytest.fixture
def items(user):
    return [
        masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user),
        masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user),
    ]


@pytest.fixture
def order(subcontractor, items, user, no_http):
    """明細 2 行の発注。2 行目を表に直に 2 (明細行取消) にする。2 行目の納期のほうが早い (外れたかが分かるように)。"""
    po = services.create_purchase_order(
        subcontractor,
        [
            {"item": items[0], "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": date(2026, 11, 20)},
            {"item": items[1], "qty": Decimal("5"), "unit_price": Decimal("1000"), "due_date": date(2026, 11, 10)},
        ],
        user,
        order_date=ORDER_DATE,
    )
    PurchaseOrderLine.objects.filter(purchase_order=po, line_no=2).update(line_status_code="2")
    return po


def _lines(po):
    return list(po.lines.order_by("line_no"))


def _notice(line, qty, shipment_no):
    return connect_services.record_shipping_notice(
        line, Decimal(qty), DAY, None, message_id=uuid.uuid4(), shipment_no=shipment_no
    )


@pytest.mark.django_db
def test_defaults_are_first_revision_new(subcontractor, items, user, no_http):
    po = services.create_purchase_order(
        subcontractor, [{"item": items[0], "qty": Decimal("1"), "unit_price": Decimal("1")}], user, order_date=ORDER_DATE
    )
    po.refresh_from_db()
    assert (po.revision_no, po.purpose_code) == (1, "9")
    (line,) = _lines(po)
    assert (line.line_status_code, line.line_status_reason_code) == ("39", "")
    assert line.is_cancelled is False


@pytest.mark.django_db
def test_list_totals_leave_out_cancelled_line(order):
    row = services.annotate_order_received(PurchaseOrder.objects.filter(pk=order.pk)).get()
    assert row.ordered_total == Decimal("10")
    assert row.receive_remaining == Decimal("10")


@pytest.mark.django_db
def test_delivered_after_live_lines_received(order, user):
    first, second = _lines(order)
    assert second.is_cancelled
    # 取り消した明細への受入は断らない (現物優先)。
    services.receive(second, Decimal("1"), DAY, user)
    order.refresh_from_db()
    assert order.status == PurchaseOrder.STATUS_DRAFT
    services.receive(first, Decimal("10"), DAY, user)
    order.refresh_from_db()
    assert order.status == PurchaseOrder.STATUS_DELIVERED


@pytest.mark.django_db
def test_select_candidates_leave_out_cancelled_line(order, client_logged_in):
    first, second = _lines(order)
    response = client_logged_in.get(reverse("purchasing:purchaseorderline_select"))
    ids = {item["id"] for item in response.json()["items"]}
    assert first.pk in ids
    assert second.pk not in ids


@pytest.mark.django_db
def test_next_receipt_skips_cancelled_line(order):
    first, second = _lines(order)
    _notice(second, "5", "SH-2")
    assert purchasing_tags.next_receipt(PurchaseOrder.objects.get(pk=order.pk)) is None
    _notice(first, "4", "SH-1")
    receipt = purchasing_tags.next_receipt(PurchaseOrder.objects.get(pk=order.pk))
    assert receipt["line"].pk == first.pk


@pytest.mark.django_db
def test_aside_and_head_chips_leave_out_cancelled_line(order):
    po = PurchaseOrder.objects.get(pk=order.pk)
    chips = {chip["label"]: chip["value"] for chip in purchasing_tags.purchase_order_head_chips(po)}
    assert chips["一番早い納期"] == "2026-11-20"
    assert chips["合計"] == "¥1,000"
    aside = purchasing_tags.purchase_order_aside(po, [])
    cards = {card["title"]: card for card in aside["cards"]}
    assert cards["金額"]["rows"][0]["value"] == Decimal("1000")
    assert cards["日付"]["rows"][1]["value"] == date(2026, 11, 20)


@pytest.mark.django_db
def test_cancelled_order_still_counts_every_line(order):
    PurchaseOrder.objects.filter(pk=order.pk).update(status=PurchaseOrder.STATUS_CANCELLED)
    po = PurchaseOrder.objects.get(pk=order.pk)
    chips = {chip["label"]: chip["value"] for chip in purchasing_tags.purchase_order_head_chips(po)}
    assert chips["合計"] == "¥6,000"


@pytest.mark.django_db
def test_stage_band_ignores_cancelled_line(order, user):
    first, _second = _lines(order)
    services.receive(first, Decimal("10"), DAY, user)
    band = timeline.stage_band(PurchaseOrder.objects.get(pk=order.pk))
    assert band.current == timeline.stage_of(PurchaseOrderLine.objects.get(pk=first.pk))
    assert band.current > 1


@pytest.mark.django_db
def test_detail_marks_cancelled_line_without_receipt_link(order, client_logged_in):
    first, second = _lines(order)
    _notice(second, "5", "SH-2")
    body = client_logged_in.get(reverse("purchasing:purchaseorder_detail", args=[order.pk])).content.decode()
    assert '<span class="badge badge-soft badge-ghost">取消</span>' in body
    assert f"?line={second.pk}&amp;" not in body
    assert f"?line={first.pk}&amp;" in body


@pytest.mark.django_db
def test_dashboard_lines_to_receive_leave_out_cancelled_line(order):
    first, second = _lines(order)
    _notice(second, "5", "SH-2")
    assert dashboard._noticed_lines_to_receive().count() == 0
    _notice(first, "4", "SH-1")
    assert list(dashboard._noticed_lines_to_receive().values_list("pk", flat=True)) == [first.pk]
