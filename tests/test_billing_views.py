"""請求書の画面と帳票 (core の土台の上)。一覧・作る・詳細・取り消す・印刷用の HTML・PDF。"""

import html
import json
import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse

from seizou.billing import services
from seizou.billing.forms import InvoiceForm
from seizou.billing.models import Invoice
from seizou.core.manual import input_rows
from seizou.core.models import Company
from seizou.core.reports import PdfUnavailable
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from tests.billing_helpers import shipped as _shipped

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


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


@pytest.fixture
def customer(user):
    return _customer(user, closing_day=20, payment_month="next", payment_day=25)


@pytest.fixture
def line(user, customer, item):
    return _order_line(user, customer, item)


def _create_post(client, customer, shipments):
    return client.post(
        reverse("billing:invoice_create"),
        {"customer": customer.pk, "shipments": [s.pk for s in shipments], "issued_on": "2026-11-30"},
    )


@pytest.mark.django_db
def test_list_requires_login(client):
    response = client.get(reverse("billing:invoice_list"))
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


@pytest.mark.django_db
def test_create_from_two_shipments(client_logged_in, user, customer, line):
    first = _shipped(user, customer, line, "4")
    second = _shipped(user, customer, line, "6")

    response = _create_post(client_logged_in, customer, [first, second])

    invoice = Invoice.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("billing:invoice_detail", args=[invoice.pk])
    assert invoice.customer == customer
    assert invoice.issued_on == ISSUED_ON
    assert {row.shipment_id for row in invoice.lines.all()} == {first.pk, second.pk}

    detail = client_logged_in.get(response["Location"])
    body = detail.content.decode()
    assert detail.status_code == 200
    assert invoice.invoice_no in body and "明細" in body
    assert reverse("billing:invoice_print", args=[invoice.pk]) in body
    assert reverse("billing:invoice_pdf", args=[invoice.pk]) in body
    assert reverse("billing:invoice_cancel", args=[invoice.pk]) in body
    assert 'target="_blank"' in body


@pytest.mark.django_db
def test_mixed_customers_is_r09_red(client_logged_in, user, customer, item, line):
    other = _customer(user, "C002", "作り物の別の得意先")
    other_line = _order_line(user, other, item)
    mine = _shipped(user, customer, line, "4")
    theirs = _shipped(user, other, other_line, "3")

    response = _create_post(client_logged_in, customer, [mine, theirs])

    body = response.content.decode()
    assert response.status_code == 200
    assert 'data-notice="stop"' in body and "R09" in body
    assert theirs.shipment_no in body
    assert Invoice.objects.count() == 0


@pytest.mark.django_db
def test_create_form_hides_unbillable(client_logged_in, user, customer, line):
    no_note = shipping_services.ship(line, Decimal("2"), SHIPPED_AT, user)
    invoiced = _shipped(user, customer, line, "3")
    billable = _shipped(user, customer, line, "5")
    services.create_invoice(customer, [invoiced], user, issued_on=ISSUED_ON)

    response = client_logged_in.get(reverse("billing:invoice_create"))

    body = response.content.decode()
    assert response.status_code == 200
    assert billable.shipment_no in body
    assert no_note.shipment_no not in body
    assert invoiced.shipment_no not in body
    # 行ごとに得意先の印が付く (得意先の欄で行を絞る JS が読む)。
    assert f'data-partner="{customer.pk}"' in body


@pytest.mark.django_db
def test_print_html_has_amounts(client_logged_in, user, customer, line):
    Company.objects.create(name="作り物の製作所")
    first = _shipped(user, customer, line, "4")
    second = _shipped(user, customer, line, "6")
    invoice = services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)

    response = client_logged_in.get(reverse("billing:invoice_print", args=[invoice.pk]))

    body = response.content.decode()
    assert response.status_code == 200
    assert invoice.invoice_no in body
    assert "作り物の得意先" in body
    assert "支払期日" in body and "2026年12月25日" in body
    assert "T-100" in body
    assert ">4<" in body and ">6<" in body
    # 小計 15,000・消費税 1,500 (10%)・合計 16,500。
    assert "15,000" in body and "1,500" in body and "16,500" in body
    assert "消費税 (10%)" in body
    assert "作り物の製作所" in body


