"""売掛の残 (得意先ごとの残の一覧と CSV) と得意先の売掛の台帳 (依頼書 N409c。売掛の 3 段目)。読むだけの画面。

使う場面: 得意先から「今いくら残っているか」を問われた。経理は左のメニューの「売掛の残」で得意先ごとの請求・入金・調整の合計と残を見て、
行を押して得意先の台帳 (請求・入金・調整を日付の古い順に並べ、行ごとにそこまでの残) を開く。請求書の詳細の入金の枠からも台帳へ移れる。
一覧と台帳の model は取引先 (masters)。一覧は取引先の新規登録を出さず、行の行き先は台帳。台帳は取引先の編集と削除を出さない。
金額は billing.services の with_receivables と customer_ledger の値 (画面では数えない)。金額の閲覧の無い人 (金額の判定が on) には、
一覧・CSV・台帳の金額と残を見出しごと出さない (「—」も出さない)。
見た目の確かめは開発サーバーを起こさず、応答の HTML (列の並び・行の行き先・マイナスの残の赤い字) で見る。
作り物の組み方は tests/test_billing_ledger.py の関数を借りる (test_ で始まる名前は import しない)。得意先は月末締め (締日 31)。
"""

import csv
import io
import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.accounts.models import app_labels
from seizou.billing import services
from seizou.billing.models import Payment
from tests.billing_helpers import shipped
from tests.test_billing_ledger import PartnerModel, _customer, _invoice, _line

MONEY_ON = {"department": "off", "write": "off", "money": "on"}
PASSWORD = "pass-for-tests-only"
LIST_HEADINGS = ["取引先コード", "名前", "締日", "請求の合計", "入金の合計", "調整の合計", "残"]
LEDGER_HEADINGS = ["日", "区分", "番号", "期間", "入金の方法", "期日", "訳", "金額", "残"]
MONEY_HEADINGS = ["請求の合計", "入金の合計", "調整の合計", "残"]


@pytest.fixture
def book(user):
    """得意先 5 つの請求・入金・調整。

    C001: 9 月分 6,600 と 10 月分 11,000 (8 月分は取消)。9 月分に入金 5,940 と調整 −660、10 月分に電子記録債権の入金 1,000
          (取消の入金 99,999・取消の調整 −5・請求書の外の入金 1,100 は数えない)。残 10,000。tests/test_billing_ledger.py の book と同じ形。
    C002: 2,200 の請求書に入金 2,200。残 0。
    C003: 請求書が無い。C004: 取消の請求書だけ。どちらも一覧に並ばない。
    C005: 1,100 の請求書に入金 1,500 (入れすぎ)。残 −400。
    """
    first = _customer(user, "C001", "作り物の得意先")
    line = _line(user, first)
    september = _invoice(user, first, line, 6, date(2026, 9, 15), date(2026, 9, 30))
    october = _invoice(user, first, line, 10, date(2026, 10, 15), date(2026, 10, 31))
    august = _invoice(user, first, line, 3, date(2026, 8, 20), date(2026, 8, 31))
    services.cancel_invoice(august, user)
    services.record_adjustment(september, date(2026, 10, 31), Decimal("-660"), user)
    services.record_payment(september, date(2026, 10, 31), Decimal("5940"), user)
    wrong = services.record_payment(october, date(2026, 11, 2), Decimal("99999"), user)
    services.cancel_payment(wrong, user)
    wrong_adjustment = services.record_adjustment(october, date(2026, 11, 2), Decimal("-5"), user)
    services.cancel_adjustment(wrong_adjustment, user)
    services.record_payment(
        october, date(2026, 11, 25), Decimal("1000"), user, method=Payment.METHOD_E_BOND, matures_on=date(2027, 2, 28)
    )
    services.record_outside_payment(shipped(user, first, line, "1", date(2026, 11, 3)), date(2026, 11, 3), Decimal("1100"), user)

    paid_up = _customer(user, "C002", "作り物の払い済みの得意先")
    paid_invoice = _invoice(user, paid_up, _line(user, paid_up), 2, date(2026, 10, 10), date(2026, 10, 31))
    services.record_payment(paid_invoice, date(2026, 11, 25), Decimal("2200"), user)

    _customer(user, "C003", "作り物の請求書の無い得意先")
    cancelled_only = _customer(user, "C004", "作り物の取消だけの得意先")
    services.cancel_invoice(_invoice(user, cancelled_only, _line(user, cancelled_only), 1, date(2026, 10, 10), date(2026, 10, 31)), user)

    over = _customer(user, "C005", "作り物の入れすぎの得意先")
    over_invoice = _invoice(user, over, _line(user, over), 1, date(2026, 10, 10), date(2026, 10, 31))
    services.record_payment(over_invoice, date(2026, 11, 25), Decimal("1500"), user)
    return {"first": first, "september": september, "october": october, "august": august, "paid_up": paid_up, "over": over}


