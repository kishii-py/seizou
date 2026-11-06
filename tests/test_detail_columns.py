"""受注と発注の詳細の 2 列 (案 3-D・8-B・3-F。N150)。

右の列のカード (相手・日付・金額・基本情報)、基本情報のタブが右の列へ移った事、金額の閲覧の無い人に金額のカードが無い事、
題の上のパンくず、ほかの詳細 (部品・取引先) は今の 1 列のまま、を見る。作り物のデータは全部 services で作る。
"""

import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.connect import services as connect_services
from seizou.connect.models import Inbox
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing import services as purchasing_services
from tests.test_r10_inbox_accept import _line, _payload

PASSWORD = "kumo-tests-2611"
#: 金額の閲覧だけを効かせる (部署と書く権限の判定はしない)
MONEY_ON = {"department": "off", "write": "off", "money": "on"}
#: 1 列の詳細の tabs の箱 (上の余白を足さない = 今のまま)
ONE_COLUMN_TABS = 'class="tabs tabs-box relative gap-2 rounded-xl border border-base-300 bg-base-100 p-2 pr-12 shadow-xs"'


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def order(user, customer, item):
    """明細 2 行 (10 × 1,500 と 2 × 300 = 合計 15,600)。納期は 11-20 と 11-12 (一番早いのは 2 行目)。"""
    return orders_services.create_order(
        customer,
        [
            {"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500"), "due_date": date(2026, 11, 20)},
            {"item": item, "qty": Decimal("2"), "unit_price": Decimal("300"), "due_date": date(2026, 11, 12)},
        ],
        user,
        order_date=date(2026, 11, 2),
    )


@pytest.fixture
def purchase_order(user, subcontractor, item):
    """「作成」の発注 (送らない)。明細 1 行 10 × 100 = 1,000、納期 11-09。"""
    return purchasing_services.create_purchase_order(
        subcontractor,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": date(2026, 11, 9)}],
        user,
        order_date=date(2026, 11, 2),
        send=False,
    )


def _get(client, name, pk):
    response = client.get(reverse(name, args=[pk]))
    assert response.status_code == 200
    return response, response.content.decode()


def _aside(body):
    start = body.index('<aside id="detail-aside"')
    return body[start : body.index("</aside>", start)]


def _titles(aside):
    return re.findall(r'<h2 class="text-sm font-bold">([^<]+)</h2>', aside)


def _card(aside, title):
    return re.search(rf'<section [^>]*aria-label="{title}">.*?</section>', aside, re.S).group(0)


def _labels(card):
    return re.findall(r'<dt class="text-xs text-base-content/70">([^<]+)</dt>', card)


def _radios(body):
    """詳細の区分のタブの radio の (名前, 選んであるか) の並び。"""
    found = re.findall(r'<input type="radio"( checked)? name="detail-tabs" aria-label="([^"]+)">', body)
    return [(label, bool(checked)) for checked, label in found]


def _crumb(body):
    return re.search(r'<nav aria-label="パンくず"[^>]*>.*?</nav>', body, re.S).group(0)


def _account(login_id):
    """金額の閲覧の無い作り物の利用者 (部署も権限も無し)。"""
    accounts_services.issue_account(login_id, PASSWORD, [], [], {}, None)
    return get_user_model().objects.get(username=login_id)


@pytest.mark.django_db
def test_order_detail_has_two_columns_in_order(logged_in, order):
    body = _get(logged_in, "orders:order_detail", order.pk)[1]

    start = body.index('<div id="detail-columns"')
    assert "lg:grid-cols-[minmax(0,1fr)_20rem]" in body[start : body.index(">", start)]
    marks = (
        '<h1 class="truncate',
        '<div id="order-stages"',
        '<div id="detail-columns"',
        '<aside id="detail-aside"',
        'id="order-lines-title"',
        'id="order-flow-title"',
        'id="notes-heading"',
        'role="tablist"',
        '<section id="attachments"',
    )
    positions = [body.index(mark) for mark in marks]
    assert positions == sorted(positions)


@pytest.mark.django_db
def test_order_aside_cards(logged_in, order):
    aside = _aside(_get(logged_in, "orders:order_detail", order.pk)[1])

    assert _titles(aside) == ["得意先", "日付", "金額", "基本情報"]
    partner = _card(aside, "得意先")
    assert _labels(partner) == ["名前", "取引先コード"]
    assert "master-link" in partner and "作り物の得意先" in partner and "C001" in partner
    dates = _card(aside, "日付")
    assert _labels(dates) == ["受注日", "一番早い納期"]
    assert "2026-11-02" in dates and "2026-11-12" in dates and "2026-11-20" not in dates
    money = _card(aside, "金額")
    assert _labels(money) == ["明細の合計"] and "15,600" in money
    basic = _card(aside, "基本情報")
    assert {"受注番号", "得意先の注文番号", "状態", "注文の入口", "備考"} <= set(_labels(basic))
    assert not {"得意先", "受注日"} & set(_labels(basic))
    assert '?part=basic"' in basic
    # 案 8-B: 項目の名は小さい灰の字、値は太い字
    assert aside.count('<dd class="min-w-0 break-words font-semibold">') == aside.count("<dt ")


@pytest.mark.django_db
def test_order_basic_tab_moved_to_aside(logged_in, order):
    response, body = _get(logged_in, "orders:order_detail", order.pk)

    assert 'aria-label="基本情報" checked' not in body
    assert _radios(body) == [("指示書", True), ("出荷", False), ("請求書", False)]
    assert ONE_COLUMN_TABS[:-1] + ' mt-6"' in body
    assert body.count('?part=basic"') == 1
    # 土台の項目 (文脈) は今のまま。右の列のカードへ移すのはテンプレートの tag
    assert [field["label"] for field in response.context["fields"]][:3] == ["受注番号", "得意先", "受注日"]


