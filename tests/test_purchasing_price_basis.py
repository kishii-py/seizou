"""発注の明細の単価の単位 (決め PRICE-1b)。部品の単位 (unit) か式 (lump。金額に数量を掛けない)。

- services: 式の明細の金額は単価のまま・鍵の無い明細は部品の単位・知らない値の断り。
- 入力の画面: 見出しと明細の行の「単価の単位」の選ぶ欄・受注の明細から開いたときは部品の単位・合計の枠と保存のボタンの字・欄を送らない POST。
- 詳細: 単価の枡に「¥120,000 / 式」の形で単価の単位を添える。
作り物のデータだけを使う。
"""

import html
import re
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder, PurchaseOrderLine

ORDER_DATE = date(2026, 11, 2)


@pytest.fixture
def maker(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def jig(user):
    return masters_services.create_item({"part_no": "J-100", "name": "作り物の治具", "unit": "個"}, user)


def _button(body):
    match = re.search(r'<button class="btn btn-primary" type="submit"([^>]*)>(.*?)</button>', body, re.S)
    assert match, "保存のボタンが無い"
    return html.unescape(re.sub(r"<[^>]+>", "", match.group(2))).strip()


def _headers(body):
    thead = re.search(r"<thead>(.*?)</thead>", body, re.S).group(1)
    cells = re.findall(r"<th[^>]*>(.*?)</th>", thead, re.S)
    return [re.sub(r"<span[^>]*>.*?</span>", "", cell, flags=re.S).strip() for cell in cells]


def _select(body, name):
    match = re.search(rf'<select name="{re.escape(name)}"([^>]*)>(.*?)</select>', body, re.S)
    assert match, f"{name} の選ぶ欄が無い"
    return match.group(1), match.group(2)


def _post(maker, *rows, partner=True):
    """発注の新規登録の POST。rows は (部品, 数量, 単価, 単価の単位 か None) の並び。None はその欄を送らない。"""
    data = {
        "partner": str(maker.pk) if partner else "",
        "order_date": ORDER_DATE.isoformat(),
        "note": "",
        "lines-TOTAL_FORMS": str(len(rows)),
        "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
    }
    for index, (part, qty, price, basis) in enumerate(rows):
        data.update(
            {
                f"lines-{index}-item": str(part.pk),
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
def test_create_purchase_order_lump_and_unit(user, maker, jig):
    purchase_order = services.create_purchase_order(
        maker,
        [
            {"item": jig, "qty": Decimal("5"), "unit_price": Decimal("120000"), "price_basis": "lump"},
            {"item": jig, "qty": Decimal("5"), "unit_price": Decimal("300")},
        ],
        user,
        order_date=ORDER_DATE,
        send=False,
    )
    lump, unit = purchase_order.lines.order_by("line_no")
    assert (lump.price_basis, lump.amount) == ("lump", Decimal("120000"))
    assert (unit.price_basis, unit.amount) == ("unit", Decimal("1500"))

    before = PurchaseOrder.objects.count()
    with pytest.raises(ValueError) as caught:
        services.create_purchase_order(
            maker,
            [{"item": jig, "qty": Decimal("5"), "unit_price": Decimal("300"), "price_basis": "set"}],
            user,
            order_date=ORDER_DATE,
            send=False,
        )
    assert str(caught.value).startswith("保存できません。単価の単位")
    assert PurchaseOrder.objects.count() == before


# --- 入力の画面 ---


@pytest.mark.django_db
def test_create_form_has_price_basis_select(logged_in):
    body = logged_in.get(reverse("purchasing:purchaseorder_create")).content.decode()
    headers = _headers(body)
    start = headers.index("単位")
    assert headers[start : start + 3] == ["単位", "単価の単位", "単価"]
    attrs, options = _select(body, "lines-0-price_basis")
    assert 'data-label-lump="lump"' in attrs
    assert '<option value="unit" selected>' in options
    assert re.findall(r"<option[^>]*>([^<]*)</option>", options) == ["部品の単位", "式"]
    attrs, _options = _select(body, "lines-__prefix__-price_basis")
    assert 'data-label-lump="lump"' in attrs


@pytest.mark.django_db
def test_from_lump_order_line_starts_with_unit(logged_in, user, customer, jig):
    order = orders_services.create_order(
        customer,
        [{"item": jig, "qty": Decimal("5"), "unit_price": Decimal("200000"), "price_basis": "lump"}],
        user,
        order_date=ORDER_DATE,
    )
    url = f"{reverse('purchasing:purchaseorder_create')}?from_line={order.lines.get().pk}"
    body = logged_in.get(url).content.decode()
    _attrs, options = _select(body, "lines-0-price_basis")
    # 受注の単価の単位は写さない (協力企業との値決めは得意先との一式と別)。
    assert '<option value="unit" selected>' in options
    assert '<option value="lump" selected>' not in options


@pytest.mark.django_db
def test_post_lump_line_and_detail(logged_in, maker, jig):
    url = reverse("purchasing:purchaseorder_create")

    # 取引先が空で止まった形: ボタンの字は式の行を数量倍しない。
    body = logged_in.post(url, _post(maker, (jig, "5", "120000", "lump"), partner=False)).content.decode()
    assert _button(body) == "1 行・合計 ¥120,000 で発注する"

    response = logged_in.post(url, _post(maker, (jig, "5", "120000", "lump")))
    assert response.status_code == 302
    line = PurchaseOrderLine.objects.get()
    assert (line.price_basis, line.qty, line.amount) == ("lump", Decimal("5"), Decimal("120000"))

    body = logged_in.get(reverse("purchasing:purchaseorder_detail", args=[line.purchase_order_id])).content.decode()
    assert '>¥120,000<span class="text-base-content/70"> / 式</span>' in body


@pytest.mark.django_db
def test_post_without_price_basis_is_unit(logged_in, maker, jig):
    # 欄を送らない POST (今の試験の形)。2 行目は何も送らない空の行で、今と同じく飛ぶ。
    data = _post(maker, (jig, "5", "300", None))
    data["lines-TOTAL_FORMS"] = "2"
    response = logged_in.post(reverse("purchasing:purchaseorder_create"), data)
    assert response.status_code == 302
    line = PurchaseOrderLine.objects.get()
    assert (line.price_basis, line.amount) == ("unit", Decimal("1500"))
