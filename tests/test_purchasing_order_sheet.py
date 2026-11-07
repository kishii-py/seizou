"""協力企業への発注書の帳票 (purchasing.reports.purchase_order_context と、印刷用の画面と PDF の口) のテスト。エンタープライズ化の調べ 第一弾の候補 13 (N356)。

- (a) 明細の品番・品名・工程・数量・単位・単価・金額・納期と、金額の合計
- (b) 支払の条件の字 (締日 20 日・翌月末) と、支払の月の空の取引先の空の字と知らせ
- (c) 納期の空の明細の知らせ
- (d) 取消の発注の印
- (e) 会社の振込先と登録番号を渡さない
- (f) 印刷用の画面に出る字
- (g) 納期の空の明細のある発注の印刷用の画面の知らせ
- (h) PDF を作れないときの印刷用の HTML
- (i) 金額の閲覧の無い人 (on) は印刷と PDF が 403 で、発注の詳細に 2 つの link が無い
- (j) 取消の発注の印刷に「取消」の印
取引先・部品・工程・会社の値は作り物だけを使う。発注は送らずに作り (send=False)、相手への HTTP は偽物 (呼んだら落とす) にする。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.core import services as core_services
from seizou.core.reports import PdfUnavailable
from seizou.masters import services as masters_services
from seizou.purchasing import reports, services
from seizou.purchasing.views import PDF_NOTICE

ORDER_DATE = date(2026, 11, 2)
DUE_DATE = date(2026, 11, 20)
PASSWORD = "kumo-tests-2611"
ALL_ON = {"department": "on", "write": "on", "money": "on"}


@pytest.fixture
def no_http(monkeypatch):
    """HTTP を呼んだら落とす (発注は送らない)。"""

    def fail(*args, **kwargs):
        raise AssertionError("HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


@pytest.fixture
def on(settings):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}


def _partner(user, code="S101", **changes):
    data = {
        "code": code,
        "name": f"作り物の協力企業 {code}",
        "role": "subcontractor",
        "closing_day": 20,
        "payment_month": "next",
        "payment_day": 31,
    }
    data.update(changes)
    return masters_services.create_partner(data, user)


@pytest.fixture
def partner(user):
    return _partner(user)


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-201", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def plate(user):
    return masters_services.create_item({"part_no": "T-202", "name": "作り物の板", "unit": "枚"}, user)


@pytest.fixture
def plating(user):
    return masters_services.create_process({"code": "P10", "name": "作り物のめっき", "is_outsourced": True}, user)


def _order(partner, user, lines):
    return services.create_purchase_order(partner, lines, user, order_date=ORDER_DATE, note="作り物の備考", send=False)


@pytest.fixture
def order(partner, shaft, plate, plating, user, no_http):
    """明細 2 行 (軸 = めっきの工程・納期あり、板 = 工程なし・納期なし)。"""
    return _order(
        partner,
        user,
        [
            {"item": shaft, "qty": Decimal("4"), "unit_price": Decimal("250"), "due_date": DUE_DATE, "process": plating},
            {"item": plate, "qty": Decimal("3"), "unit_price": Decimal("1200")},
        ],
    )


def _account(login_id, *, permissions=(), **flags):
    from django.contrib.auth import get_user_model

    accounts_services.issue_account(login_id, PASSWORD, [], list(permissions), flags, None)
    return get_user_model().objects.get(username=login_id)


# ---------------------------------------------------------------- context


@pytest.mark.django_db
def test_context_lines_and_total(order):
    """(a) 明細の品番・品名・工程・数量・単位・単価・金額・納期と、明細の金額の合計 (税抜)。"""
    context = reports.purchase_order_context(order)
    assert context["order"] == {"number": order.po_no, "date": ORDER_DATE, "note": "作り物の備考"}
    assert context["partner"].pk == order.partner_id
    first, second = context["lines"]
    assert first == {
        "line_no": 1,
        "item_code": "T-201",
        "item_name": "作り物の軸",
        "process": "作り物のめっき",
        "quantity": Decimal("4"),
        "unit": "個",
        "unit_price": Decimal("250"),
        "price_unit": "個",
        "amount": Decimal("1000"),
        "due_date": DUE_DATE,
    }
    assert (second["item_code"], second["process"], second["unit"], second["amount"], second["due_date"]) == (
        "T-202",
        "",
        "枚",
        Decimal("3600"),
        None,
    )
    assert context["total"] == Decimal("4600")
    assert context["delivery_place"] == reports.DELIVERY_PLACE_DEFAULT
    assert context["cancelled"] is False


@pytest.mark.django_db
def test_context_payment_terms(user, shaft, no_http):
    """(b) 20 日締め・翌月・31 日払いは「締日 20 日・支払日 翌月末」。締日の空は「月末」。支払の月の空は空の字と知らせの 1 行。"""
    line = [{"item": shaft, "qty": Decimal("1"), "unit_price": Decimal("100"), "due_date": DUE_DATE}]

    context = reports.purchase_order_context(_order(_partner(user, "S102"), user, line))
    assert context["payment_terms"] == "締日 20 日・支払日 翌月末"
    assert context["notices"] == []

    context = reports.purchase_order_context(_order(_partner(user, "S103", closing_day=None, payment_day=25), user, line))
    assert context["payment_terms"] == "締日 月末・支払日 翌月 25 日"

    context = reports.purchase_order_context(_order(_partner(user, "S104", payment_month="", payment_day=None), user, line))
    assert context["payment_terms"] == ""
    assert context["notices"] == [reports.NO_PAYMENT_TERMS_NOTICE]
    assert "支払の条件 (締日・支払の月・支払日) が登録されていません" in reports.NO_PAYMENT_TERMS_NOTICE


@pytest.mark.django_db
def test_context_notice_for_missing_due_date(order):
    """(c) 納期の空の明細 (行 2) があると、知らせの 1 行。"""
    assert reports.purchase_order_context(order)["notices"] == [
        "納期の決まっていない明細があります (行 2)。納期を決めて相手に伝えてください。"
    ]


@pytest.mark.django_db
def test_context_cancelled(order, user):
    """(d) 取消の発注は cancelled が真 (明細は全部出す)。"""
    services.cancel_purchase_order(order, user)
    order.refresh_from_db()
    context = reports.purchase_order_context(order)
    assert context["cancelled"] is True
    assert [line["line_no"] for line in context["lines"]] == [1, 2]


@pytest.mark.django_db
def test_context_company_without_bank_and_registration_no(order, user):
    """(e) 会社に振込先と登録番号を入れても、context の company にその鍵が無い (帳票の土台が出さない)。"""
    core_services.update_company(
        core_services.company(),
        {"bank_account": "作り物銀行 見本支店 普通 0000000", "registration_no": "T0000000000001"},
        user,
    )
    company = reports.purchase_order_context(order)["company"]
    assert set(company) == {"name", "address", "phone", "logo"}
    assert "bank_account" not in company and "registration_no" not in company


# ---------------------------------------------------------------- 印刷用の画面と PDF


def _print(client, order):
    response = client.get(reverse("purchasing:purchaseorder_print", args=[order.pk]))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_print_shows_order_sheet(client_logged_in, order):
    """(f) 印刷用の画面に、会社・宛名・発注番号・発注日・品名・工程・数量・納期・受領場所・単価・金額・支払の条件の字が出る。"""
    body = _print(client_logged_in, order)
    for words in (
        core_services.company().name,
        f"{order.partner.name} 御中",
        order.po_no,
        "2026年11月2日",
        "作り物の軸",
        "工程 作り物のめっき",
        ">4<",
        "2026年11月20日",
        reports.DELIVERY_PLACE_DEFAULT,
        "¥250 / 個",
        "¥1,000",
        "¥4,600",
        "金額の合計 (税抜)",
        "締日 20 日・支払日 翌月末",
        "下記のとおり発注いたします。",
        "備考 作り物の備考",
    ):
        assert words in body, words
    assert "<title>発注書 " in body
    assert "お振込先" not in body and "登録番号" not in body


@pytest.mark.django_db
def test_print_shows_due_date_notice(client_logged_in, order):
    """(g) 納期の空の明細のある発注の印刷用の画面に、納期の知らせの 1 行。"""
    body = _print(client_logged_in, order)
    assert '<p role="alert">納期の決まっていない明細があります (行 2)。納期を決めて相手に伝えてください。</p>' in body


@pytest.mark.django_db
def test_pdf_falls_back_to_html(monkeypatch, client_logged_in, order):
    """(h) PDF を作れない環境では、印刷用の HTML の上に知らせを出して返す。"""

    def unavailable(*args, **kwargs):
        raise PdfUnavailable("作れない")

    monkeypatch.setattr("seizou.purchasing.views.render_pdf", unavailable)
    response = client_logged_in.get(reverse("purchasing:purchaseorder_pdf", args=[order.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert f'<p role="alert">{PDF_NOTICE}</p>' in body
    assert order.po_no in body


@pytest.mark.django_db
def test_print_and_pdf_need_money(on, client, order):
    """(i) money が on で金額の閲覧の無い人は印刷と PDF が 403、発注の詳細に 2 つの link が無い。閲覧のある人には link があり、開ける。"""
    links = [reverse(name, args=[order.pk]) for name in ("purchasing:purchaseorder_print", "purchasing:purchaseorder_pdf")]
    detail = reverse("purchasing:purchaseorder_detail", args=[order.pk])

    client.force_login(_account("hacchu", permissions=["purchasing"]))
    for url in links:
        response = client.get(url)
        assert response.status_code == 403, url
        html = response.content.decode()
        assert "この書面は開けません" in html
        assert "発注書は金額が主な内容のため、金額の閲覧の権限が無い方は開けません。" in html
    response = client.get(detail)
    assert response.status_code == 200
    body = response.content.decode()
    for url in links:
        assert url not in body, url

    client.force_login(_account("keiri", permissions=["purchasing"], see_money=True))
    response = client.get(detail)
    assert response.status_code == 200
    body = response.content.decode()
    for url in links:
        assert f'href="{url}" target="_blank" rel="noopener"' in body, url
    assert client.get(links[0]).status_code == 200


@pytest.mark.django_db
def test_print_shows_cancelled_mark(client_logged_in, order, user):
    """(j) 取消の発注の印刷に「取消」の印 (取消でない発注には出ない)。"""
    assert 'class="report-cancelled"' not in _print(client_logged_in, order)
    services.cancel_purchase_order(order, user)
    assert _print(client_logged_in, order).count('class="report-cancelled"') == 1