@pytest.mark.django_db
def test_print_html_without_due_date(client_logged_in, user, item):
    blank = _customer(user, "C002", "作り物の支払の欄の無い得意先")
    line = _order_line(user, blank, item)
    shipment = _shipped(user, blank, line, "10")
    invoice = services.create_invoice(blank, [shipment], user, issued_on=ISSUED_ON)

    response = client_logged_in.get(reverse("billing:invoice_print", args=[invoice.pk]))

    body = response.content.decode()
    assert response.status_code == 200
    assert invoice.invoice_no in body
    assert "支払期日" not in body


@pytest.mark.django_db
def test_pdf_falls_back_to_html_when_unavailable(client_logged_in, user, customer, line, monkeypatch):
    shipment = _shipped(user, customer, line, "10")
    invoice = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)

    def unavailable(template, context):
        raise PdfUnavailable("作り物の試験: PDF の部品が無い")

    monkeypatch.setattr("seizou.billing.views.render_pdf", unavailable)
    response = client_logged_in.get(reverse("billing:invoice_pdf", args=[invoice.pk]))

    body = response.content.decode()
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    assert "PDF の準備がありません" in body
    assert invoice.invoice_no in body


@pytest.mark.pdf
@pytest.mark.django_db
def test_pdf_is_returned_when_available(client_logged_in, user, customer, line):
    shipment = _shipped(user, customer, line, "10")
    invoice = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)

    response = client_logged_in.get(reverse("billing:invoice_pdf", args=[invoice.pk]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert response["Content-Disposition"] == f'inline; filename="{invoice.invoice_no}.pdf"'


@pytest.mark.django_db
def test_cancel_then_create_again(client_logged_in, user, customer, line):
    first = _shipped(user, customer, line, "4")
    second = _shipped(user, customer, line, "6")
    invoice = services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)

    page = client_logged_in.get(reverse("billing:invoice_cancel", args=[invoice.pk]))
    assert "この請求書を取り消します。入っていた出荷は、また請求できます。" in page.content.decode()
    response = client_logged_in.post(reverse("billing:invoice_cancel", args=[invoice.pk]), {})

    assert response.status_code == 302
    assert response["Location"] == reverse("billing:invoice_detail", args=[invoice.pk])
    invoice.refresh_from_db()
    assert invoice.status == Invoice.STATUS_CANCELLED
    detail = client_logged_in.get(reverse("billing:invoice_detail", args=[invoice.pk]))
    assert reverse("billing:invoice_cancel", args=[invoice.pk]) not in detail.content.decode()

    again = _create_post(client_logged_in, customer, [first, second])
    assert again.status_code == 302
    assert Invoice.objects.filter(status=Invoice.STATUS_ISSUED).count() == 1


@pytest.mark.django_db
def test_menu_has_invoice_entry(client_logged_in):
    # 請求書の画面でない所 (ダッシュボード) の左のメニューに、請求書の入口が並ぶ。
    response = client_logged_in.get(reverse("core:home"))
    body = response.content.decode()
    assert response.status_code == 200
    assert "請求書" in body
    assert f'href="{reverse("billing:invoice_list")}"' in body


# --- 発行日の案内の字 (初見テスト R5・候補 C02 の 1。初期値は今日のまま) ---

ISSUED_ON_HELP = "請求書に載る日付です。支払期日は発行日でなく、締めの期間の終わりと得意先の支払の決まりから決まります。"


def test_issued_on_help_text_on_form():
    assert InvoiceForm.base_fields["issued_on"].help_text == ISSUED_ON_HELP


def test_issued_on_help_text_in_manual_input_rows():
    # 「?」の説明の「入れる物」の表の「発行日」の行に、同じ字が出る。
    rows = {name: note for name, _required, note in input_rows(InvoiceForm)}
    assert rows["発行日"] == ISSUED_ON_HELP


@pytest.mark.django_db
def test_create_form_shows_issued_on_help_text(client_logged_in, user, customer, line):
    _shipped(user, customer, line, "4")

    response = client_logged_in.get(reverse("billing:invoice_create"))

    body = response.content.decode()
    assert response.status_code == 200
    assert ISSUED_ON_HELP in body


# --- 作った後に、同じ締めの期間に残った出荷を右上に知らせる (初見テスト R5・候補 C02 の 2。止めない) ---


def _messages(response):
    """その要求で足した右上の知らせ (level_tag と字) の並び (tests/test_cancel_messages.py と同じ形)。"""
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


@pytest.mark.django_db
def test_create_warns_unbilled_shipments_in_same_period(client_logged_in, user, customer, line):
    # 20 日締めなので、11/5 の出荷は 10/21〜11/20 の期間。11/25 の出荷は次の期間 (11/21〜12/20)。
    first = _shipped(user, customer, line, "4")
    second = _shipped(user, customer, line, "3")
    no_note = shipping_services.ship(line, Decimal("2"), SHIPPED_AT, user)
    later = _shipped(user, customer, line, "1", date(2026, 11, 25))

    response = _create_post(client_logged_in, customer, [first])

    assert response.status_code == 302
    assert _messages(response) == [
        (
            "warning",
            "保存しました。この期間 (2026-10-21〜2026-11-20) の作り物の得意先の出荷で、まだ請求書に入っていない物が 2 件あります: "
            f"{second.shipment_no}・{no_note.shipment_no} (発行の納品書なし)。",
        )
    ]
    assert later.shipment_no not in _messages(response)[0][1]
    invoice = Invoice.objects.get()
    assert (invoice.period_from, invoice.period_to) == (date(2026, 10, 21), date(2026, 11, 20))
    assert invoice.due_on == date(2026, 12, 25)
    assert [row.shipment_id for row in invoice.lines.all()] == [first.pk]


@pytest.mark.django_db
def test_create_says_all_billed_in_same_period(client_logged_in, user, customer, line):
    first = _shipped(user, customer, line, "4")
    second = _shipped(user, customer, line, "5")
    _shipped(user, customer, line, "1", date(2026, 11, 25))  # 次の期間の出荷は残りに数えない

    response = _create_post(client_logged_in, customer, [first, second])

    assert response.status_code == 302
    assert _messages(response) == [("success", "保存しました。この期間の作り物の得意先の出荷は、全部請求書に入っています。")]
    invoice = Invoice.objects.get()
    assert (invoice.period_from, invoice.period_to) == (date(2026, 10, 21), date(2026, 11, 20))
    assert invoice.due_on == date(2026, 12, 25)
    assert {row.shipment_id for row in invoice.lines.all()} == {first.pk, second.pk}


# --- 出荷の表・合計の枠・保存のボタンの字 (初見テスト R5・候補 C74 の 1 晩目。保存の値と決まりは変えない) ---

#: 合計の枠の箱の印 (rows・qty・amount の span の印と分ける。tests/test_core_line_totals.py の FRAME と同じ)
FRAME = re.compile(r"data-line-totals(?=[\s>])")
#: 金額の閲覧が無い人の設定 (tests/test_core_line_totals.py の NO_MONEY と同じ)
NO_MONEY = {"department": "off", "write": "off", "money": "on"}
INVOICE_HEADERS = ("選ぶ", "出荷番号", "出荷日", "得意先", "部品", "数量", "金額 (税抜)", "納品書")
INVOICE_TEMPLATES = ["{rows} 行・税抜 ¥{amount} の請求書を作る", "{rows} 行の請求書を作る"]


def _button(body):
    """入力のカードの主の保存のボタン。(ボタンの字, data-submit-label の型の並び か None, data-submit-label-plain の字 か None)。
    tests/test_core_line_totals.py の _button を写した。"""
    match = re.search(r'<button class="btn btn-primary" type="submit"([^>]*)>(.*?)</button>', body, re.S)
    assert match, "保存のボタンが無い"
    attrs, inner = match.group(1), match.group(2)
    label = html.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
    template = re.search(r'data-submit-label="([^"]*)"', attrs)
    plain = re.search(r'data-submit-label-plain="([^"]*)"', attrs)
    return (
        label,
        json.loads(html.unescape(template.group(1))) if template else None,
        html.unescape(plain.group(1)) if plain else None,
    )


def _frame(body):
    """合計の枠の字。{"rows": 字, "qty": 字, "amount": 字 か None (金額の組が無い)}。枠はちょうど 1 つ。
    tests/test_core_line_totals.py の _frame を写した。"""
    assert len(FRAME.findall(body)) == 1, "合計の枠が 1 つでない"
    found = {}
    for name in ("rows", "qty", "amount"):
        match = re.search(rf"data-line-totals-{name}>([^<]*)</span>", body)
        found[name] = match.group(1) if match else None
    return found


def _box_tag(body, shipment):
    """出荷の checkbox の tag の字。"""
    match = re.search(rf'<input type="checkbox" name="shipments" value="{shipment.pk}"[^>]*>', body)
    assert match, f"{shipment.shipment_no} の checkbox が無い"
    return match.group(0)


@pytest.mark.django_db
def test_create_form_shows_shipments_as_table(client_logged_in, user, customer, line):
    shipment = _shipped(user, customer, line, "5")

    response = client_logged_in.get(reverse("billing:invoice_create"))

    body = response.content.decode()
    assert response.status_code == 200
    assert '<table id="id_shipments"' in body
    for header in INVOICE_HEADERS:
        assert f">{header}</th>" in body, header
    # 行の数量と金額 (5 × 1,500) は、JS が読む素の数の字。
    assert 'data-label-pick-qty="5"' in body
    assert 'data-label-pick-amount="7500"' in body
    assert re.search(r'<input type="checkbox" name="shipments"[^>]*data-label-pick(?=[\s>])', body)
    assert f'data-partner="{customer.pk}"' in _box_tag(body, shipment)
    # checkbox の見えない名前は今の行の字 (出荷番号から始まる)。
    assert any(shipment.shipment_no in text for text in re.findall(r'<span class="sr-only">([^<]*)</span>', body))


@pytest.mark.django_db
def test_row_amount_is_saved_amount(client_logged_in, user, item):
    # 3 × 33.4 = 100.2。切上なら 101、四捨五入なら 100 (tests/test_billing_invoice.py の test_amount_uses_round_amount と同じ数)。
    ceil = _customer(user, "C003", "作り物の切上の得意先", rounding="ceil")
    order = orders_services.create_order(
        ceil,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("33.4")}],
        user,
        order_date=ORDER_DATE,
    )
    shipment = _shipped(user, ceil, order.lines.get(), "3")

    body = client_logged_in.get(reverse("billing:invoice_create")).content.decode()
    assert 'data-label-pick-amount="101"' in body

    response = _create_post(client_logged_in, ceil, [shipment])

    assert response.status_code == 302
    assert Invoice.objects.get().lines.get().amount == Decimal("101")