@pytest.fixture
def money_on(settings):
    settings.SEIZOU = {"access_control": dict(MONEY_ON)}


def _account(login_id, **flags):
    """作り物の利用者 (部署も権限も無し)。see_money=True で金額の閲覧あり。"""
    accounts_services.issue_account(login_id, PASSWORD, [], [], flags, None)
    return get_user_model().objects.get(username=login_id)


@pytest.fixture
def jimu(db):
    """金額の閲覧の無い利用者でログインした client。"""
    other = Client()
    other.force_login(_account("jimu"))
    return other


def _list(client, **params):
    return client.get(reverse("billing:receivable_list"), params)


def _ledger(client, partner):
    return client.get(reverse("billing:receivable_detail", args=[partner.pk]))


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def _headings(html):
    """最初の表の見出しの字の並び (タグと並び替えの印を除く)。"""
    head = re.search(r"<thead>(.*?)</thead>", html, re.S).group(1)
    return [_text(cell) for cell in re.findall(r"<th[^>]*>(.*?)</th>", head, re.S)]


def _list_rows(html):
    """一覧の行 {pk: 行の HTML}。"""
    return {int(pk): row for pk, row in re.findall(r'<tr id="row-(\d+)"(.*?)</tr>', html, re.S)}


def _codes(response):
    return [row["obj"].code for row in response.context["rows"]]


def _csv(response):
    text = response.content.decode("utf-8").lstrip("﻿")
    return list(csv.reader(io.StringIO(text)))


def _ledger_section(html):
    return re.search(r'<section id="ledger".*?</section>', html, re.S).group(0)


def _ledger_rows(html):
    """台帳の行の欄の字の並び。"""
    rows = re.findall(r"<tr[^>]*data-ledger-row>(.*?)</tr>", _ledger_section(html), re.S)
    return [[_text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)] for row in rows]


# ---------------------------------------------------------------- 一覧


@pytest.mark.django_db
def test_menu_has_receivable_entry(logged_in, book):
    body = _list(logged_in).content.decode()

    assert app_labels()["billing"] == "請求書・売掛の残"
    assert f'href="{reverse("billing:receivable_list")}"' in body
    assert "売掛の残" in body


@pytest.mark.django_db
def test_list_has_no_create_button(logged_in, book):
    body = _list(logged_in).content.decode()
    empty = _list(logged_in, q="無い得意先").content.decode()
    nothing = _list(logged_in, f_customer=book["first"].pk, f_open="1", q="無い").content.decode()

    for html in (body, empty, nothing):
        assert "新規登録" not in html
        assert reverse("masters:partner_create") not in html


@pytest.mark.django_db
def test_empty_list_has_no_create_button(logged_in, user):
    _customer(user, "C001", "作り物の得意先")
    body = _list(logged_in).content.decode()

    assert "まだ 1 件もありません。" in body
    assert "新規登録" not in body


@pytest.mark.django_db
def test_list_shows_issued_customers_with_ledger_totals(logged_in, book):
    response = _list(logged_in)
    body = response.content.decode()

    assert response.status_code == 200
    assert _codes(response) == ["C001", "C002", "C005"]
    for row in response.context["rows"]:
        partner = row["obj"]
        ledger = services.customer_ledger(partner)
        assert [cell["value"] for cell in row["cells"][3:]] == [ledger["billed"], ledger["paid"], ledger["adjusted"], ledger["balance"]]
    first = _list_rows(body)[book["first"].pk]
    assert ["月末", "¥17,600", "¥6,940", "-¥660", "¥10,000"] == re.findall(r'<td class="whitespace-nowrap text-right tabular-nums">(?:<span class="text-error">)?([^<]+)<', first)
    assert _headings(body) == LIST_HEADINGS


@pytest.mark.django_db
def test_open_only_drops_zero_balance(logged_in, book):
    response = _list(logged_in, f_open="1")

    assert _codes(response) == ["C001", "C005"]
    assert "残のある得意先だけ" in response.content.decode()


