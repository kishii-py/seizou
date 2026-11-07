"""請求書の一覧と詳細の字の直し (精査 A1 の不具合 6・想定外 S10)。

- 一覧の「残」は 3 桁区切りの金額の列 (kind が money。Python の値なので並び替えと合計は無い)。
- 取消の請求書の入金の状態の字は「—」(payment_status は請求書の状態を見ないまま)。
- 取消の請求書の行の「残」は「—」(T03 の直し 11-07。列の値と CSV の残は数のまま。数え方はもう一方の報告の担当)。
fixture と、一覧の行を読む関数は tests/test_billing_payment_views.py の形。得意先・品目は作り物。
"""

import re
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.billing.models import Invoice
from seizou.masters import services as masters_services
from tests.test_billing_payment_views import PASSWORD, RECEIVED_ON, _cell, _cells, _customer, _invoice, _order_line, _panel


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password=PASSWORD)


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def customer(user):
    return _customer(user, closing_day=20, payment_month="next", payment_day=25)


@pytest.fixture
def line(user, customer, item):
    return _order_line(user, customer, item)


@pytest.fixture
def invoice(user, customer, line):
    return _invoice(user, customer, line)


def test_list_balance_is_money_column(client_logged_in, invoice):
    response = client_logged_in.get(reverse("billing:invoice_list"))

    balance = next(spec for spec in response.context["columns"] if spec.name == "balance")
    assert balance.numeric and balance.money
    assert not balance.sortable
    body = response.content.decode()
    assert "16,500" in body
    assert "16500" not in body
    # 列の値は Decimal のまま (字にするのはテンプレート)
    assert _cells(response, invoice.invoice_no)["balance"] == Decimal("16500.00")


def test_cancelled_invoice_payment_label_is_dash(client_logged_in, user, item, invoice):
    billing_services.record_payment(invoice, RECEIVED_ON, Decimal("500"), user)
    # 入金の残る請求書の取消は services が止める (決め 11-04 06:4x)。止める前に取り消した請求書 (古いデータ) は表に直に作る。
    Invoice.objects.filter(pk=invoice.pk).update(status=Invoice.STATUS_CANCELLED)
    cancelled = Invoice.objects.get(pk=invoice.pk)

    assert cancelled.payment_status() == "partial"
    assert cancelled.payment_status_label() == "—"
    assert _cell(client_logged_in.get(reverse("billing:invoice_list")), "payment_status_label") == "—"
    panel = _panel(client_logged_in.get(reverse("billing:invoice_detail", args=[invoice.pk])))
    assert '<span class="badge badge-ghost">—</span>' in panel
    assert "一部入金" not in panel

    # 取り消していない請求書は今までどおり
    other = _customer(user, "C002", "作り物の別の得意先")
    fresh = _invoice(user, other, _order_line(user, other, item))

    assert fresh.payment_status_label() == "未入金"
    panel = _panel(client_logged_in.get(reverse("billing:invoice_detail", args=[fresh.pk])))
    assert '<span class="badge badge-soft badge-warning">未入金</span>' in panel


#: 一覧の枡の「—」(盤の「—」と同じ形。右寄せの薄い字)
DASH_CELL = '<td class="whitespace-nowrap text-right"><span class="text-base-content/60">—</span></td>'


def _row_html(body, pk):
    """一覧の行 (<tr id="row-<pk>" ...>...</tr>) の HTML。"""
    match = re.search(rf'<tr id="row-{pk}".*?</tr>', body, re.S)
    assert match, f"row-{pk} が無い"
    return match.group(0)


def test_cancelled_invoice_balance_cell_is_dash(client_logged_in, user, item, invoice):
    # 取消の請求書の行の「残」は「—」(同じ行の「入金」と同じ)。残の金額が出ると、残の列を縦に読む人に入金待ちに見える (T03)
    billing_services.record_payment(invoice, RECEIVED_ON, Decimal("500"), user)
    Invoice.objects.filter(pk=invoice.pk).update(status=Invoice.STATUS_CANCELLED)
    other = _customer(user, "C002", "作り物の別の得意先")
    fresh = _invoice(user, other, _order_line(user, other, item))

    response = client_logged_in.get(reverse("billing:invoice_list"))
    body = response.content.decode()

    cancelled_row = _row_html(body, invoice.pk)
    assert cancelled_row.count(DASH_CELL) == 1  # 残の枡だけ (入金の「—」は印の無い字)
    assert "16,000" not in cancelled_row  # 残 (16,500 − 500) は出さない
    assert "16,500" in cancelled_row  # 合計はそのまま
    # 取り消していない請求書の残は今までどおり (合計と残の 2 か所に 16,500)
    fresh_row = _row_html(body, fresh.pk)
    assert DASH_CELL not in fresh_row
    assert fresh_row.count("16,500") == 2
    # 列の値 (CSV と集計が読む物) は数のまま。数え方はもう一方の報告の担当なので、ここでは変えない
    assert _cells(response, invoice.invoice_no)["balance"] == Decimal("16000.00")