@pytest.mark.django_db
def test_create_form_frame_and_button_when_opened(client_logged_in, user, customer, line):
    _shipped(user, customer, line, "5")

    body = client_logged_in.get(reverse("billing:invoice_create")).content.decode()

    assert _frame(body) == {"rows": "0", "qty": "0", "amount": "0"}
    assert _button(body) == ("請求書を作る", INVOICE_TEMPLATES, "請求書を作る")


@pytest.mark.django_db
def test_stopped_form_counts_only_chosen_customer_rows(client_logged_in, user, customer, item, line):
    # test_mixed_customers_is_r09_red と同じ組。自分の得意先の 4 (6,000) と、別の得意先の 3 (4,500)。
    other = _customer(user, "C002", "作り物の別の得意先")
    other_line = _order_line(user, other, item)
    mine = _shipped(user, customer, line, "4")
    theirs = _shipped(user, other, other_line, "3")

    response = _create_post(client_logged_in, customer, [mine, theirs])

    body = response.content.decode()
    assert response.status_code == 200
    # 画面の JS が隠す別の得意先の行は数えない。
    assert _frame(body) == {"rows": "1", "qty": "4", "amount": "6,000"}
    assert _button(body)[0] == "1 行・税抜 ¥6,000 の請求書を作る"
    # 印は外していない。
    for shipment in (mine, theirs):
        assert re.search(r"\schecked(?=[\s>])", _box_tag(body, shipment)), shipment.shipment_no


