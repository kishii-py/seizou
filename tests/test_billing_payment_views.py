"""入金の画面 (core の土台の上)。請求書の詳細の「入金」の枠・入金の記録・入金の取消・移るだけの口・一覧の列と「残のある物だけ」。

金額の閲覧 (N36) は 2 つの隠し方を見る: 一覧の「残」の列は土台が名前で隠し、枠と入力の画面の字はテンプレートが隠す。
入金の記録は金額が主な登録なので、金額の閲覧が無い人は保存で断られる。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.billing import services
from seizou.billing.models import Payment
from seizou.core import access
from seizou.core.screens import all_screens
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services

User = get_user_model()

PASSWORD = "kumo-tests-2611"
ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
RECEIVED_ON = date(2026, 12, 25)
ALL_ON = {"department": "on", "write": "on", "money": "on"}
POST = {"received_on": "2026-12-25", "amount": "6500", "method": "transfer", "note": "作り物の入金"}


@pytest.fixture
def on(settings):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}


@pytest.fixture
def user(db):
    return User.objects.create_user("tester", password=PASSWORD)


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _customer(user, code="C001", name="作り物の得意先", **terms):
    return masters_services.create_partner({"code": code, "name": name, "role": "customer", **terms}, user)


def _order_line(user, customer, item):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


def _shipped(user, customer, line, qty):
    """納品書の出た出荷 (出荷済み) を 1 つ作る。"""
    shipment = shipping_services.ship(line, Decimal(qty), SHIPPED_AT, user)
    shipping_services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    return shipment


def _invoice(user, customer, line):
    """出荷 4 と 6 の請求書 (小計 15,000・消費税 1,500・合計 16,500)。"""
    first = _shipped(user, customer, line, "4")
    second = _shipped(user, customer, line, "6")
    return services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)


@pytest.fixture
def customer(user):
    return _customer(user, closing_day=20, payment_month="next", payment_day=25)


@pytest.fixture
def line(user, customer, item):
    return _order_line(user, customer, item)


@pytest.fixture
def invoice(user, customer, line):
    return _invoice(user, customer, line)


def account(login_id, *, departments=(), permissions=(), **flags):
    accounts_services.issue_account(login_id, PASSWORD, list(departments), list(permissions), flags, None)
    return User.objects.get(username=login_id)


def _panel(response):
    """請求書の詳細の「入金」の枠の HTML (枠の始まりから終わりまで)。"""
    body = response.content.decode()
    start = body.index('<section id="payments"')
    return body[start : body.index("</section>", start)]


def _cells(response, invoice_no):
    """一覧の 1 行の {列の名前: 値} (行は請求書番号で引く)。"""
    names = [spec.name for spec in response.context["columns"]]
    for row in response.context["rows"]:
        values = dict(zip(names, (cell["value"] for cell in row["cells"]), strict=True))
        if values["invoice_no"] == invoice_no:
            return values
    raise AssertionError(f"一覧に {invoice_no} の行が無い")


def _cell(response, column_name):
    names = [spec.name for spec in response.context["columns"]]
    return response.context["rows"][0]["cells"][names.index(column_name)]["value"]


def _csv_header(response):
    return response.content.decode("utf-8-sig").splitlines()[0].split(",")


def test_detail_shows_payment_panel_and_button(client_logged_in, invoice):
    response = client_logged_in.get(reverse("billing:invoice_detail", args=[invoice.pk]))

    body = response.content.decode()
    assert response.status_code == 200
    assert 'id="payments"' in body
    panel = _panel(response)
    assert "未入金" in panel
    assert "16,500" in panel
    assert reverse("billing:payment_create", args=[invoice.pk]) in panel
    assert "入金は、まだ記録されていません。" in panel
    assert "「入金を記録する」から 1 件目を記録します。" in panel
    assert body.count("btn-primary") == 1


def test_create_get_shows_invoice_summary(client_logged_in, invoice):
    response = client_logged_in.get(reverse("billing:payment_create", args=[invoice.pk]))

    body = response.content.decode()
    assert response.status_code == 200
    assert invoice.invoice_no in body
    assert "作り物の得意先" in body
    assert "残" in body and "16,500" in body
    for name in ("received_on", "amount", "method", "note"):
        assert f'name="{name}"' in body
    for label in ("振込", "現金", "手形", "その他"):
        assert f">{label}</option>" in body
    assert "キャンセル" in body
    assert response.context["cancel_url"] == reverse("billing:invoice_list")
    assert body.count("btn-primary") == 1
    assert body.count('aria-label="この画面の説明"') == 1


def test_create_post_records_and_returns_to_invoice(client_logged_in, user, invoice):
    response = client_logged_in.post(reverse("billing:payment_create", args=[invoice.pk]), POST)

    payment = Payment.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("billing:payment_detail", args=[payment.pk])
    assert payment.recorded_by == user
    assert payment.invoice_id == invoice.pk

    hop = client_logged_in.get(response["Location"])
    assert hop.status_code == 302
    assert hop["Location"] == reverse("billing:invoice_detail", args=[invoice.pk])

    detail = client_logged_in.get(hop["Location"])
    body = detail.content.decode()
    panel = _panel(detail)
    assert "一部入金" in panel
    assert "10,000" in panel
    assert "2026-12-25" in panel
    assert "6,500" in panel
    assert "振込" in panel
    assert "tester" in panel
    assert "作り物の入金" in panel
    assert reverse("billing:payment_cancel", args=[payment.pk]) in panel
    assert "保存しました。" in body


@pytest.mark.parametrize("amount", ["0", "-5"])
def test_create_post_zero_amount_is_refused_on_screen(client_logged_in, invoice, amount):
    response = client_logged_in.post(reverse("billing:payment_create", args=[invoice.pk]), {**POST, "amount": amount})

    assert response.status_code == 200
    assert "0 より大きい" in response.content.decode()
    assert Payment.objects.count() == 0


def test_create_on_cancelled_invoice_is_refused(client_logged_in, user, invoice):
    services.cancel_invoice(invoice, user)

    detail = client_logged_in.get(reverse("billing:invoice_detail", args=[invoice.pk]))
    panel = _panel(detail)
    assert reverse("billing:payment_create", args=[invoice.pk]) not in detail.content.decode()
    assert "入金は、まだ記録されていません。" in panel
    assert "「入金を記録する」から" not in panel

    response = client_logged_in.post(reverse("billing:payment_create", args=[invoice.pk]), POST)
    body = response.content.decode()
    assert response.status_code == 200
    assert "alert-error" in body
    assert "R21" in body and "取消した請求書" in body
    assert Payment.objects.count() == 0


def test_cancel_payment_from_screen(client_logged_in, user, invoice):
    payment = services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), user)
    url = reverse("billing:payment_cancel", args=[payment.pk])

    page = client_logged_in.get(url)
    text = page.content.decode()
    assert page.status_code == 200
    assert "を取り消します" in text
    assert invoice.invoice_no in text
    assert "振込" in text
    assert "6,500" in text

    response = client_logged_in.post(url, {})
    assert response.status_code == 302
    assert response["Location"] == reverse("billing:payment_detail", args=[payment.pk])
    detail = client_logged_in.get(response["Location"], follow=True)
    panel = _panel(detail)
    assert ">取消</td>" in panel
    assert "未入金" in panel
    assert "16,500" in panel
    assert url not in panel
    payment.refresh_from_db()
    assert payment.status == Payment.STATUS_CANCELLED

    again = client_logged_in.post(url, {})
    body = again.content.decode()
    assert again.status_code == 200
    assert "alert-error" in body and "取り消し済み" in body


def test_list_has_payment_columns_and_unpaid_filter(client_logged_in, user, item, invoice):
    other = _customer(user, "C002", "作り物の別の得意先")
    paid = _invoice(user, other, _order_line(user, other, item))
    services.record_payment(paid, RECEIVED_ON, paid.total, user)
    third = _customer(user, "C003", "作り物の三つ目の得意先")
    cancelled = _invoice(user, third, _order_line(user, third, item))
    services.cancel_invoice(cancelled, user)

    response = client_logged_in.get(reverse("billing:invoice_list"))

    names = [spec.name for spec in response.context["columns"]]
    assert "payment_status_label" in names and "balance" in names
    paid_row = _cells(response, paid.invoice_no)
    assert paid_row["payment_status_label"] == "入金済み"
    assert paid_row["balance"] == 0
    unpaid_row = _cells(response, invoice.invoice_no)
    assert unpaid_row["payment_status_label"] == "未入金"
    assert unpaid_row["balance"] == Decimal("16500.00")
    body = response.content.decode()
    # 「残のある物だけ」は絞り込みの枠の中の印 (既定は切。N127)。古い「すべて / 残のある物だけ」の切り替えは無い
    assert 'name="f_unpaid" value="1" class="checkbox checkbox-sm" data-list-filter>残のある物だけ</label>' in body
    assert 'id="invoices-unpaid"' not in body

    filtered = client_logged_in.get(reverse("billing:invoice_list") + "?f_unpaid=1")
    body = filtered.content.decode()
    assert invoice.invoice_no in body
    assert paid.invoice_no not in body
    assert cancelled.invoice_no not in body
    assert 'name="f_unpaid" value="1" class="checkbox checkbox-sm" checked data-list-filter>残のある物だけ</label>' in body
    assert 'id="invoices-unpaid"' not in body
    assert 'hx-include="#invoices-unpaid"' not in body

    searched = client_logged_in.get(reverse("billing:invoice_list") + f"?f_unpaid=1&q={invoice.invoice_no}")
    assert [row["obj"].pk for row in searched.context["rows"]] == [invoice.pk]
    missed = client_logged_in.get(reverse("billing:invoice_list") + f"?f_unpaid=1&q={paid.invoice_no}")
    assert missed.context["rows"] == []


@pytest.mark.django_db
def test_money_masked_in_payment_screens(on, client, invoice):
    jimu = account("jimu", permissions=["billing"])
    client.force_login(jimu)

    listed = client.get(reverse("billing:invoice_list"))
    assert _cell(listed, "balance") == access.MASK
    assert _cell(listed, "payment_status_label") == "未入金"
    header = _csv_header(client.get(reverse("billing:invoice_list") + "?format=csv"))
    assert "入金" in header
    assert "残" not in header

    detail = client.get(reverse("billing:invoice_detail", args=[invoice.pk]))
    panel = _panel(detail)
    assert "16,500" not in detail.content.decode()
    assert "—" in panel
    assert "未入金" in panel

    form = client.get(reverse("billing:payment_create", args=[invoice.pk]))
    assert form.status_code == 200
    assert "16,500" not in form.content.decode()

    refused = client.post(reverse("billing:payment_create", args=[invoice.pk]), POST)
    body = refused.content.decode()
    assert refused.status_code == 200
    assert "alert-error" in body and "入金は金額が主な内容" in body
    assert Payment.objects.count() == 0

    keiri = account("keiri", permissions=["billing"], see_money=True)
    client.force_login(keiri)
    assert "16,500" in _panel(client.get(reverse("billing:invoice_detail", args=[invoice.pk])))
    assert "16,500" in client.get(reverse("billing:payment_create", args=[invoice.pk])).content.decode()
    saved = client.post(reverse("billing:payment_create", args=[invoice.pk]), POST)
    assert saved.status_code == 302
    assert Payment.objects.count() == 1
    assert _cell(client.get(reverse("billing:invoice_list")), "balance") == Decimal("10000.00")


def test_unknown_payment_is_404(client_logged_in, invoice):
    assert client_logged_in.get(reverse("billing:payment_detail", args=[99999])).status_code == 404
    assert client_logged_in.get(reverse("billing:payment_cancel", args=[99999])).status_code == 404
    assert client_logged_in.get(reverse("billing:payment_create", args=[99999])).status_code == 404


def test_new_screens_are_in_catalog():
    by_url_name = {screen.url_name: screen for screen in all_screens()}

    for url_name in ("billing:payment_create", "billing:payment_cancel"):
        assert url_name in by_url_name
        assert by_url_name[url_name].usecase.startswith("使う人: 経理")
