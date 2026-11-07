"""発注の明細の協力企業の工程と見積の番号 (C100 の その 3)。工程の欄の選択肢と出し分け・最初の値・2 つの列の保存・候補の口の値。

決め: 発注の明細に工程の欄 (空でもよい。協力企業に出す工程から選ぶ。標準の工程に 1 つだけなら最初から入る) (PRICE-5)・
単価表から写した見積の番号を明細ごとに持つ (PRICE-0)。名前は作り物だけ。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder

MONEY_ON = {"access_control": {"department": "off", "write": "off", "money": "on"}}
ORDER_DATE = date(2026, 11, 2)


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def outside(db):
    return Process.objects.create(code="OUT1", name="作り物の外の工程", is_outsourced=True, sort_order=10)


@pytest.fixture
def processes(db, outside):
    second = Process.objects.create(code="OUT2", name="作り物の二つ目の外の工程", is_outsourced=True, sort_order=20)
    inside = Process.objects.create(code="IN1", name="作り物の社内の工程", is_outsourced=False, sort_order=1)
    stopped = Process.objects.create(code="OUT9", name="作り物の止めた外の工程", is_outsourced=True, sort_order=90)
    Process.objects.filter(pk=stopped.pk).update(active=False)
    return {"outside": outside, "second": second, "inside": inside, "stopped": stopped}


@pytest.fixture
def items(user, processes):
    single = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    double = masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user)
    masters_services.set_item_processes(single, [processes["inside"], processes["outside"]], user)
    masters_services.set_item_processes(double, [processes["outside"], processes["second"]], user)
    return single, double


def _management(total):
    return {"lines-TOTAL_FORMS": str(total), "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000"}


def _cell(body, name):
    return next(part for part in body.split("<td") if f'name="{name}"' in part)


def _select(body, name):
    start = body.index(f'<select name="{name}"')
    return body[start : body.index("</select>", start)]


# ---------------------------------------------------------------- (a) services


@pytest.mark.django_db
def test_create_keeps_process_and_quote_no(user, subcontractor, items, outside):
    single, double = items
    order = services.create_purchase_order(
        subcontractor,
        [
            {"item": single, "qty": Decimal("10"), "unit_price": Decimal("300"), "process": outside, "quote_no": "Q-1"},
            {"item": double, "qty": Decimal("3"), "unit_price": Decimal("100")},
        ],
        user,
        order_date=ORDER_DATE,
        send=False,
    )
    first, second = order.lines.order_by("line_no")
    assert (first.process, first.quote_no) == (outside, "Q-1")
    assert (second.process, second.quote_no) == (None, "")


# ---------------------------------------------------------------- (b) (c) 入力の画面の工程の欄


@pytest.mark.django_db
def test_create_screen_has_process_column(client_logged_in, processes):
    body = client_logged_in.get(reverse("purchasing:purchaseorder_create")).content.decode()
    head = body[body.index("<thead>") : body.index("</thead>")]
    assert re.search(r"部品 <span[^>]*>必須</span></th>\s*<th>工程</th>", head)
    options = re.findall(r"<option[^>]*>([^<]*)</option>", _select(body, "lines-0-process"))
    assert options == ["指定しない", str(processes["outside"]), str(processes["second"])]
    assert 'name="lines-__prefix__-process"' in body
    cell = _cell(body, "lines-0-unit_price")
    assert 'name="lines-0-quote_no"' in cell and 'data-price-kind="purchase"' in cell
    assert 'data-price-partner-field="partner"' in cell and 'data-price-date-field="order_date"' in cell
    assert body.index("</form>") < body.index('id="price-pick"')


@pytest.mark.django_db
def test_company_without_outsourced_process_has_no_column(client_logged_in, user, subcontractor):
    Process.objects.create(code="IN1", name="作り物の社内の工程", is_outsourced=False)
    # 単位は数量単位の一覧にある字 (C108 = 405 回目。一覧に無い「本」だと単位の欄の誤りで止まる。この試験は工程の欄を見る物)
    item = masters_services.create_item({"part_no": "T-300", "name": "作り物の棒", "unit": "個"}, user)
    body = client_logged_in.get(reverse("purchasing:purchaseorder_create")).content.decode()
    assert "<th>工程</th>" not in body and "-process" not in body
    posted = client_logged_in.post(
        reverse("purchasing:purchaseorder_create"),
        {"partner": subcontractor.pk, "order_date": "2026-11-02", **_management(1), "lines-0-item": item.pk, "lines-0-qty": "2", "lines-0-unit_price": "50"},
    )
    assert posted.status_code == 302
    line = PurchaseOrder.objects.get().lines.get()
    assert (line.process, line.quote_no) == (None, "")


# ---------------------------------------------------------------- (d) 盤から開いた発注の工程の最初の値


@pytest.mark.django_db
def test_board_lines_get_default_process_in_one_query(client_logged_in, user, customer, items, outside):
    single, double = items
    order = orders_services.create_order(
        customer,
        [
            {"item": single, "qty": Decimal("10"), "unit_price": Decimal("100")},
            {"item": double, "qty": Decimal("5"), "unit_price": Decimal("100")},
        ],
        user,
        order_date=ORDER_DATE,
    )
    first, second = order.lines.order_by("line_no")
    url = reverse("purchasing:purchaseorder_create")
    response = client_logged_in.get(f"{url}?line={first.pk}&line={second.pk}")
    assert [row["process"] for row in response.context["form"].lines.initial] == [outside.pk, None]
    body = response.content.decode()
    assert re.search(rf'<option value="{outside.pk}" selected>', _select(body, "lines-0-process"))
    assert " selected" not in _select(body, "lines-1-process").split("</option>", 1)[1]
    assert "disabled" not in _cell(body, "lines-0-unit_price")

    def process_queries(query):
        client_logged_in.get(f"{url}?{query}")
        with CaptureQueriesContext(connection) as queries:
            client_logged_in.get(f"{url}?{query}")
        return [each["sql"] for each in queries.captured_queries if "masters_itemprocess" in each["sql"]]

    assert len(process_queries(f"line={first.pk}")) == len(process_queries(f"line={first.pk}&line={second.pk}")) == 1


# ---------------------------------------------------------------- (e) 候補の口


@pytest.mark.django_db
def test_item_select_returns_default_process(client_logged_in, items, outside):
    single, double = items
    data = client_logged_in.get(reverse("purchasing:item_select")).json()
    assert {row["id"]: row["default_process"] for row in data["items"]} == {single.pk: outside.pk, double.pk: None}
    assert all("standard_price" not in row for row in data["items"])


# ---------------------------------------------------------------- (f) 保存と詳細


@pytest.mark.django_db
def test_post_saves_process_and_quote_no(client_logged_in, subcontractor, items, outside):
    single, _double = items
    posted = client_logged_in.post(
        reverse("purchasing:purchaseorder_create"),
        {
            "partner": subcontractor.pk,
            "order_date": "2026-11-02",
            **_management(1),
            "lines-0-item": single.pk,
            "lines-0-process": outside.pk,
            "lines-0-qty": "10",
            "lines-0-unit_price": "300",
            "lines-0-quote_no": "Q-1",
        },
    )
    assert posted.status_code == 302
    order = PurchaseOrder.objects.get()
    line = order.lines.get()
    assert (line.process, line.quote_no) == (outside, "Q-1")
    body = client_logged_in.get(reverse("purchasing:purchaseorder_detail", args=[order.pk])).content.decode()
    assert "工程 作り物の外の工程" in body and "見積の番号" in body and "Q-1" in body


# ---------------------------------------------------------------- (g) 金額の閲覧


@pytest.mark.django_db
def test_people_without_money_keep_quote_no_without_pick(client_logged_in, processes, settings):
    settings.SEIZOU = MONEY_ON
    body = client_logged_in.get(reverse("purchasing:purchaseorder_create")).content.decode()
    assert "data-price-pick" not in body
    assert 'name="lines-0-quote_no"' in body
