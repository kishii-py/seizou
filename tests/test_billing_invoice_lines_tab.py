"""請求書の詳細の「明細」のタブ (初見テスト R5 の V26。候補 C17)。

core の詳細の block tab_rows を billing のテンプレートが差し替え、明細を印刷用の画面と同じ並び
(出荷日・出荷番号・品番・品名・数量・単位・単価・金額) の表で出す。金額の閲覧が無い人には単価と金額の列を見出しごと出さない
(「—」も出さない。N155)。タブの行は出荷と受注の明細と部品を行と一緒に 1 回で引くので、問い合わせの数は明細の行の数で増えない。
明細の tabpanel は、ページの aria-label="明細" から、その後の最初の </table> まで (前にある基本情報の小計と合計を含めない)。
作り物のデータは全部 services で作る。名前と品番は作り物だけ。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.billing import services as billing_services
from seizou.billing.reports import invoice_context
from seizou.core.templatetags.seizou_tags import comma
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

PASSWORD = "kumo-tests-2611"
#: 金額の閲覧だけを効かせる (部署と書く権限の判定はしない。tests/test_core_money_hidden.py と同じ)
MONEY_ON = {"department": "off", "write": "off", "money": "on"}
ISSUED_ON = date(2026, 11, 30)
HEADINGS = ["出荷日", "出荷番号", "品番", "品名", "数量", "単位", "単価", "金額"]


@pytest.fixture
def money_on(settings):
    settings.SEIZOU = {"access_control": dict(MONEY_ON)}


def _account(login_id, **flags):
    """作り物の利用者 (部署も権限も無し)。see_money=True で金額の閲覧あり。"""
    accounts_services.issue_account(login_id, PASSWORD, [], [], flags, None)
    return get_user_model().objects.get(username=login_id)


@pytest.fixture
def jimu(client, db):
    """金額の閲覧の無い利用者でログインした client。"""
    client.force_login(_account("jimu"))
    return client


@pytest.fixture
def keiri(db):
    """金額の閲覧のある利用者でログインした client (jimu と別の client)。"""
    other = Client()
    other.force_login(_account("keiri", see_money=True))
    return other


def _customer(user, code="C017"):
    return masters_services.create_partner({"code": code, "name": f"作り物の得意先{code}", "role": "customer"}, user)


def _order_line(user, customer, item, unit_price):
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal(unit_price)}], user, order_date=date(2026, 11, 2)
    )
    return order.lines.get()


@pytest.fixture
def invoice(user):
    """出荷 2 つ (数量 3 と 4。単価 1,375) をまとめた請求書。"""
    customer = _customer(user)
    item = masters_services.create_item({"part_no": "C17-G01", "name": "作り物の歯車", "unit": "本"}, user)
    line = _order_line(user, customer, item, "1375")
    first = shipped(user, customer, line, "3")
    second = shipped(user, customer, line, "4")
    return billing_services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)


def _panel(response) -> str:
    """明細の tabpanel: aria-label="明細" から、その後の最初の </table> まで。"""
    assert response.status_code == 200
    html = response.content.decode()
    start = html.index('aria-label="明細"')
    return html[start : html.index("</table>", start) + len("</table>")]


def _headings(panel) -> list[str]:
    return [re.sub(r"<[^>]+>", "", cell).strip() for cell in re.findall(r"<th[^>]*>(.*?)</th>", panel, re.S)]


def _rows(panel) -> list[list[str]]:
    """tbody の行ごとの欄の字 (タグを落とした字)。"""
    body = panel[panel.index("<tbody>") :]
    return [
        [re.sub(r"<[^>]+>", "", cell).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)
    ]


# ---------------------------------------------------------------- 1. 金額の閲覧のある人


@pytest.mark.django_db
def test_lines_tab_is_table_in_print_order(money_on, keiri, invoice):
    panel = _panel(keiri.get(reverse("billing:invoice_detail", args=[invoice.pk])))

    assert _headings(panel) == HEADINGS
    printed = invoice_context(invoice)["lines"]
    assert len(printed) == 2
    # 行の並びと字は印刷用の画面の表と同じ (数量・単価・金額は明細の写しを comma で出した字)
    assert _rows(panel) == [
        [
            f"{row['shipped_at']:%Y-%m-%d}",
            row["shipment_no"],
            row["item_code"],
            row["item_name"],
            comma(row["quantity"]),
            row["unit"],
            f"{comma(row['unit_price'])} / {row['price_unit']}",
            comma(row["amount"]),
        ]
        for row in printed
    ]
    for invoice_line in invoice.lines.all():
        assert invoice_line.shipment.shipment_no in panel
        assert comma(invoice_line.amount) in panel
        assert str(invoice_line) not in panel  # 「<請求書番号> <出荷番号>」の行の字 (InvoiceLine.__str__) は出さない
    assert "作り物の歯車" in panel
    # 品番は部品の小窓を開く link
    item = invoice.lines.first().shipment.order_line.item
    assert reverse("masters:item_modal", args=[item.pk]) in panel
    # 足した表にボタンは無い (tabpanel の右上の「?」は土台の物なので、表の中だけを見る)
    assert "btn-" not in panel[panel.index("<table") :]


# ---------------------------------------------------------------- 2. 金額の閲覧の無い人


@pytest.mark.django_db
def test_lines_tab_hides_price_and_amount_columns_without_money(money_on, jimu, invoice):
    panel = _panel(jimu.get(reverse("billing:invoice_detail", args=[invoice.pk])))

    assert _headings(panel) == HEADINGS[:6]
    assert "品名" in panel and "数量" in panel
    assert "単価" not in panel and "金額" not in panel
    assert [len(row) for row in _rows(panel)] == [6, 6]
    for invoice_line in invoice.lines.all():
        assert invoice_line.shipment.shipment_no in panel
        assert comma(invoice_line.unit_price) not in panel
        assert comma(invoice_line.amount) not in panel
    assert "—" not in panel


# ---------------------------------------------------------------- 3. 問い合わせの数


def _queries(client, url) -> int:
    """url を開いた 1 回の要求の問い合わせの数 (前に 1 回開いて、設定の控えを読み終えておく)。"""
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200
    return len(ctx.captured_queries)


@pytest.mark.django_db
def test_lines_tab_queries_do_not_grow_with_rows(money_on, keiri, user):
    item = masters_services.create_item({"part_no": "C17-P01", "name": "作り物の板", "unit": "枚"}, user)
    customer = _customer(user)
    # 明細 5 行: 数量 10 の受注の明細から、数量 2 の出荷を 5 回 (出荷の和は受注の数量以下)
    five_line = _order_line(user, customer, item, "800")
    five = billing_services.create_invoice(
        customer, [shipped(user, customer, five_line, "2") for _ in range(5)], user, issued_on=ISSUED_ON
    )
    # 明細 1 行: さらに別の受注の明細から。出荷は前の月 (同じ得意先の同じ締め期間の請求書は 1 枚だけ = R23)
    one_line = _order_line(user, customer, item, "800")
    one = billing_services.create_invoice(
        customer, [shipped(user, customer, one_line, "2", date(2026, 10, 5))], user, issued_on=ISSUED_ON
    )
    assert one.lines.count() == 1 and five.lines.count() == 5

    one_url = reverse("billing:invoice_detail", args=[one.pk])
    five_url = reverse("billing:invoice_detail", args=[five.pk])
    for url in (one_url, five_url):  # 最初の 1 回の控えを外す
        assert keiri.get(url).status_code == 200

    assert _queries(keiri, five_url) == _queries(keiri, one_url)
    panel = _panel(keiri.get(five_url))
    assert len(_rows(panel)) == 5
    for invoice_line in five.lines.all():
        assert invoice_line.shipment.shipment_no in panel
