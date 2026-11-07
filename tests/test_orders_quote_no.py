"""受注の明細の見積の番号と工程名 (C100 の その 2)。2 つの列の保存・入力の画面の欄と「単価表から選ぶ」の置き場・金額の閲覧の門。

決め: 単価表から写した見積の番号を明細ごとに持つ (PRICE-0)・受信した注文の工程名を受注の明細に出す (PRICE-5)。名前は作り物だけ。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import Order

ORDER_DATE = date(2026, 11, 2)
MONEY_ON = {"access_control": {"department": "off", "write": "off", "money": "on"}}
PICK_URL_ZERO = "/masters/items/0/prices/pick/"


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _management(total, initial):
    return {
        "lines-TOTAL_FORMS": str(total),
        "lines-INITIAL_FORMS": str(initial),
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
    }


def _order(user, customer, shaft, **line):
    return services.create_order(
        customer,
        [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100"), **line}],
        user,
        order_date=ORDER_DATE,
    )


def _cell(body, name):
    """name の欄がある <td> の片 (次の <td まで)。"""
    return next(part for part in body.split("<td") if f'name="{name}"' in part)


def _pick_button(cell):
    start = cell.index("<button")
    return cell[start : cell.index(">", start)]


# ---------------------------------------------------------------- (a) (b) services


@pytest.mark.django_db
def test_create_and_update_keep_quote_no_and_process_name(user, customer, shaft):
    order = _order(user, customer, shaft, quote_no="Q-1", process_name="作り物の工程")
    line = order.lines.get()
    assert (line.quote_no, line.process_name) == ("Q-1", "作り物の工程")

    plain = _order(user, customer, shaft).lines.get()
    assert (plain.quote_no, plain.process_name) == ("", "")

    services.update_order(order, {}, [{"order_line": line, "item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100"), "quote_no": "Q-2"}], user)
    line.refresh_from_db()
    assert (line.quote_no, line.process_name) == ("Q-2", "作り物の工程")
    services.update_order(order, {}, [{"order_line": line, "item": shaft, "qty": Decimal("12"), "unit_price": Decimal("100")}], user)
    line.refresh_from_db()
    assert (line.qty, line.quote_no, line.process_name) == (Decimal("12"), "Q-2", "作り物の工程")


# ---------------------------------------------------------------- (c) 入力の画面の置き場


@pytest.mark.django_db
def test_create_screen_puts_quote_no_and_pick_button_in_price_cell(client_logged_in):
    body = client_logged_in.get(reverse("orders:order_create")).content.decode()
    assert reverse("masters:item_price_pick", args=[0]) == PICK_URL_ZERO
    cell = _cell(body, "lines-0-unit_price")
    assert 'name="lines-0-quote_no"' in cell
    button = _pick_button(cell)
    for word in (f'data-price-pick="{PICK_URL_ZERO}"', 'data-price-kind="sale"', 'data-price-partner-field="customer"', 'data-price-date-field="order_date"', "disabled"):
        assert word in button, word
    template_cell = _cell(body, "lines-__prefix__-unit_price")
    assert 'name="lines-__prefix__-quote_no"' in template_cell and "disabled" in _pick_button(template_cell)
    head = body[body.index("<thead>") : body.index("</thead>")]
    assert "見積の番号" not in head
    assert body.count('id="price-pick"') == 1 and body.count("masters/price-pick.js") == 1
    assert body.index("</form>") < body.index('id="price-pick"')


# ---------------------------------------------------------------- (d) (e) 保存と詳細と編集


@pytest.mark.django_db
def test_create_post_saves_quote_no_and_detail_shows_it(client_logged_in, user, customer, shaft):
    posted = client_logged_in.post(
        reverse("orders:order_create"),
        {
            "customer": customer.pk,
            "order_date": "2026-11-02",
            **_management(1, 0),
            "lines-0-item": shaft.pk,
            "lines-0-qty": "2",
            "lines-0-unit_price": "100",
            "lines-0-quote_no": "Q-1",
        },
    )
    assert posted.status_code == 302
    order = Order.objects.get()
    line = order.lines.get()
    assert line.quote_no == "Q-1"
    body = client_logged_in.get(reverse("orders:order_detail", args=[order.pk])).content.decode()
    assert "見積の番号" in body and "Q-1" in body

    edit = client_logged_in.get(reverse("orders:order_update", args=[order.pk])).content.decode()
    cell = _cell(edit, "lines-0-unit_price")
    assert 'name="lines-0-quote_no"' in cell and 'value="Q-1"' in cell
    assert "disabled" not in _pick_button(cell)

    received = _order(user, customer, shaft, process_name="作り物の工程")
    body = client_logged_in.get(reverse("orders:order_detail", args=[received.pk])).content.decode()
    assert "工程 作り物の工程" in body


# ---------------------------------------------------------------- (f) (g) 金額の閲覧と使わない項目


@pytest.mark.django_db
def test_people_without_money_see_no_quote_no(client_logged_in, user, customer, shaft, settings):
    order = _order(user, customer, shaft, quote_no="Q-1")
    line = order.lines.get()
    settings.SEIZOU = MONEY_ON
    for url in (reverse("orders:order_create"), reverse("orders:order_update", args=[order.pk])):
        body = client_logged_in.get(url).content.decode()
        assert "-quote_no" not in body and "data-price-pick" not in body, url

    posted = client_logged_in.post(
        reverse("orders:order_update", args=[order.pk]),
        {
            "customer": customer.pk,
            "order_date": ORDER_DATE.isoformat(),
            "customer_order_no": "",
            "note": "",
            **_management(1, 1),
            "lines-0-order_line": line.pk,
            "lines-0-item": shaft.pk,
            "lines-0-qty": "12",
            "lines-0-unit": line.unit_code,  # 単位の欄は数量単位の一覧から選ぶ欄 (C108 = 405 回目)
            "lines-0-due_date": "",
            "lines-0-note": "",
        },
    )
    # 受注の編集の保存は金額の門が断る (決め 11-07 21:0x の 4 の続き)。見積の番号は欄が無くても変わらない。
    assert posted.status_code == 200
    assert "受注は金額が主な内容のため、金額の閲覧の権限が無い方は変更できません。" in posted.content.decode()
    line.refresh_from_db()
    assert (line.qty, line.quote_no) == (Decimal("10"), "Q-1")


@pytest.mark.django_db
def test_hidden_quote_no_keeps_pick_button(client_logged_in, settings):
    settings.SEIZOU = {"hidden_fields": ["quote_no"]}
    body = client_logged_in.get(reverse("orders:order_create")).content.decode()
    assert "-quote_no" not in body
    assert "data-price-pick" in _cell(body, "lines-0-unit_price")
