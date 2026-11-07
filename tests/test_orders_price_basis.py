"""受注の明細の単価の単位 (決め PRICE-1b)。部品の単位 (unit) か式 (lump。金額に数量を掛けない)。

- services: 知らない値の断り・鍵の無い明細は部品の単位・式の明細の金額は単価のまま。
- 入力の画面: 明細の行の「単価の単位」の選ぶ欄 (初めは部品の単位)・合計の枠と保存のボタンの字・欄を送らない POST。
- 金額の閲覧の無い人: 欄を出さず、直す保存で今の値のまま。
- 詳細: 単価の枡に「¥50,000 / 式」「¥250 / 個」の形で単価の単位を添える。
作り物のデータだけを使う。合計の枠とボタンの字の読み方は tests/test_core_line_totals.py の _frame と _button と同じ。
"""

import html
import re
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import Order, OrderLine

ORDER_DATE = date(2026, 11, 2)
NO_MONEY = {"department": "off", "write": "off", "money": "on"}
FRAME = re.compile(r"data-line-totals(?=[\s>])")


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def lump_order(user, customer, shaft):
    """式の明細 1 行 (数量 300・単価 50000) の受注。"""
    return services.create_order(
        customer,
        [{"item": shaft, "qty": Decimal("300"), "unit_price": Decimal("50000"), "price_basis": "lump"}],
        user,
        order_date=ORDER_DATE,
    )


def _button(body):
    match = re.search(r'<button class="btn btn-primary" type="submit"([^>]*)>(.*?)</button>', body, re.S)
    assert match, "保存のボタンが無い"
    return html.unescape(re.sub(r"<[^>]+>", "", match.group(2))).strip()


def _frame(body):
    assert len(FRAME.findall(body)) == 1, "合計の枠が 1 つでない"
    found = {}
    for name in ("rows", "qty", "amount"):
        match = re.search(rf"data-line-totals-{name}>([^<]*)</span>", body)
        found[name] = match.group(1) if match else None
    return found


def _headers(body):
    thead = re.search(r"<thead>(.*?)</thead>", body, re.S).group(1)
    cells = re.findall(r"<th[^>]*>(.*?)</th>", thead, re.S)
    return [re.sub(r"<span[^>]*>.*?</span>", "", cell, flags=re.S).strip() for cell in cells]


def _select(body, name):
    match = re.search(rf'<select name="{re.escape(name)}"([^>]*)>(.*?)</select>', body, re.S)
    assert match, f"{name} の選ぶ欄が無い"
    return match.group(1), match.group(2)


def _create_post(customer, *rows):
    """受注の新規登録の POST。rows は (部品, 数量, 単価, 単価の単位 か None) の並び。None はその欄を送らない。"""
    data = {
        "customer": str(customer.pk) if customer is not None else "",
        "order_date": ORDER_DATE.isoformat(),
        "customer_order_no": "",
        "lines-TOTAL_FORMS": str(len(rows)),
        "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
    }
    for index, (part, qty, price, basis) in enumerate(rows):
        data.update(
            {
                f"lines-{index}-order_line": "",
                f"lines-{index}-item": str(part.pk),
                f"lines-{index}-customer_part_no": "",
                f"lines-{index}-qty": qty,
                f"lines-{index}-unit": "",
                f"lines-{index}-unit_price": price,
                f"lines-{index}-due_date": "",
                f"lines-{index}-note": "",
            }
        )
        if basis is not None:
            data[f"lines-{index}-price_basis"] = basis
    return data


# --- services ---


@pytest.mark.django_db
def test_create_order_refuses_unknown_price_basis(user, customer, shaft):
    before = Order.objects.count()
    with pytest.raises(ValueError) as caught:
        services.create_order(
            customer,
            [{"item": shaft, "qty": Decimal("3"), "unit_price": Decimal("100"), "price_basis": "set"}],
            user,
            order_date=ORDER_DATE,
        )
    assert str(caught.value).startswith("保存できません。単価の単位")
    assert Order.objects.count() == before


def test_price_unit_text():
    assert OrderLine(price_basis="lump", unit="個").price_unit_text == "式"
    assert OrderLine(price_basis="unit", unit="kg").price_unit_text == "kg"
    assert OrderLine(price_basis="unit", unit="").price_unit_text == "個"


# --- 入力の画面 ---