@pytest.mark.django_db
def test_customer_filter_and_search(logged_in, book):
    chosen = _list(logged_in, f_customer=book["over"].pk)
    searched = _list(logged_in, q="払い済み")
    by_code = _list(logged_in, q="C001")
    select = re.search(r'<select[^>]*name="f_customer".*?</select>', chosen.content.decode(), re.S).group(0)
    options = dict(re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', select))

    assert _codes(chosen) == ["C005"]
    assert _codes(searched) == ["C002"]
    assert _codes(by_code) == ["C001"]
    assert sorted(options.values()) == sorted(["作り物の得意先", "作り物の払い済みの得意先", "作り物の入れすぎの得意先"])


@pytest.mark.django_db
def test_sort_by_balance(logged_in, book):
    assert _codes(_list(logged_in, sort="-receivable_balance")) == ["C001", "C002", "C005"]
    assert _codes(_list(logged_in, sort="receivable_balance")) == ["C005", "C002", "C001"]


@pytest.mark.django_db
def test_row_goes_to_ledger(logged_in, book):
    body = _list(logged_in).content.decode()
    rows = _list_rows(body)

    for partner in (book["first"], book["paid_up"], book["over"]):
        ledger_url = reverse("billing:receivable_detail", args=[partner.pk])
        row = rows[partner.pk]
        assert f"window.location='{ledger_url}'" in row
        assert f'<a class="link link-primary block max-w-xs truncate focus-visible:outline-2 focus-visible:outline-primary" href="{ledger_url}" title="{partner.code}">{partner.code}</a>' in row
        assert f'href="{reverse("masters:partner_detail", args=[partner.pk])}"' not in row


@pytest.mark.django_db
def test_negative_balance_is_red_in_list(logged_in, book):
    row = _list_rows(_list(logged_in).content.decode())[book["over"].pk]

    assert '<span class="text-error">-¥400</span>' in row


@pytest.mark.django_db
def test_summary_view_opens(logged_in, book):
    response = _list(logged_in, view="summary")

    assert response.status_code == 200


@pytest.mark.django_db
def test_csv_has_same_columns_and_values(logged_in, book):
    rows = _csv(_list(logged_in, format="csv"))

    assert rows[0] == LIST_HEADINGS
    assert [row[0] for row in rows[1:]] == ["C001", "C002", "C005"]
    for row in rows[1:]:
        ledger = services.customer_ledger(PartnerModel.objects.get(code=row[0]))
        assert row[2] == "月末"
        assert [Decimal(value) for value in row[3:]] == [ledger["billed"], ledger["paid"], ledger["adjusted"], ledger["balance"]]


@pytest.mark.django_db
def test_list_and_csv_hide_money_without_money(money_on, jimu, book):
    response = _list(jimu)
    body = response.content.decode()
    rows = _csv(_list(jimu, format="csv"))
    sorted_rows = _list(jimu, sort="receivable_balance")  # 並べ替えが効けば C005 (残 −400) が先頭に来る

    assert response.status_code == 200
    assert _headings(body) == ["取引先コード", "名前", "締日"]
    assert _codes(response) == ["C001", "C002", "C005"]
    for heading in MONEY_HEADINGS:
        assert f">{heading}<" not in body
    first = _list_rows(body)[book["first"].pk]
    assert "¥" not in first and "17,600" not in first and "—" not in first
    assert rows[0] == ["取引先コード", "名前", "締日"]
    assert all(len(row) == 3 for row in rows)
    assert "17600" not in "".join(",".join(row) for row in rows)
    assert _codes(sorted_rows) == ["C001", "C002", "C005"]


@pytest.mark.django_db
def test_list_and_csv_show_money_with_money(money_on, client, book):
    client.force_login(_account("keiri", see_money=True))

    assert _headings(_list(client).content.decode()) == LIST_HEADINGS
    assert _csv(_list(client, format="csv"))[0] == LIST_HEADINGS


# ---------------------------------------------------------------- 台帳


@pytest.mark.django_db
def test_ledger_rows_in_date_order_with_running_balance(logged_in, book):
    response = _ledger(logged_in, book["first"])
    body = response.content.decode()
    september, october = book["september"].invoice_no, book["october"].invoice_no

    assert response.status_code == 200
    assert _headings(_ledger_section(body)) == LEDGER_HEADINGS
    assert _ledger_rows(body) == [
        ["2026-09-30", "請求", september, "2026-09-01〜2026-09-30", "", "", "", "¥6,600", "¥6,600"],
        ["2026-10-31", "請求", october, "2026-10-01〜2026-10-31", "", "", "", "¥11,000", "¥17,600"],
        ["2026-10-31", "入金", september, "", "振込", "", "", "-¥5,940", "¥11,660"],
        ["2026-10-31", "調整", september, "", "", "", "振込手数料", "-¥660", "¥11,000"],
        ["2026-11-25", "入金", october, "", "電子記録債権", "2027-02-28", "", "-¥1,000", "¥10,000"],
    ]
    section = _ledger_section(body)
    assert book["august"].invoice_no not in section
    assert "99,999" not in section and "-¥5<" not in section and "1,100" not in section
    assert f'href="{reverse("billing:invoice_detail", args=[book["september"].pk])}"' in section


@pytest.mark.django_db
def test_ledger_head_shows_customer_and_totals(logged_in, book):
    body = _ledger(logged_in, book["first"]).content.decode()
    summary = re.search(r'<section id="receivable-summary".*?</section>', body, re.S).group(0)
    values = dict(re.findall(r'<dt class="text-xs text-base-content/60">([^<]+)</dt><dd[^>]*>(?:<span class="text-error">)?([^<]+)<', summary))

    assert values == {
        "取引先コード": "C001",
        "名前": "作り物の得意先",
        "締日": "月末",
        "請求の合計": "¥17,600",
        "入金の合計": "¥6,940",
        "調整の合計": "-¥660",
        "残": "¥10,000",
    }


@pytest.mark.django_db
def test_ledger_has_no_edit_or_delete_and_goes_back_to_list(logged_in, book):
    partner = book["first"]
    body = _ledger(logged_in, partner).content.decode()
    actions = re.search(r"<div[^>]*data-detail-actions>(.*?)</div>", body, re.S).group(1)

    assert reverse("masters:partner_update", args=[partner.pk]) not in body
    assert "編集" not in actions
    assert "data-detail-more" not in body
    assert f'href="{reverse("billing:receivable_list")}"' in actions and "売掛の残に戻る" in actions
    assert "売掛の台帳" in body


@pytest.mark.django_db
def test_negative_balance_is_red_in_ledger(logged_in, book):
    body = _ledger(logged_in, book["over"]).content.decode()
    summary = re.search(r'<section id="receivable-summary".*?</section>', body, re.S).group(0)

    assert _ledger_rows(body)[-1][-1] == "-¥400"
    assert '<span class="text-error">-¥400</span>' in _ledger_section(body)
    assert '<span class="text-error">-¥400</span>' in summary
    assert '<span class="text-error">¥1,100</span>' not in body


@pytest.mark.django_db
def test_ledger_hides_money_without_money(money_on, jimu, book):
    response = _ledger(jimu, book["first"])
    body = response.content.decode()
    section = _ledger_section(body)

    assert response.status_code == 200
    assert _headings(section) == ["日", "区分", "番号", "期間", "入金の方法", "期日", "訳"]
    assert [row[:3] for row in _ledger_rows(body)] == [
        ["2026-09-30", "請求", book["september"].invoice_no],
        ["2026-10-31", "請求", book["october"].invoice_no],
        ["2026-10-31", "入金", book["september"].invoice_no],
        ["2026-10-31", "調整", book["september"].invoice_no],
        ["2026-11-25", "入金", book["october"].invoice_no],
    ]
    assert all(len(row) == 7 for row in _ledger_rows(body))
    assert "data-receivable-totals" not in body
    assert "¥" not in section and "—" not in section
    assert "請求の合計" not in body and "10,000" not in body


@pytest.mark.django_db
def test_ledger_of_customer_without_invoice(logged_in, book):
    partner = PartnerModel.objects.get(code="C003")
    body = _ledger(logged_in, partner).content.decode()

    assert "発行の請求書は、まだありません。" in body
    assert "data-ledger-row" not in body


# ---------------------------------------------------------------- 請求書の詳細から


@pytest.mark.django_db
def test_invoice_detail_links_to_customer_ledger(logged_in, book):
    body = logged_in.get(reverse("billing:invoice_detail", args=[book["september"].pk])).content.decode()
    ledger_url = reverse("billing:receivable_detail", args=[book["first"].pk])
    payments = re.search(r'<section id="payments".*?</section>', body, re.S).group(0)
    link = re.search(r"<p[^>]*data-ledger-link>(.*?)</p>", payments, re.S)

    assert link is not None
    assert f'href="{ledger_url}"' in link.group(1) and "得意先の台帳を見る" in link.group(1)
    followed = logged_in.get(ledger_url)
    assert followed.status_code == 200
    assert followed.context["object"] == book["first"]


@pytest.mark.django_db
def test_invoice_detail_link_hidden_without_money(money_on, jimu, book):
    body = jimu.get(reverse("billing:invoice_detail", args=[book["september"].pk])).content.decode()

    assert "data-ledger-link" not in body
    assert "得意先の台帳を見る" not in body
