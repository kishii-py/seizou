"""金額の閲覧が無い人には、単価と金額の列と項目そのものを出さない (本人の決め 11-05 09:4x。初見テスト R4 の 2-4。N155)。

金額の判定だけを on にした会社 (部署と書く権限の判定はしない) で、閲覧の無い作り物の利用者 (jimu) には、一覧の金額の列
(見出し・欄・並び替え・CSV)、詳細と小窓の金額の項目、受注・発注・請求書の詳細の明細の表の単価と金額の列、入金の枠の合計と残、
入金の記録の画面の合計と残、受入の一覧の支払の見込みの表、注文のデータと読み取りの明細の単価の列が出ず、「—」も出ないことを見る。
「まだ無い」の意味の「—」(検収の無い明細) は今のまま。閲覧のある利用者 (keiri) には今までどおり出る。
作り物のデータは全部 services で作る。相手の会社に HTTP を送らない (つながりなし。requests.post は呼んだら落とす物に差し替える)。
"""

import re
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.billing import services as billing_services
from seizou.core import access
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing import services as purchasing_services
from seizou.shipping import services as shipping_services
from seizou.shipping.models import Shipment

PASSWORD = "kumo-tests-2611"
#: 金額の閲覧だけを効かせる (部署と書く権限の判定はしない)
MONEY_ON = {"department": "off", "write": "off", "money": "on"}
#: N155 の前の、金額の枡の「—」の形 (明細の表の欄と入金の枠の値)
OLD_DASH_CELL = 'tabular-nums">—</td>'
OLD_DASH_VALUE = 'tabular-nums">—</dd>'


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", refuse)


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


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個", "standard_price": Decimal("1234")}, user
    )


@pytest.fixture
def order(user, customer, item):
    """明細 1 行 (10 × 1,500 = 15,000。納期 11-20)。検収はまだ無い (検収の枡は「まだ無い」の「—」)。"""
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500"), "due_date": date(2026, 11, 20)}],
        user,
        order_date=date(2026, 11, 2),
    )


@pytest.fixture
def invoice(user, customer, order):
    """出荷 4 の請求書 (小計 6,000) と、入金 1 件 (1,000)。"""
    shipment = shipping_services.ship(order.lines.get(), Decimal("4"), date(2026, 11, 5), user)
    shipping_services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=date(2026, 11, 30))
    billing_services.record_payment(invoice, date(2026, 12, 25), Decimal("1000"), user)
    return invoice


@pytest.fixture
def purchase_order(user, subcontractor, item):
    """「作成」の発注 (送らない)。明細 1 行 10 × 250 = 2,500 と、受入 4 (= 1,000)。"""
    po = purchasing_services.create_purchase_order(
        subcontractor,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("250"), "due_date": date(2026, 11, 9)}],
        user,
        order_date=date(2026, 11, 2),
        send=False,
    )
    purchasing_services.receive(po.lines.get(), Decimal("4"), date(2026, 11, 3), user)
    return po


def _get(client, url, **extra):
    response = client.get(url, **extra)
    assert response.status_code == 200, url
    return response, response.content.decode()


def _list_head(body):
    """一覧の表 (id="rows" の中) の見出しの HTML。行が無ければ空の字。"""
    start = body.index('<div id="rows"')
    head = body.find("<thead", start)
    return "" if head < 0 else body[head : body.index("</thead>", head)]


def _heads(body, marker):
    """marker の後の最初の表の見出しの字の並び (字だけの th)。"""
    start = body.index(marker)
    head = body[body.index("<thead", start) : body.index("</thead>", start)]
    return re.findall(r"<th[^>]*>([^<]*)</th>", head)


def _labels(response):
    return [str(field["label"]) for field in response.context["fields"]]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("name", "money", "labels"),
    [
        ("masters:item_list", {"standard_price"}, ("標準単価",)),
        ("billing:invoice_list", {"total", "balance"}, ("合計", "残")),
        ("purchasing:receipt_list", {"amount"}, ("金額",)),
        # 金額の列の無い一覧 (受注・生産管理ボード・発注) は今のまま
        ("orders:order_list", set(), ()),
        ("orders:orderline_list", set(), ()),
        ("purchasing:purchaseorder_list", set(), ()),
    ],
)
def test_list_drops_money_columns(money_on, jimu, keiri, invoice, purchase_order, name, money, labels):
    url = reverse(name)
    shown, _ = _get(keiri, url)
    shown_names = [spec.name for spec in shown.context["columns"]]
    assert money <= set(shown_names)

    hidden, body = _get(jimu, url)
    names = [spec.name for spec in hidden.context["columns"]]
    assert names == [n for n in shown_names if n not in money]
    head = _list_head(body)
    assert not [label for label in labels if label in head]
    for row in hidden.context["rows"]:
        assert len(row["cells"]) == len(names)
        assert access.MASK not in [cell["value"] for cell in row["cells"]]
    if money:
        # 並び替えの対象からも外れる (金額の列の名前の sort は読まずに既定の並び)
        assert _get(jimu, url, data={"sort": sorted(money)[0]})[0].context["sort"] == ""
        exported = jimu.get(url, {"format": "csv"}).content.decode("utf-8-sig").splitlines()[0]
        assert not [label for label in labels if label in exported.split(",")]