@pytest.mark.django_db
def test_breadcrumb_above_title(logged_in, order, purchase_order):
    for name, obj, word, list_name in (
        ("orders:order_detail", order, "受注", "orders:order_list"),
        ("purchasing:purchaseorder_detail", purchase_order, "発注", "purchasing:purchaseorder_list"),
    ):
        body = _get(logged_in, name, obj.pk)[1]
        crumb = _crumb(body)
        assert f'href="{reverse(list_name)}">{word}</a>' in crumb, name
        assert f'aria-current="page">{obj}</span>' in crumb, name
        assert body.index('<nav aria-label="パンくず"') < body.index('<h1 class="truncate'), name
        assert "一覧に戻る" not in body, name  # パンくずがあれば出さない (N163)


@pytest.mark.django_db
def test_money_card_only_for_people_who_see_money(settings, client, order, purchase_order):
    settings.SEIZOU = {"access_control": dict(MONEY_ON)}
    client.force_login(_account("genba"))

    response, body = _get(client, "orders:order_detail", order.pk)
    assert bool(response.context["seizou"]["hide_money"]) is True
    assert _titles(_aside(body)) == ["得意先", "日付", "基本情報"]
    assert "15,600" not in body
    body = _get(client, "purchasing:purchaseorder_detail", purchase_order.pk)[1]
    assert _titles(_aside(body)) == ["協力企業", "日付", "基本情報"]


@pytest.mark.django_db
def test_purchase_order_detail_has_two_columns(logged_in, purchase_order):
    body = _get(logged_in, "purchasing:purchaseorder_detail", purchase_order.pk)[1]

    marks = (
        '<div id="po-heading"',
        '<div id="po-alert">',
        '<div id="detail-columns"',
        '<aside id="detail-aside"',
        '<div id="po-live">',
        'id="po-lines-title"',
        'id="po-flow-title"',
        'role="tablist"',
        'id="purchaseorder-detail-poll"',
    )
    positions = [body.index(mark) for mark in marks]
    assert positions == sorted(positions)
    aside = _aside(body)
    assert _titles(aside) == ["協力企業", "日付", "金額", "基本情報"]
    partner = _card(aside, "協力企業")
    assert _labels(partner) == ["名前", "取引先コード"] and "作り物の協力企業" in partner
    dates = _card(aside, "日付")
    assert _labels(dates) == ["発注日", "一番早い納期"] and "2026-11-09" in dates
    assert "1,000" in _card(aside, "金額")
    basic = set(_labels(_card(aside, "基本情報")))
    assert {"発注番号", "状態", "備考", "注文回答の日時", "断りの理由"} <= basic and not {"取引先", "発注日"} & basic
    assert _radios(body) == [("送った記録", True), ("受入", False)]
    assert 'hx-select-oob="#po-heading,#po-alert,#detail-aside"' in body


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["masters:item_detail", "masters:partner_detail"])
def test_other_details_keep_one_column(logged_in, item, customer, name):
    obj = item if name == "masters:item_detail" else customer
    body = _get(logged_in, name, obj.pk)[1]

    assert 'aria-label="パンくず"' in body  # 部品と取引先の詳細にもパンくず (N163)。1 列のままは下の 2 つで見る
    for mark in ('id="detail-columns"', 'id="detail-aside"'):
        assert mark not in body, (name, mark)
    assert '<input type="radio" name="detail-tabs" aria-label="基本情報" checked>基本情報' in body
    assert ONE_COLUMN_TABS in body


@pytest.mark.django_db
def test_order_aside_dates_show_buyer_order_date_for_inbox_order(logged_in, user, customer, item):
    """受信した注文から作った受注の「日付」のカードに「相手の発注日」が出る (初見テスト R5 の候補 C08 の 4)。

    受注日を編集で別の日に直しても、相手の発注日は受信した注文の payload の日のまま。
    """
    payload = _payload(str(uuid.uuid4()), [_line(1, "A-100")])
    payload.update(order_no="PO-000002", order_date="2026-11-03")
    inbox = connect_services.record_inbox(customer, payload, user)
    order = connect_services.accept(inbox, {"A-100": item}, user, send=False)

    dates = _card(_aside(_get(logged_in, "orders:order_detail", order.pk)[1]), "日付")
    assert _labels(dates) == ["受注日", "相手の発注日", "一番早い納期"]
    assert dates.count("2026-11-03") == 2

    orders_services.update_order(order, {"order_date": date(2026, 11, 5)}, [{"id": line.pk} for line in order.lines.all()], user)

    dates = _card(_aside(_get(logged_in, "orders:order_detail", order.pk)[1]), "日付")
    assert _labels(dates) == ["受注日", "相手の発注日", "一番早い納期"]
    assert "2026-11-05" in dates and dates.count("2026-11-03") == 1
    assert dates.index("2026-11-05") < dates.index("2026-11-03")


@pytest.mark.django_db
def test_order_aside_dates_skip_unreadable_buyer_order_date(logged_in, user, customer, item):
    """発注日の無い読み取りの行から作った受注は、今までどおり「受注日」「一番早い納期」の 2 行。"""
    payload = _payload(str(uuid.uuid4()), [_line(1, "A-100")])
    payload["order_date"] = None
    inbox = connect_services.record_inbox(customer, payload, user, source=Inbox.SOURCE_INTAKE, sender_name="作り物の差出人")
    order = connect_services.accept(inbox, {"A-100": item}, user, send=False)

    dates = _card(_aside(_get(logged_in, "orders:order_detail", order.pk)[1]), "日付")
    assert _labels(dates) == ["受注日", "一番早い納期"]
