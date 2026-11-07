"""単価表から選ぶ小窓 (C100 の その 1)。その日に効く行・並び・今の数量に合う帯・期限切れ・発注の工程の最初の値・小窓の字・金額の門。

決め: 取引先の行を先・共通を後 (PRICE-2)・受注日 (発注日) に効いている単価 (PRICE-3)・期限切れは印を付けて下 (PRICE-3b)・
今の数量に合う帯を上 (PRICE-0)・協力企業に出す工程が 1 つだけなら発注の工程の最初の値 (PRICE-5)。名前は作り物だけ。
"""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

import seizou.masters
from seizou.masters import services
from seizou.masters.models import Item, ItemPrice, Partner, Process

HX = {"HTTP_HX_REQUEST": "true"}
MONEY_ON = {"access_control": {"department": "off", "write": "off", "money": "on"}}
ON = date(2026, 5, 1)
PRICE_PICK_JS = Path(seizou.masters.__file__).resolve().parent / "static" / "masters" / "price-pick.js"


@pytest.fixture
def item(db):
    return Item.objects.create(part_no="T-100", name="作り物の軸", unit="個")


@pytest.fixture
def customer(db):
    return Partner.objects.create(code="C001", name="作り物の得意先", short_name="作り得", role="customer")


@pytest.fixture
def other_customer(db):
    return Partner.objects.create(code="C002", name="作り物のほかの得意先", role="customer")


@pytest.fixture
def supplier(db):
    return Partner.objects.create(code="S001", name="作り物の協力企業", role="subcontractor")


@pytest.fixture
def plating(db):
    return Process.objects.create(code="PL", name="作り物のめっき", is_outsourced=True, sort_order=2)


@pytest.fixture
def painting(db):
    return Process.objects.create(code="PT", name="作り物の塗装", is_outsourced=True, sort_order=3)


@pytest.fixture
def cutting(db):
    return Process.objects.create(code="CT", name="作り物の切削", is_outsourced=False, sort_order=1)


def _row(user, partner, item):
    return services.add_partner_item({"partner": partner, "item": item, "partner_part_no": "", "partner_item_name": ""}, user)


def _price(item, user, **values):
    data = {"item": item, "kind": ItemPrice.KIND_SALE, "unit_price": Decimal("1200"), **values}
    return services.add_item_price(data, user)


def _pks(rows):
    return [row.pk for row in rows]


# ---------------------------------------------------------------- (a) その日に効く行


@pytest.mark.django_db
def test_effective_prices_choose_newest_started_row(user, item, customer, other_customer, supplier):
    blank = _price(item, user, unit_price=Decimal("1000"))
    april = _price(item, user, unit_price=Decimal("1100"), start_date=date(2026, 4, 1))
    june = _price(item, user, unit_price=Decimal("1300"), start_date=date(2026, 6, 1))
    mine = _price(item, user, partner_item=_row(user, customer, item), unit_price=Decimal("900"))
    theirs = _price(item, user, partner_item=_row(user, other_customer, item), unit_price=Decimal("950"))
    bought = _price(item, user, partner_item=_row(user, supplier, item), kind=ItemPrice.KIND_PURCHASE, unit_price=Decimal("300"))

    may = services.effective_prices(item, kind=ItemPrice.KIND_SALE, partner=customer, on_date=ON)
    assert _pks(may) == [mine.pk, april.pk]
    assert june.pk not in _pks(may) and blank.pk not in _pks(may) and theirs.pk not in _pks(may)
    march = services.effective_prices(item, kind=ItemPrice.KIND_SALE, partner=customer, on_date=date(2026, 3, 1))
    assert _pks(march) == [mine.pk, blank.pk]
    assert _pks(services.effective_prices(item, kind=ItemPrice.KIND_SALE, partner=None, on_date=ON)) == [april.pk]
    assert _pks(services.effective_prices(item, kind=ItemPrice.KIND_PURCHASE, partner=supplier, on_date=ON)) == [bought.pk]


# ---------------------------------------------------------------- (b) 並び


@pytest.mark.django_db
def test_price_choices_order(user, item, customer):
    row = _row(user, customer, item)
    yesterday = timezone.localdate() - timedelta(days=1)
    partner_blank = _price(item, user, partner_item=row, unit_price=Decimal("1200"))
    partner_100 = _price(item, user, partner_item=row, min_qty=Decimal("100"), unit_price=Decimal("1000"))
    common = _price(item, user, unit_price=Decimal("1500"))
    expired = _price(item, user, partner_item=row, min_qty=Decimal("300"), unit_price=Decimal("900"), quote_expires=yesterday)

    choices = services.price_choices(item, kind=ItemPrice.KIND_SALE, partner=customer, on_date=ON, qty=Decimal("150"))
    assert [choice.price.pk for choice in choices] == [partner_100.pk, partner_blank.pk, common.pk, expired.pk]
    assert [choice.fits for choice in choices] == [True, False, True, False]
    assert [choice.expired for choice in choices] == [False, False, False, True]


# ---------------------------------------------------------------- (c) 今の数量に合う帯


