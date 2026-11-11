"""入金の画面 (core の土台の上)。請求書の詳細の「入金」の枠・入金の記録・入金の取消・移るだけの口・一覧の列と「残のある物だけ」。

金額の閲覧 (N36) は 2 つの隠し方を見る: 一覧の「残」の列は土台が名前で隠し、枠と入力の画面の字はテンプレートが隠す。
入金の記録は金額が主な登録なので、金額の閲覧が無い人は保存で断られる。
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.billing import services
from seizou.billing.models import Invoice, Payment
from seizou.core import access
from seizou.core.screens import all_screens
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped as _shipped

User = get_user_model()

PASSWORD = "kumo-tests-2611"
ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
RECEIVED_ON = date(2026, 12, 25)
ALL_ON = {"department": "on", "write": "on", "money": "on"}
POST = {"received_on": "2026-12-25", "amount": "6500", "method": "transfer", "note": "作り物の入金"}
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}


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


def _invoice(user, customer, line):
    """出荷 4 と 6 の請求書 (小計 15,000・消費税 1,500・合計 16,500)。

    出荷日は、その得意先の 1 枚目が SHIPPED_AT で、2 枚目から 1 か月ずつ前 (同じ得意先の同じ締め期間の請求書は 1 枚だけ = R23。
    ほかの試験のファイルもこの道具で同じ得意先に 2 枚作る)。
    """
    earlier = Invoice.objects.filter(customer=customer).count()
    shipped_at = SHIPPED_AT.replace(month=SHIPPED_AT.month - earlier)
    first = _shipped(user, customer, line, "4", shipped_at)
    second = _shipped(user, customer, line, "6", shipped_at)
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
    assert f"請求書 {invoice.invoice_no} に ¥6,500 を入金として記録しました。" in body


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
    assert 'data-notice="stop"' in body
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
    assert 'data-notice="stop"' in body and "取り消し済み" in body


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
    assert not {"total", "balance"} & {spec.name for spec in listed.context["columns"]}  # 列ごと出ない (N155)
    assert access.MASK not in [cell["value"] for cell in listed.context["rows"][0]["cells"]]
    assert _cell(listed, "payment_status_label") == "未入金"
    header = _csv_header(client.get(reverse("billing:invoice_list") + "?format=csv"))
    assert "入金" in header
    assert "残" not in header

    detail = client.get(reverse("billing:invoice_detail", args=[invoice.pk]))
    panel = _panel(detail)
    assert "16,500" not in detail.content.decode()
    assert "—" not in panel and "合計" not in panel  # 合計・入金済み・残の行ごと出ない (N155)
    assert "未入金" in panel

    # 入金の記録は金額に関わる操作なので、金額の閲覧の無い人には開いた時点で断る (本人の決め 11-12 07:2x)。開けない画面に金額は出ない
    form = client.get(reverse("billing:payment_create", args=[invoice.pk]))
    assert form.status_code == 403
    assert "入金は金額が主な内容" in form.content.decode()
    assert "16,500" not in form.content.decode()

    refused = client.post(reverse("billing:payment_create", args=[invoice.pk]), POST)
    body = refused.content.decode()
    assert refused.status_code == 200
    assert 'data-notice="stop"' in body and "入金は金額が主な内容" in body
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


def test_create_in_modal_says_what_was_recorded(client_logged_in, invoice):
    """小窓で入金を記録すると、保存の後の知らせ (HX-Trigger の seizou:saved) が請求書の番号と金額を書く (候補 C44 の 2)。"""
    response = client_logged_in.post(reverse("billing:payment_create", args=[invoice.pk]), POST, **MODAL)

    assert response.status_code == 204
    saved = json.loads(response["HX-Trigger"])["seizou:saved"]
    assert saved["message"] == f"請求書 {invoice.invoice_no} に ¥6,500 を入金として記録しました。"
    assert list(get_messages(response.wsgi_request)) == []


def test_record_payment_saved_message_has_yen(user, invoice):
    """service が返す入金の saved_message は、金額を 3 桁の区切りと頭の ¥ で書く (残を超える入金も止めない)。"""
    payment = services.record_payment(invoice, RECEIVED_ON, Decimal("734900.00"), user)

    assert payment.saved_message == f"請求書 {invoice.invoice_no} に ¥734,900 を入金として記録しました。"


# --- 金額の欄の誤りの字 (初見テスト R5 の V37。候補 C25 の 1 と 2) ---

AMOUNT_NOT_A_NUMBER = "金額は 8800 のように数字で入れてください。"


@pytest.mark.parametrize("headers", [{}, MODAL], ids=["page", "modal"])
def test_create_post_amount_with_yen_says_how_to_write_it(client_logged_in, invoice, headers):
    """金額に「8800円」を入れて押すと、欄の下の字が直し方の分かる字になる (Django の既定の「数値を入力してください。」を出さない。C25 の 1)。"""
    response = client_logged_in.post(
        reverse("billing:payment_create", args=[invoice.pk]), {**POST, "amount": "8800円"}, **headers
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert AMOUNT_NOT_A_NUMBER in body
    assert "数値を入力してください" not in body
    assert Payment.objects.count() == 0


def test_create_post_negative_amount_keeps_the_min_value_message(client_logged_in, invoice):
    response = client_logged_in.post(reverse("billing:payment_create", args=[invoice.pk]), {**POST, "amount": "-1"})

    assert response.status_code == 200
    assert "入金の金額は 0 より大きい数にしてください。" in response.content.decode()
    assert Payment.objects.count() == 0


@pytest.mark.parametrize("amount", ["8,800", "８８００"])
def test_create_post_amount_with_comma_or_wide_digits_is_saved(client_logged_in, invoice, amount):
    response = client_logged_in.post(reverse("billing:payment_create", args=[invoice.pk]), {**POST, "amount": amount})

    assert response.status_code == 302
    assert Payment.objects.count() == 1
    assert Payment.objects.get().amount == Decimal("8800")


@pytest.mark.parametrize("headers", [{}, MODAL], ids=["page", "modal"])
def test_create_post_empty_amount_names_the_field(client_logged_in, invoice, headers):
    """金額を空のまま押すと、欄の下の字は「金額を入れてください。」(入力の土台がまとめて替える。C25 の 2)。止まった知らせは C57 の形のまま。"""
    response = client_logged_in.post(
        reverse("billing:payment_create", args=[invoice.pk]), {**POST, "amount": ""}, **headers
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert "金額を入れてください。" in body
    assert "このフィールドは必須です" not in body
    assert Payment.objects.count() == 0
    if not headers:
        start = body.index('data-notice="stop"')
        stop = body[start : body.index("data-toast-close", start)]
        assert "記録できません。" in stop
        assert "「金額」の欄に誤りがあります。" in stop


def _assert_payments_between_tabs_and_attachments(body):
    """入金の枠はタブの後・添付ファイルの枠の前で、取り直しの中 (#invoice-live) にある (初見テスト R5 の候補 C26 の 1)。"""
    assert body.count('<section id="payments"') == 1
    assert body.index('role="tablist"') < body.index('<section id="payments"') < body.index('<section id="attachments"')
    assert (
        body.index('<div id="invoice-live">')
        < body.index('<section id="payments"')
        < body.index('<div id="invoice-detail-refresh"')
    )


def test_detail_puts_payments_above_attachments(client_logged_in, user, invoice):
    """1280×720 で入金の枠が添付ファイルの枠の下 (窓の外) にあった (V40・U35)。枠をタブの直後に出す。取消の請求書でも同じ順。"""
    url = reverse("billing:invoice_detail", args=[invoice.pk])
    _assert_payments_between_tabs_and_attachments(client_logged_in.get(url).content.decode())

    services.cancel_invoice(invoice, user)
    _assert_payments_between_tabs_and_attachments(client_logged_in.get(url).content.decode())


@pytest.mark.django_db
def test_detail_puts_payments_above_attachments_without_money(on, client, invoice):
    """金額の閲覧の無い人 (合計と残の行が無い枠) でも、入金の枠はタブと添付ファイルの枠の間。"""
    client.force_login(account("jimu", permissions=["billing"]))

    _assert_payments_between_tabs_and_attachments(client.get(reverse("billing:invoice_detail", args=[invoice.pk])).content.decode())


def test_detail_payments_after_tabs_without_attachments_part(client_logged_in, invoice, monkeypatch):
    """添付ファイルの部品が無い会社でも、入金の枠は 1 つだけで、タブの後ろにある。"""
    from seizou.core import context_processors

    names = [name for name in context_processors.part_names() if name != "attachments"]
    monkeypatch.setattr(context_processors, "part_names", lambda: names)

    body = client_logged_in.get(reverse("billing:invoice_detail", args=[invoice.pk])).content.decode()
    assert '<section id="attachments"' not in body
    assert body.count('<section id="payments"') == 1
    assert body.index('role="tablist"') < body.index('<section id="payments"')
