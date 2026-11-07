"""請求書の詳細の題の横の入金の札の前に、灰の字「入金」を置く (初見テスト R5 の V91。候補 C17。決め C17-1 = a)。

題の横の札が何の状態の札か (入金の状態) を、開いた直後の窓で読めるようにする。字と札は 1 つの span で包み、
題の行が狭い幅で折れても別の行に離れないようにする (div にすると、試験の _heading の切り出しの外へ札が出る)。
札の色と字は変えない (決め 11-05 16:2x。N164)。取消の請求書は赤の線の「取消」の札だけで、「入金」の字を付けない。
作り物のデータは全部 services で作る。
"""

import re
from datetime import date

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from tests.test_core_status_colors import DOT_OUTLINE, DOT_WARNING, _heading, _invoice, customer, item, no_http  # noqa: F401

RECEIVED_ON = date(2026, 11, 5)
#: 包む span と、灰の字「入金」の span (札の前に置く)
LABEL = '<span class="inline-flex items-center gap-2"><span class="text-base-content/70">入金</span>'
#: 入金済みの札 (点つき。緑。tests/test_core_badge_colors.py と同じ字)
DOT_SUCCESS = '<span class="badge badge-soft badge-success gap-2"><span class="status status-success" aria-hidden="true"></span>{}</span>'


def _labelled(badge: str) -> re.Pattern:
    """「入金」の字の後に札が続き、その後に包む span の閉じがある形 (札の tag が出す空白と改行を挟んでも当たる)。"""
    return re.compile(re.escape(LABEL) + r"\s*" + re.escape(badge) + r"\s*</span>")


@pytest.mark.django_db
def test_issued_invoice_heading_has_payment_label(user, logged_in, customer, item):  # noqa: F811
    invoice = _invoice(user, customer, item)
    url = reverse("billing:invoice_detail", args=[invoice.pk])

    assert _labelled(DOT_WARNING.format("未入金")).search(_heading(logged_in, url))

    billing_services.record_payment(invoice, RECEIVED_ON, invoice.total, user)
    assert _labelled(DOT_SUCCESS.format("入金済み")).search(_heading(logged_in, url))


@pytest.mark.django_db
def test_cancelled_invoice_heading_has_no_payment_label(user, logged_in, customer, item):  # noqa: F811
    invoice = _invoice(user, customer, item)
    billing_services.cancel_invoice(invoice, user)

    heading = _heading(logged_in, reverse("billing:invoice_detail", args=[invoice.pk]))
    assert ">入金</span>" not in heading
    assert DOT_OUTLINE.format("取消") in heading