@pytest.mark.django_db
def test_create_form_has_price_basis_select(logged_in):
    body = logged_in.get(reverse("orders:order_create")).content.decode()
    attrs, options = _select(body, "lines-0-price_basis")
    assert 'data-label-lump="lump"' in attrs
    assert re.findall(r"<option[^>]*>([^<]*)</option>", options) == ["部品の単位", "式"]
    assert '<option value="unit" selected>' in options
    # 「行を足す」の雛形の行にもある。
    attrs, _options = _select(body, "lines-__prefix__-price_basis")
    assert 'data-label-lump="lump"' in attrs
    # 列の並びは 単位・単価の単位・単価・金額 (単価の単位で金額の数え方が変わるので、単価の前に選ぶ)。
    headers = _headers(body)
    start = headers.index("単位")
    assert headers[start : start + 4] == ["単位", "単価の単位", "単価", "金額"]


@pytest.mark.django_db
def test_create_lump_line_saves_unit_price_as_amount(logged_in, customer, shaft):
    url = reverse("orders:order_create")

    # 得意先が空で止まった形: 合計の枠とボタンは式の行を数量倍しない。
    body = logged_in.post(url, _create_post(None, (shaft, "300", "50000", "lump"))).content.decode()
    assert _frame(body) == {"rows": "1", "qty": "300", "amount": "50,000"}
    assert _button(body) == "1 行・合計 ¥50,000 で受注する"

    response = logged_in.post(url, _create_post(customer, (shaft, "300", "50000", "lump")))
    assert response.status_code == 302
    line = OrderLine.objects.get()
    assert (line.price_basis, line.qty, line.amount) == ("lump", Decimal("300"), Decimal("50000"))
    assert line.unit == "個"


@pytest.mark.django_db
def test_update_form_selects_lump(logged_in, lump_order):
    body = logged_in.get(reverse("orders:order_update", args=[lump_order.pk])).content.decode()
    _attrs, options = _select(body, "lines-0-price_basis")
    assert '<option value="lump" selected>' in options
    assert _frame(body) == {"rows": "1", "qty": "300", "amount": "50,000"}


@pytest.mark.django_db
def test_post_without_price_basis_is_unit(logged_in, customer, shaft):
    # 欄を送らない POST (今の試験の形)。2 行目は何も送らない空の行で、今と同じく飛ぶ。
    data = _create_post(customer, (shaft, "3", "100", None))
    data["lines-TOTAL_FORMS"] = "2"
    response = logged_in.post(reverse("orders:order_create"), data)
    assert response.status_code == 302
    line = OrderLine.objects.get()
    assert (line.price_basis, line.amount) == ("unit", Decimal("300"))


# --- 金額の閲覧の無い人 ---


@pytest.mark.django_db
def test_no_money_user_has_no_price_basis_and_keeps_it(settings, logged_in, lump_order):
    settings.SEIZOU = {"access_control": dict(NO_MONEY)}
    assert "price_basis" not in logged_in.get(reverse("orders:order_create")).content.decode()
    url = reverse("orders:order_update", args=[lump_order.pk])
    assert "price_basis" not in logged_in.get(url).content.decode()

    line = lump_order.lines.get()
    data = {
        "customer": str(lump_order.customer_id),
        "order_date": lump_order.order_date.isoformat(),
        "customer_order_no": "",
        "note": "",
        "lines-TOTAL_FORMS": "1",
        "lines-INITIAL_FORMS": "1",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
        "lines-0-order_line": str(line.pk),
        "lines-0-item": str(line.item_id),
        "lines-0-customer_part_no": "",
        "lines-0-qty": "200",
        "lines-0-unit": line.unit_code,  # 単位の欄は数量単位の一覧から選ぶ欄 (C108 = 405 回目)
        "lines-0-due_date": "",
        "lines-0-note": "",
    }
    response = logged_in.post(url, data)
    assert response.status_code == 302
    line.refresh_from_db()
    assert (line.qty, line.price_basis, line.amount) == (Decimal("200"), "lump", Decimal("50000"))


# --- 詳細 ---


@pytest.mark.django_db
def test_detail_shows_price_unit(settings, logged_in, user, customer, shaft):
    order = services.create_order(
        customer,
        [
            {"item": shaft, "qty": Decimal("300"), "unit_price": Decimal("50000"), "price_basis": "lump"},
            {"item": shaft, "qty": Decimal("300"), "unit_price": Decimal("250")},
            {"item": shaft, "qty": Decimal("2"), "unit_price": Decimal("700"), "unit": ""},
        ],
        user,
        order_date=ORDER_DATE,
    )
    url = reverse("orders:order_detail", args=[order.pk])
    body = logged_in.get(url).content.decode()
    assert '>¥50,000<span class="text-base-content/70"> / 式</span>' in body
    assert '>¥250<span class="text-base-content/70"> / 個</span>' in body
    assert '>¥700<span class="text-base-content/70"> / 個</span>' in body

    settings.SEIZOU = {"access_control": dict(NO_MONEY)}
    body = logged_in.get(url).content.decode()
    assert " / 式</span>" not in body
    assert "¥50,000" not in body