@pytest.mark.django_db
def test_details_and_modal_drop_money_fields(money_on, jimu, keiri, item, invoice, purchase_order):
    receipt = purchase_order.lines.get().receipts.get()
    cases = [
        (reverse("masters:item_detail", args=[item.pk]), ("標準単価",)),
        (reverse("billing:invoice_detail", args=[invoice.pk]), ("小計", "消費税", "合計")),
        (reverse("purchasing:receipt_detail", args=[receipt.pk]), ("金額",)),
        (reverse("shipping:shipment_detail", args=[Shipment.objects.get().pk]), ()),
    ]
    for url, labels in cases:
        hidden = _labels(_get(jimu, url)[0])
        assert not [label for label in hidden if label.startswith(labels or ("単価", "金額"))], url
        shown = _labels(_get(keiri, url)[0])
        assert all(any(label.startswith(word) for label in shown) for word in labels), url
        assert len(shown) - len(hidden) == len(labels), url

    modal = reverse("masters:item_modal", args=[item.pk])
    assert not [label for label in _labels(_get(jimu, modal, HTTP_HX_REQUEST="true")[0]) if label.startswith("標準単価")]
    assert [label for label in _labels(_get(keiri, modal, HTTP_HX_REQUEST="true")[0]) if label.startswith("標準単価")]


@pytest.mark.django_db
def test_order_detail_lines_have_no_price_columns(money_on, jimu, keiri, order):
    """初見テスト R4 の 2-4: 事務の人の受注の詳細の明細に、単価と金額の列が無い (「—」も無い)。検収の「—」(まだ無い) は残る。"""
    url = reverse("orders:order_detail", args=[order.pk])
    body = _get(jimu, url)[1]

    assert _heads(body, 'id="order-lines-title"') == ["操作", "行番号", "部品", "数量", "単位", "納期", "出荷済み", "検収", "預かり", "残"]
    assert OLD_DASH_CELL not in body
    assert "1,500" not in body and "15,000" not in body
    lines = body[body.index('id="order-lines-title"') : body.index("</section>", body.index('id="order-lines-title"'))]
    assert '<span class="text-base-content/50">—</span></td>' in lines

    shown = _get(keiri, url)[1]
    assert _heads(shown, 'id="order-lines-title"')[5:7] == ["単価", "金額"]
    assert "1,500" in shown and "15,000" in shown


@pytest.mark.django_db
def test_purchase_order_detail_lines_have_no_price_columns(money_on, jimu, keiri, user, purchase_order):
    # 受けすぎ (受入 4 + 8 = 12 > 10) の知らせの行は、表の列の数に合わせて横に広がる
    purchasing_services.receive(purchase_order.lines.get(), Decimal("8"), date(2026, 11, 4), user)
    url = reverse("purchasing:purchaseorder_detail", args=[purchase_order.pk])
    body = _get(jimu, url)[1]

    heads = _heads(body, 'id="po-lines-title"')
    assert heads == ["行番号", "部品", "数量", "単位", "受入の数", "受入の残", "出荷の知らせ", "納期", "備考", "操作"]
    assert OLD_DASH_CELL not in body
    assert '<td colspan="10">' in body and "2,500" not in body

    shown = _get(keiri, url)[1]
    assert _heads(shown, 'id="po-lines-title"')[4:6] == ["単価", "金額"]
    assert '<td colspan="12">' in shown and "2,500" in shown


@pytest.mark.django_db
def test_invoice_payments_and_payment_form_without_money(money_on, jimu, keiri, invoice):
    url = reverse("billing:invoice_detail", args=[invoice.pk])
    body = _get(jimu, url)[1]
    panel = body[body.index('<section id="payments"') : body.index("</section>", body.index('<section id="payments"'))]

    assert ">合計</dt>" not in panel and ">入金済み</dt>" not in panel and ">残</dt>" not in panel
    assert OLD_DASH_VALUE not in panel and OLD_DASH_CELL not in panel
    assert _heads(panel, 'id="payments-heading"') == ["入金日", "入金の方法", "備考", "記録した人", "記録の日時", "状態", "操作"]
    assert "一部入金" in panel

    shown = _get(keiri, url)[1]
    shown_panel = shown[shown.index('<section id="payments"') :]
    assert ">合計</dt>" in shown_panel and "金額" in _heads(shown_panel, 'id="payments-heading"')

    form = reverse("billing:payment_create", args=[invoice.pk])
    hidden_form = _get(jimu, form)[1]
    assert '合計 <span class="tabular-nums">' not in hidden_form and 'tabular-nums">—</span>' not in hidden_form
    assert '合計 <span class="tabular-nums">' in _get(keiri, form)[1]


@pytest.mark.django_db
def test_receipt_list_has_no_payable_table_without_money(money_on, jimu, keiri, purchase_order):
    url = reverse("purchasing:receipt_list")

    assert 'id="payable-title"' not in _get(jimu, url)[1]
    assert 'id="payable-title"' in _get(keiri, url)[1]


@pytest.mark.parametrize(
    ("template", "attr"),
    [("connect/_payload_lines.html", "payload"), ("intake/_lines.html", "extracted")],
)
def test_payload_and_extracted_lines_drop_unit_price(template, attr):
    """受信した注文の注文のデータの明細と、注文書の読み取りの明細 (どちらも詳細と受け入れの画面が include する断片)。"""
    line = {"line_no": 1, "part_no": "A-100", "part_name": "作り物の軸", "qty": "10", "unit": "個", "unit_price": "250", "due_date": "2026-11-20"}

    def render(lines, hide):
        return render_to_string(template, {"object": SimpleNamespace(**{attr: {"lines": lines}}), "seizou": {"hide_money": hide}})

    hidden, shown = render([line], True), render([line], False)
    heads = re.findall(r"<th[^>]*>([^<]*)</th>", hidden)
    assert len(heads) == 6 and "単価" not in heads
    heads = re.findall(r"<th[^>]*>([^<]*)</th>", shown)
    assert len(heads) == 7 and "単価" in heads
    assert ">250<" not in hidden and OLD_DASH_CELL not in hidden and ">250<" in shown
    assert 'colspan="6"' in render([], True) and 'colspan="7"' in render([], False)
