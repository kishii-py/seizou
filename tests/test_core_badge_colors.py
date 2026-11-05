"""印の色の意味を 1 組にそろえる (決め 11-05 16:2x。N164)。

意味の色は 5 つで 1 組: 灰 (ghost) = 作成・下書き・まだ / 青 (info) = 進行中 (受注・発行・送った・相手が受け入れた) /
橙 (warning) = 注意 (一部出荷・未入金・一部入金) / 緑 (success) = 済み (出荷済・入金済み) /
赤の線 (error) = 止まった (取消・断った・捨てた)。
請求書の印は入金の状態 (未入金と一部入金 = 橙、入金済み = 緑、取消 = 赤の線) で、「発行」は印の無い字。
左のメニューの件数の印は、指すと何の数かを吹き出しで出す。
作り物のデータは全部 services で作る。相手の会社に HTTP を送らない (requests.post は呼んだら落とす物に差し替える)。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.billing.models import Invoice
from seizou.core import status_colors
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from tests.test_core_menu_counts import _aside, page
from tests.test_core_status_colors import DOT_OUTLINE, _fake, _heading, _invoice, _render, _tr, customer, item, no_http  # noqa: F401

#: 詳細の見出しの印 (点つき。橙)
DOT_WARNING = '<span class="badge badge-soft badge-warning gap-2"><span class="status status-warning" aria-hidden="true"></span>{}</span>'
RECEIVED_ON = date(2026, 11, 5)


def test_every_value_is_one_of_five_meanings():
    assert status_colors.TONES == ("ghost", "info", "warning", "success", "error")
    for table in (status_colors.STATUS_TONES, status_colors.APP_TONES, status_colors.PAYMENT_TONES):
        assert set(table.values()) <= set(status_colors.TONES), table
    assert set(status_colors.PAYMENT_TONES) == set(Invoice.PAYMENT_LABELS)
    assert status_colors.PAYMENT_TONES == {"unpaid": "warning", "partial": "warning", "paid": "success"}
    for value in ("cancelled", "rejected", "discarded"):
        assert status_colors.tone_for(value) == "error", value
    assert status_colors.tone_for("accepted", "purchasing") == "info"


def test_stopped_states_are_a_red_line():
    cases = (
        (_fake("cancelled", "取消"), "取消"),
        (_fake("rejected", "断った", "connect"), "断った"),
        (_fake("discarded", "捨てた", "intake"), "捨てた"),
    )
    for obj, label in cases:
        html = _render(obj)
        assert html == f'<span class="badge badge-outline badge-error">{label}</span>', label
        assert "text-base-content/50" not in html, label
    assert _render(_fake("cancelled", "取消"), dot=True) == DOT_OUTLINE.format("取消")


@pytest.mark.django_db
def test_invoice_list_marks_payment_state_and_leaves_issued_plain(user, logged_in, customer, item):  # noqa: F811
    unpaid = _invoice(user, customer, item)
    partial = _invoice(user, customer, item)
    billing_services.record_payment(partial, RECEIVED_ON, Decimal("100"), user)
    paid = _invoice(user, customer, item)
    billing_services.record_payment(paid, RECEIVED_ON, paid.total, user)
    cancelled = _invoice(user, customer, item)
    billing_services.cancel_invoice(cancelled, user)

    body = logged_in.get(reverse("billing:invoice_list")).content.decode()
    cases = (
        (unpaid, '<span class="badge badge-soft badge-warning">未入金</span>'),
        (partial, '<span class="badge badge-soft badge-warning">一部入金</span>'),
        (paid, '<span class="badge badge-soft badge-success">入金済み</span>'),
    )
    for invoice, badge in cases:
        row = _tr(body, invoice.pk)
        assert badge in row, invoice.pk
        assert '<td class="whitespace-nowrap">発行</td>' in row, invoice.pk
        assert ">発行</span>" not in row, invoice.pk

    row = _tr(body, cancelled.pk)
    assert '<span class="badge badge-outline badge-error">取消</span>' in row
    assert '<td class="whitespace-nowrap">—</td>' in row
    tbody = body.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert "badge-info" not in tbody


@pytest.mark.django_db
def test_invoice_detail_heading_shows_payment_state(user, logged_in, customer, item):  # noqa: F811
    invoice = _invoice(user, customer, item)
    url = reverse("billing:invoice_detail", args=[invoice.pk])

    heading = _heading(logged_in, url)
    assert DOT_WARNING.format("未入金") in heading
    assert "発行" not in heading

    billing_services.record_payment(invoice, RECEIVED_ON, invoice.total, user)
    heading = _heading(logged_in, url)
    assert (
        '<span class="badge badge-soft badge-success gap-2"><span class="status status-success" aria-hidden="true"></span>入金済み</span>'
        in heading
    )
    body = logged_in.get(url).content.decode()
    panel = body[body.index('id="payments"') :]
    assert '<span class="badge badge-soft badge-success">入金済み</span>' in panel

    other = _invoice(user, customer, item)
    billing_services.cancel_invoice(other, user)
    assert DOT_OUTLINE.format("取消") in _heading(logged_in, reverse("billing:invoice_detail", args=[other.pk]))


@pytest.mark.django_db
def test_menu_count_says_what_it_counts(user, logged_in, customer, item):  # noqa: F811
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}], user, order_date=date(2026, 11, 2)
    )
    shipment = shipping_services.ship(order.lines.get(), Decimal("10"), date(2026, 11, 3), user)
    shipping_services.create_delivery_note(customer, [shipment], user)  # 請求書は作らない = 請求書に入っていない出荷が 1

    aside = _aside(page(logged_in, reverse("core:home")))
    assert (
        '<span class="tooltip tooltip-left ml-auto text-xs" data-tip="請求書に入っていない出荷 1 件">'
        '<span class="badge badge-soft badge-primary badge-sm">1</span></span>'
    ) in aside
    assert 'title="請求書に入っていない出荷' not in aside
