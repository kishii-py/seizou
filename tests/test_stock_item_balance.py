"""払い出しと棚卸しの調整で、部品を選んだら数の欄の下に今の在庫数を出す (初見テスト R5 の候補 C75)。

1. stock の読むだけの口 (stock:item_balance) が、部品の今の在庫数を末尾の 0 を落とした字か null で返す。
2. 口はログインが要り、GET だけで、応答を保存させない。無い部品は 404。
3. 払い出しと棚卸しの調整の画面の全体と小窓は stock/stock-form.js を 1 回読む。製造の完了を入庫にするは読まない。
4. JS は字を textContent で出し、HTML として差し込まず、聞き手を document に付けない。

ブラウザの動き (選んだときの字・差・小窓) は本体の手で確かめる。作り物のデータだけを使う。
"""

from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

import seizou.stock
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.stock import services
from seizou.workorders import services as workorders_services

STOCK_FORM_JS = Path(seizou.stock.__file__).resolve().parent / "static" / "stock" / "stock-form.js"
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
BALANCE_URL = 'data-balance-url="/stock/items/0/balance/"'


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def items(user):
    return [
        masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user),
        masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user),
        masters_services.create_item({"part_no": "T-300", "name": "作り物の環", "unit": "個"}, user),
        masters_services.create_item({"part_no": "T-400", "name": "作り物の栓", "unit": "個"}, user),
    ]


@pytest.fixture
def cut(user):
    return masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)


def _line(user, customer, item, qty="10"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100")}],
        user,
        order_date=timezone.localdate(),
    )
    return order.lines.get()


def _issue(user, line, process, qty="10"):
    return workorders_services.issue_workorder(line, Decimal(qty), [{"process": process, "planned_qty": Decimal(qty)}], user)


def _balance(client, item):
    return client.get(reverse("stock:item_balance", args=[item.pk]))


# ---------------------------------------------------------------- 1. 口の数


@pytest.mark.django_db
def test_item_balance_returns_the_plain_quantity(user, logged_in, customer, items, cut):
    finished, counted, minus, untouched = items
    workorder = _issue(user, _line(user, customer, finished), cut)
    services.record_finished(workorder, Decimal("10"), user)
    services.record_issued(finished, Decimal("3"), timezone.localdate(), user)
    services.adjust(counted, Decimal("12.5"), timezone.localdate(), user)
    services.adjust(minus, Decimal("4"), timezone.localdate(), user)
    services.record_issued(minus, Decimal("6"), timezone.localdate(), user)

    assert _balance(logged_in, finished).json() == {"balance": "7"}
    assert _balance(logged_in, counted).json() == {"balance": "12.5"}
    assert _balance(logged_in, minus).json() == {"balance": "-2"}
    assert _balance(logged_in, untouched).json() == {"balance": None}
    for item in items:
        assert ".00" not in _balance(logged_in, item).content.decode(), item.part_no


# ---------------------------------------------------------------- 2. 口の門


@pytest.mark.django_db
def test_item_balance_gate(client, logged_in, user, items):
    item = items[0]
    assert logged_in.get(reverse("stock:item_balance", args=[999999])).status_code == 404

    response = _balance(logged_in, item)
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]

    assert logged_in.post(reverse("stock:item_balance", args=[item.pk])).status_code == 405

    logged_in.logout()
    response = _balance(client, item)
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


# ---------------------------------------------------------------- 3. 画面の全体


@pytest.mark.django_db
def test_issue_and_adjust_screens_read_the_stock_js(logged_in):
    for name in ("stock:issue_create", "stock:adjust_create"):
        response = logged_in.get(reverse(name))
        body = response.content.decode()
        assert response.status_code == 200, name
        assert body.count("stock/stock-form.js") == 1, name
        assert BALANCE_URL in body, name
        assert "stock/stockmove_form.html" in [template.name for template in response.templates], name

    assert "stock/stock-form.js" not in logged_in.get(reverse("stock:finished_create")).content.decode()


# ---------------------------------------------------------------- 4. 小窓


@pytest.mark.django_db
def test_issue_and_adjust_modals_read_the_stock_js(logged_in, items):
    for name in ("stock:issue_create", "stock:adjust_create"):
        response = logged_in.get(reverse(name), **MODAL)
        body = response.content.decode()
        assert response.status_code == 200, name
        assert "<html" not in body, name
        assert "hx-post=" in body, name
        assert body.count("stock/stock-form.js") == 1, name
        assert BALANCE_URL in body, name
        assert "stock/_form_modal.html" in [template.name for template in response.templates], name

    finished = logged_in.get(reverse("stock:finished_create"), **MODAL)
    body = finished.content.decode()
    assert finished.status_code == 200
    assert "hx-post=" in body
    assert "stock/stock-form.js" not in body

    item = items[0]
    for name in ("stock:issue_create", "stock:adjust_create"):
        body = logged_in.get(reverse(name), {"item": item.pk}, **MODAL).content.decode()
        assert f'<option value="{item.pk}" selected>' in body, name


# ---------------------------------------------------------------- 6. JS の字


def test_stock_form_js_writes_text_only():
    text = STOCK_FORM_JS.read_text(encoding="utf-8")
    for word in ("textContent", "今の在庫数", "まだ数えていません", "差 ", "data-balance-url", "currentScript", "NFKC"):
        assert word in text, word
    for word in (
        "innerHTML",
        "insertAdjacentHTML",
        'document.addEventListener("input"',
        'document.addEventListener("change"',
    ):
        assert word not in text, word