@pytest.mark.django_db
def test_price_choices_fits(user, item, supplier, plating, painting):
    ten = _price(item, user, min_qty=Decimal("10"))
    hundred = _price(item, user, min_qty=Decimal("100"), unit_price=Decimal("1000"))

    def fitting(qty, kind=ItemPrice.KIND_SALE, partner=None):
        return {choice.price.pk for choice in services.price_choices(item, kind=kind, partner=partner, on_date=ON, qty=qty) if choice.fits}

    assert fitting(Decimal("9")) == set()
    assert fitting(Decimal("100")) == {hundred.pk}
    assert fitting(Decimal("99")) == {ten.pk}
    assert fitting(None) == set()

    purchase = {"kind": ItemPrice.KIND_PURCHASE}
    plate_blank = _price(item, user, process=plating, unit_price=Decimal("300"), **purchase)
    plate_50 = _price(item, user, process=plating, min_qty=Decimal("50"), unit_price=Decimal("280"), **purchase)
    paint_blank = _price(item, user, process=painting, unit_price=Decimal("400"), **purchase)
    _price(item, user, process=painting, min_qty=Decimal("200"), unit_price=Decimal("350"), **purchase)
    assert fitting(Decimal("100"), kind=ItemPrice.KIND_PURCHASE) == {plate_50.pk, paint_blank.pk}
    assert plate_blank.pk not in fitting(Decimal("100"), kind=ItemPrice.KIND_PURCHASE)


@pytest.mark.django_db
def test_expired_row_can_fit(user, item):
    yesterday = timezone.localdate() - timedelta(days=1)
    expired = _price(item, user, quote_expires=yesterday)
    choices = services.price_choices(item, kind=ItemPrice.KIND_SALE, on_date=ON, qty=Decimal("5"))
    assert [(choice.price.pk, choice.fits, choice.expired) for choice in choices] == [(expired.pk, True, True)]


# ---------------------------------------------------------------- (d) 期限切れは今日と比べる


@pytest.mark.django_db
def test_expired_is_judged_by_today(user, item):
    yesterday = timezone.localdate() - timedelta(days=1)
    _price(item, user, quote_expires=yesterday)
    choices = services.price_choices(item, kind=ItemPrice.KIND_SALE, on_date=yesterday - timedelta(days=30))
    assert [choice.expired for choice in choices] == [True]


# ---------------------------------------------------------------- (e) (f) 発注の工程の最初の値


@pytest.fixture
def process_items(user, plating, painting, cutting):
    one = Item.objects.create(part_no="T-201", name="作り物の板 1")
    two = Item.objects.create(part_no="T-202", name="作り物の板 2")
    none = Item.objects.create(part_no="T-203", name="作り物の板 3")
    stopped = Item.objects.create(part_no="T-204", name="作り物の板 4")
    bare = Item.objects.create(part_no="T-205", name="作り物の板 5")
    old = Process.objects.create(code="OL", name="作り物の止めた工程", is_outsourced=True, sort_order=9)
    Process.objects.filter(pk=old.pk).update(active=False)
    old.refresh_from_db()
    services.set_item_processes(one, [cutting, plating], user)
    services.set_item_processes(two, [plating, painting], user)
    services.set_item_processes(none, [cutting], user)
    services.set_item_processes(stopped, [old, painting], user)
    return {one: plating, two: None, none: None, stopped: painting, bare: None}


@pytest.mark.django_db
def test_default_process(process_items):
    for item, expected in process_items.items():
        assert services.default_process(item) == expected, item.part_no


@pytest.mark.django_db
def test_with_default_process_in_one_query(process_items, django_assert_num_queries):
    with django_assert_num_queries(1):
        rows = {row.pk: row.default_process for row in services.with_default_process(Item.objects.all())}
    for item, expected in process_items.items():
        assert rows[item.pk] == (expected.pk if expected else None), item.part_no


# ---------------------------------------------------------------- (g)〜(j) 小窓の画面


def _url(item, **query):
    url = reverse("masters:item_price_pick", args=[item.pk])
    return url + ("?" + "&".join(f"{key}={value}" for key, value in query.items()) if query else "")


