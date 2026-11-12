"""前回の残を写した請求書の帳票と詳細の 1 行 (依頼書 N409b。売掛の 2 段目)。

前回の残を載せる会社 (invoice_previous_balance = true) で、10 月分 (合計 11,000) に入金 5,000 と調整 −660 を記録した後に 11 月分 (合計 5,500) を作ると、
11 月分は前回の残 5,340 を写す。11 月分の帳票は、合計の行の後に「前回の残」と「今回のご請求額」(5,340 + 5,500 = 10,840) を出し、
頭の「ご請求金額 (税込)」の値を今回のご請求額にする。写しの無い請求書 (設定が「載せない」の間に作った物) は今の字のまま。
写しは作った時の値なので、写した後に 10 月分へ入金を足しても、刷る字は変わらない。請求書の詳細の入金の枠にも同じ 2 つの値の 1 行を出す。
得意先は月末締め (締日 31)。単価 1,000・消費税 10%。作り物のデータは全部 services で作る。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services
from seizou.billing.reports import invoice_context
from seizou.core.models import ReportCopy
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped
from tests.test_billing_payment_views import ALL_ON, account, _panel

ON = {"invoice_previous_balance": True}
AMOUNT_BOX = 'class="report-amount"'


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer", "closing_day": 31, "payment_month": "next", "payment_day": 25},
        user,
    )


@pytest.fixture
def line(user, customer):
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("100"), "unit_price": Decimal("1000")}], user, order_date=date(2026, 8, 1)
    )
    return order.lines.get()


def _invoice(user, customer, line, qty, shipped_at):
    shipment = shipped(user, customer, line, str(qty), shipped_at)
    _period_from, period_to = services.closing_period(customer, shipped_at)
    return services.create_invoice(customer, [shipment], user, issued_on=period_to)


@pytest.fixture
def printed(settings, user, customer, line):
    """10 月分の入金 5,000 と調整 −660 の後に作った 11 月分 (前回の残 5,340 を写した)。"""
    settings.SEIZOU = dict(ON)
    october = _invoice(user, customer, line, 10, date(2026, 10, 15))
    services.record_payment(october, date(2026, 10, 31), Decimal("5000"), user)
    services.record_adjustment(october, date(2026, 10, 31), Decimal("-660"), user)
    november = _invoice(user, customer, line, 5, date(2026, 11, 5))
    november.refresh_from_db()
    assert november.previous_balance == Decimal("5340")
    return {"october": october, "november": november}


def _print(client, invoice):
    response = client.get(reverse("billing:invoice_print", args=[invoice.pk]))
    assert response.status_code == 200
    return response.content.decode()


def _foot(body):
    start = body.index("<tfoot>")
    return body[start : body.index("</tfoot>", start)]


def _head_amount(body):
    box = body[body.index(AMOUNT_BOX) : body.index("</table>", body.index(AMOUNT_BOX))]
    return re.search(r'<td class="report-amount-total">([^<]*)</td>', box).group(1)


# --- 帳票 ---


@pytest.mark.django_db
def test_print_shows_previous_balance_and_billed_now(logged_in, printed):
    body = _print(logged_in, printed["november"])
    foot = _foot(body)

    total = '<tr class="report-total"><th colspan="7">合計</th><td class="num">¥5,500</td></tr>'
    previous = '<tr><th colspan="7">前回の残</th><td class="num">¥5,340</td></tr>'
    now = '<tr class="report-total"><th colspan="7">今回のご請求額</th><td class="num">¥10,840</td></tr>'
    assert foot.index(total) < foot.index(previous) < foot.index(now)
    assert _head_amount(body) == "¥10,840"
    assert "ご請求金額 (税込)" in body


@pytest.mark.django_db
def test_print_without_copy_keeps_todays_text(logged_in, settings, user, customer, line):
    """設定が「載せない」の間に作った請求書 (写しが空) は今の字のまま。"""
    settings.SEIZOU = {}
    invoice = _invoice(user, customer, line, 10, date(2026, 10, 15))
    assert invoice.previous_balance is None

    body = _print(logged_in, invoice)
    assert "前回の残" not in body and "今回のご請求額" not in body
    assert _head_amount(body) == "¥11,000"
    context = invoice_context(invoice)
    assert context["previous_balance"] is None and context["billed_now"] is None


@pytest.mark.django_db
def test_print_with_zero_copy_shows_rows(logged_in, printed):
    """前の期間の請求書の無い得意先の 1 枚目は、写しが 0 (空でない)。0 も写しなので、前回の残 ¥0 と今回のご請求額 (合計と同じ) を出す。"""
    october = printed["october"]
    october.refresh_from_db()
    assert october.previous_balance == Decimal("0")

    foot = _foot(_print(logged_in, october))
    assert '<tr><th colspan="7">前回の残</th><td class="num">¥0</td></tr>' in foot
    assert '<th colspan="7">今回のご請求額</th><td class="num">¥11,000</td>' in foot


@pytest.mark.django_db
def test_print_does_not_change_after_later_payment(logged_in, user, printed):
    """写しを作った後に 10 月分へ入金を足しても、11 月分の刷る字は変わらない (計算し直さない)。発行の時の写しにも同じ行がある。"""
    before = _foot(_print(logged_in, printed["november"]))
    services.record_payment(printed["october"], date(2026, 11, 20), Decimal("5340"), user)
    printed["october"].refresh_from_db()
    assert printed["october"].balance() == Decimal("0")

    after = _print(logged_in, printed["november"])
    assert _foot(after) == before
    assert _head_amount(after) == "¥10,840"

    copy = ReportCopy.objects.get(model="billing.invoice", object_pk=printed["november"].pk)
    assert '<th colspan="7">前回の残</th><td class="num">¥5,340</td>' in copy.html
    assert '<th colspan="7">今回のご請求額</th><td class="num">¥10,840</td>' in copy.html


@pytest.mark.django_db
def test_print_negative_previous_balance(logged_in, settings, user, customer, line):
    """多い入金で前の請求書の残がマイナスなら、前回の残もマイナスで刷る (画面の yen の字と同じ -¥…)。"""
    settings.SEIZOU = dict(ON)
    october = _invoice(user, customer, line, 10, date(2026, 10, 15))
    services.record_payment(october, date(2026, 10, 31), Decimal("12000"), user)
    november = _invoice(user, customer, line, 5, date(2026, 11, 5))

    foot = _foot(_print(logged_in, november))
    assert '<tr><th colspan="7">前回の残</th><td class="num">-¥1,000</td></tr>' in foot
    assert '<th colspan="7">今回のご請求額</th><td class="num">¥4,500</td>' in foot


# --- 請求書の詳細の入金の枠の 1 行 ---


@pytest.mark.django_db
def test_detail_panel_shows_previous_balance_line(logged_in, printed):
    panel = _panel(logged_in.get(reverse("billing:invoice_detail", args=[printed["november"].pk])))
    line = re.search(r"<p[^>]*data-previous-balance>(.*?)</p>", panel, re.S)
    assert line, "前回の残の 1 行が無い"
    text = re.sub(r"<[^>]+>", "", line.group(1))
    assert text == "前回の残 ¥5,340・今回のご請求額 ¥10,840"
    # 枠の中 (灰色の地に直に置かない)。入金の頭の後、合計・入金済み・調整・残の並びの前
    assert panel.index("payments-heading") < panel.index("data-previous-balance") < panel.index("<dl")


@pytest.mark.django_db
def test_detail_panel_hides_line_without_copy_or_money(logged_in, client, settings, user, customer, line, printed):
    settings.SEIZOU = {}
    other = masters_services.create_partner({"code": "C002", "name": "作り物の別の得意先", "role": "customer", "closing_day": 31}, user)
    other_line = orders_services.create_order(
        other, [{"item": line.item, "qty": Decimal("10"), "unit_price": Decimal("1000")}], user, order_date=date(2026, 8, 1)
    ).lines.get()
    plain = _invoice(user, other, other_line, 10, date(2026, 10, 15))
    assert plain.previous_balance is None
    assert "data-previous-balance" not in _panel(logged_in.get(reverse("billing:invoice_detail", args=[plain.pk])))

    settings.SEIZOU = {**ON, "access_control": dict(ALL_ON)}
    client.force_login(account("jimu", permissions=["billing"]))
    detail = client.get(reverse("billing:invoice_detail", args=[printed["november"].pk]))
    assert detail.status_code == 200
    panel = _panel(detail)
    assert "data-previous-balance" not in panel and "前回の残" not in panel and "10,840" not in panel