@pytest.mark.django_db
def test_create_form_without_money(settings, client_logged_in, user, customer, line):
    settings.SEIZOU = {"access_control": dict(NO_MONEY)}
    _shipped(user, customer, line, "5")

    response = client_logged_in.get(reverse("billing:invoice_create"))

    body = response.content.decode()
    assert response.status_code == 200
    assert "金額 (税抜)" not in body
    assert "data-label-pick-amount" not in body
    assert _frame(body)["amount"] is None
    assert _button(body) == ("請求書を作る", ["{rows} 行の請求書を作る"], "請求書を作る")


@pytest.mark.django_db
def test_create_form_narrows_table_rows(client_logged_in, user, customer, line):
    _shipped(user, customer, line, "5")

    body = client_logged_in.get(reverse("billing:invoice_create")).content.decode()

    # 得意先で絞る JS は表の行を隠す (div だと表ごと隠れる)。
    assert 'input.closest("tr")' in body
    assert 'input.closest("div")' not in body
    # 別の得意先の行に残った印の数の 1 行は、ちょうど 1 つで、開いた時は隠れている。
    tags = re.findall(r"<p [^>]*data-picked-elsewhere(?=[\s>])[^>]*>", body)
    assert len(tags) == 1
    assert re.search(r"\shidden(?=[\s>])", tags[0])