@pytest.mark.django_db
def test_pick_screen_lists_rows(client_logged_in, user, item, customer, other_customer):
    row = _row(user, customer, item)
    yesterday = timezone.localdate() - timedelta(days=1)
    _price(item, user, partner_item=row, unit_price=Decimal("1200"))
    _price(item, user, partner_item=row, min_qty=Decimal("100"), unit_price=Decimal("1000"), quote_no="Q-1", quote_date=date(2026, 3, 20))
    _price(item, user, unit_price=Decimal("1500"))
    _price(item, user, partner_item=row, min_qty=Decimal("300"), unit_price=Decimal("900"), quote_expires=yesterday)
    _price(item, user, partner_item=_row(user, other_customer, item), unit_price=Decimal("7777"))

    response = client_logged_in.get(_url(item, kind="sale", partner=customer.pk, on="2026-05-01", qty="150"), **HX)
    assert response.status_code == 200
    html = response.content.decode()
    for word in (
        "単価表から選ぶ",
        "¥1,200 / 個",
        "¥1,000 / 個",
        "今の数量に合う",
        "期限切れ",
        "この取引先の単価",
        "共通の単価 (どの取引先にも同じ)",
        "見積の有効期限が切れた単価",
        "作り得・100 個から・見積 Q-1 (発行 2026-03-20)",
        "data-price-choice",
        'data-unit-price="1200.00"',
        'data-price-basis="unit"',
        'data-quote-no="Q-1"',
        "data-quote-no=",
        "2026-05-01 に効く単価・数量 150 個",
        "autofocus",
    ):
        assert word in html, word
    assert "¥7,777" not in html
    assert "data-process=" not in html
    # 段の並び (取引先 → 共通 → 期限切れ) と、取引先の段の中の並び (今の数量に合う 100 個からの行が先)
    assert html.index("この取引先の単価") < html.index("¥1,000 / 個") < html.index("¥1,200 / 個") < html.index("共通の単価")
    assert html.index("共通の単価") < html.index("¥1,500 / 個") < html.index("見積の有効期限が切れた単価") < html.index("¥900 / 個")

    response = client_logged_in.get(_url(item, kind="sale"))
    assert response.status_code == 302
    assert response["Location"] == reverse("masters:item_detail", args=[item.pk])


@pytest.mark.django_db
def test_pick_screen_without_partner_and_other_kinds(client_logged_in, user, item, customer, supplier, plating):
    _price(item, user, partner_item=_row(user, customer, item), unit_price=Decimal("1200"))
    _price(item, user, unit_price=Decimal("1500"))
    html = client_logged_in.get(_url(item, kind="sale", on="2026-05-01"), **HX).content.decode()
    assert "¥1,500 / 個" in html and "¥1,200 / 個" not in html
    assert "上の「得意先」を選ぶと、その得意先の単価も出ます。" in html
    assert "得意先 まだ選んでいません" in html

    _price(item, user, partner_item=_row(user, supplier, item), kind=ItemPrice.KIND_PURCHASE, process=plating, unit_price=Decimal("300"))
    html = client_logged_in.get(_url(item, kind="purchase", partner=supplier.pk, on="2026-05-01"), **HX).content.decode()
    assert "協力企業への発注・取引先" in html
    assert f'data-process="{plating.pk}"' in html
    assert "作り物のめっき" in html

    html = client_logged_in.get(_url(item, kind="xx"), **HX).content.decode()
    assert "alert-error" in html and "単価表を出せません。" in html
    assert "<h4" not in html and "data-price-choice" not in html

    bare = Item.objects.create(part_no="T-300", name="作り物の単価の無い部品")
    html = client_logged_in.get(_url(bare, kind="sale"), **HX).content.decode()
    assert "この部品の単価表に、売りの単価がありません。" in html
    assert reverse("masters:item_detail", args=[bare.pk]) in html and 'target="_blank"' in html


@pytest.mark.django_db
def test_pick_screen_hidden_from_people_without_money(client_logged_in, user, item, customer, settings):
    _price(item, user, partner_item=_row(user, customer, item), unit_price=Decimal("4321"))
    settings.SEIZOU = MONEY_ON
    with CaptureQueriesContext(connection) as queries:
        response = client_logged_in.get(_url(item, kind="sale", partner=customer.pk), **HX)
    html = response.content.decode()
    assert response.status_code == 200
    assert "¥" not in html and "data-price-choice" not in html and "4321" not in html
    assert "alert-error" in html and "単価表から選べません。" in html
    assert not [query["sql"] for query in queries.captured_queries if "masters_itemprice" in query["sql"]]


@pytest.mark.django_db
def test_pick_screen_queries_do_not_grow_with_rows(client_logged_in, user, customer, plating):
    def measure(count):
        target = Item.objects.create(part_no=f"T-4{count:02d}", name="作り物の数える部品")
        row = _row(user, customer, target)
        for number in range(count):
            _price(target, user, partner_item=row if number % 2 else None, min_qty=Decimal(number + 1), unit_price=Decimal("100") + number)
        url = _url(target, kind="sale", partner=customer.pk, qty="5")
        # 1 回目は控え (設定・休日・セッション) の読みを含むので、2 回目を数える
        assert client_logged_in.get(url, **HX).status_code == 200
        with CaptureQueriesContext(connection) as queries:
            assert client_logged_in.get(url, **HX).status_code == 200
        return len(queries.captured_queries)

    assert measure(2) == measure(10)


# ---------------------------------------------------------------- (k) JS の約束


def test_price_pick_js_promises():
    text = PRICE_PICK_JS.read_text(encoding="utf-8")
    for word in (
        "window.seizouPricePick",
        "[data-price-pick]",
        "[data-price-choice]",
        "'select[name$=\"-item\"]'",
        ".disabled =",
        '"#price-pick-box"',
        "showModal",
        'new Event("input", { bubbles: true })',
        'new Event("change", { bubbles: true })',
        'removeAttribute("data-copied")',
    ):
        assert word in text, word
    for word in ("innerHTML", "insertAdjacentHTML", "classList", "className"):
        assert word not in text, word
