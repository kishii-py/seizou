"""入金の記録の頭の「残」の右の「残の全額を入れる」(初見テスト R5 の V146・候補 C62)。

ボタンは頭の 1 行の、金額の閲覧がある人だけの枝の中にあり、残が 0 より大きいときだけ出る。
押すと billing/payment-fill.js が、ボタンの data-fill-amount の字 (comma の形) を同じカードの金額の欄に入れる。保存はしない。
作り物の組み方は tests/test_billing_payment_views.py の関数を借りる (test_ で始まる名前は import しない)。
"""

import re
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse

import seizou.billing
from seizou.billing import services
from seizou.billing.models import Invoice, Payment
from seizou.masters import services as masters_services
from tests.test_billing_payment_views import RECEIVED_ON, _customer, _invoice, _order_line, account

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
JS = Path(seizou.billing.__file__).resolve().parent / "static" / "billing" / "payment-fill.js"
LABEL = ">残の全額を入れる</button>"


@pytest.fixture
def on(settings):
    settings.SEIZOU = {"access_control": {"department": "on", "write": "on", "money": "on"}}


@pytest.fixture
def invoice(user):
    """合計 16,500 の請求書 (出荷 4 と 6。tests/test_billing_payment_views.py の _invoice)。"""
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    customer = _customer(user, closing_day=20, payment_month="next", payment_day=25)
    return _invoice(user, customer, _order_line(user, customer, item))


def _url(invoice):
    return reverse("billing:payment_create", args=[invoice.pk])


def _fill_button(body):
    """「残の全額を入れる」のボタンの開きの tag。"""
    return re.search(r"<button [^>]*data-fill-amount[^>]*>", body).group(0)


# --- 1. 画面の全体 ---


def test_full_page_has_fill_button_after_balance(client_logged_in, invoice):
    response = client_logged_in.get(_url(invoice))
    body = response.content.decode()

    assert response.status_code == 200
    assert body.count('data-fill-amount="16,500"') == 1
    assert body.count(LABEL) == 1
    tag = _fill_button(body)
    assert 'type="button"' in tag
    assert "btn-xs" in tag
    assert "btn-primary" not in tag
    assert body.count("btn-primary") == 1  # 主色のボタンは保存の 1 つのまま
    assert body.count("billing/payment-fill.js") == 1
    # 金額の欄はカードの包みの中 (包みの始まり < 欄 < 包みの最後の script)
    assert body.index("data-payment-card") < body.index('name="amount"') < body.index("billing/payment-fill.js")


# --- 2. 小窓 ---


def test_modal_has_same_button_and_script_once(client_logged_in, invoice):
    response = client_logged_in.get(_url(invoice), **MODAL)
    body = response.content.decode()

    assert response.status_code == 200
    assert "<html" not in body
    assert body.count('data-fill-amount="16,500"') == 1
    assert body.count(LABEL) == 1
    assert body.count("billing/payment-fill.js") == 1
    assert body.index("data-payment-card") < body.index('name="amount"')


# --- 3. 一部入金の後 ---


def test_button_fills_remaining_after_partial_payment(client_logged_in, user, invoice):
    services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), user)

    body = client_logged_in.get(_url(invoice)).content.decode()

    assert body.count('data-fill-amount="10,000"') == 1
    assert body.count(LABEL) == 1


# --- 4. 残が 0 とマイナス ---


@pytest.mark.parametrize("extra", [Decimal("0"), Decimal("1000")], ids=["paid", "overpaid"])
def test_no_button_when_nothing_remains(client_logged_in, user, invoice, extra):
    services.record_payment(invoice, RECEIVED_ON, invoice.total + extra, user)

    body = client_logged_in.get(_url(invoice)).content.decode()

    assert "data-fill-amount" not in body
    assert "残の全額を入れる" not in body


# --- 5. 金額の閲覧 ---


@pytest.mark.django_db
def test_button_only_for_people_who_see_money(on, client, invoice):
    jimu = account("jimu", permissions=["billing"])
    client.force_login(jimu)
    hidden = client.get(_url(invoice)).content.decode()
    assert "残の全額を入れる" not in hidden
    assert "data-fill-amount" not in hidden

    keiri = account("keiri", permissions=["billing"], see_money=True)
    client.force_login(keiri)
    shown = client.get(_url(invoice)).content.decode()
    assert shown.count('data-fill-amount="16,500"') == 1
    assert shown.count(LABEL) == 1


# --- 6. ボタンが入れる字で送った形 ---


def test_post_with_filled_text_records_full_payment(client_logged_in, invoice):
    post = {"received_on": "2026-12-25", "amount": "16,500", "method": "transfer", "note": ""}

    response = client_logged_in.post(_url(invoice), post)

    assert response.status_code == 302
    assert Payment.objects.get().amount == Decimal("16500")
    assert Invoice.objects.get(pk=invoice.pk).payment_status() == Invoice.PAYMENT_PAID


# --- 7. JS の約束 ---


def test_js_file_keeps_promises():
    text = JS.read_text(encoding="utf-8")
    for word in (
        '"use strict"',
        "window.seizouPaymentFill",
        'document.addEventListener("click"',
        "[data-fill-amount]",
        "[data-payment-card]",
        'input[name="amount"]',
        'new Event("input", { bubbles: true })',
    ):
        assert word in text, word
    for word in (
        "innerHTML",
        "insertAdjacentHTML",
        "fetch(",
        "XMLHttpRequest",
        "classList",
        "className",
        ".focus(",
        "requestSubmit",
        ".submit(",
    ):
        assert word not in text, word


# --- 8. node --check ---


def test_js_passes_node_check():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node が無い (node --check を回せない)")
    result = subprocess.run([node, "--check", str(JS)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
