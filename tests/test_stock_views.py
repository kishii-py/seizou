"""在庫の画面 (在庫の一覧・動きの一覧・製造の完了を入庫にする・払い出し・棚卸しの調整)。Django の test client で HTML を見る。

受注・指示書・実績・出荷は services で作る。日は timezone.localdate() から数える。作り物のデータだけを使う。
"""

import re
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.progress import services as progress_services
from seizou.shipping import services as shipping_services
from seizou.stock import services
from seizou.stock.models import KIND_ADJUSTED, KIND_FINISHED, KIND_ISSUED, StockMove
from seizou.workorders import services as workorders_services


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def items(user):
    return [
        masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user),
        masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user),
        masters_services.create_item({"part_no": "T-300", "name": "作り物の環", "unit": "個"}, user),
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


def _today():
    return timezone.localdate().isoformat()


@pytest.mark.django_db
def test_menu_and_screens_open(client, logged_in):
    body = logged_in.get(reverse("core:home")).content.decode()
    assert "在庫" in body
    assert f'href="{reverse("stock:stock_list")}"' in body

    for name in ("stock:stock_list", "stock:stockmove_list", "stock:finished_create", "stock:issue_create", "stock:adjust_create"):
        assert logged_in.get(reverse(name)).status_code == 200, name

    client.logout()
    response = client.get(reverse("stock:stock_list"))
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


@pytest.mark.django_db
def test_stock_list_shows_only_items_with_moves(user, logged_in, customer, items, cut):
    moved, plain, shipped_only = items
    workorder = _issue(user, _line(user, customer, moved), cut)
    services.record_finished(workorder, Decimal("10"), user)
    services.record_issued(moved, Decimal("3"), timezone.localdate(), user)
    shipping_services.ship(_line(user, customer, shipped_only), Decimal("2"), timezone.localdate(), user)

    response = logged_in.get(reverse("stock:stock_list"))
    body = response.content.decode()
    rows = response.context["rows"]

    assert [row["obj"].pk for row in rows] == [moved.pk]
    assert "T-100" in body and "T-200" not in body and "T-300" not in body
    cells = [cell["value"] for cell in rows[0]["cells"]]
    assert any(str(value) in ("7", "7.00") for value in cells)
    assert timezone.localdate() in cells
    assert rows[0]["url"] == reverse("masters:item_detail", args=[moved.pk])
    assert f'href="{reverse("stock:stockmove_list")}"' in body
    # 入力の入口は在庫の一覧の道を ?next= で渡す (「キャンセル」で在庫の一覧へ戻る。初見テスト R4)
    for name in ("stock:issue_create", "stock:adjust_create", "stock:finished_create"):
        assert f'href="{reverse(name)}?next={reverse("stock:stock_list")}"' in body
    assert reverse("masters:item_create") not in body

    searched = logged_in.get(reverse("stock:stock_list"), {"q": "T-999"})
    assert searched.context["rows"] == []
    assert len(logged_in.get(reverse("stock:stock_list"), {"q": "T-100"}).context["rows"]) == 1

    csv_text = logged_in.get(reverse("stock:stock_list"), {"format": "csv"}).content.decode("utf-8")
    assert "在庫数" in csv_text.lstrip("﻿").splitlines()[0]


@pytest.mark.django_db
def test_move_list_filters_by_item_and_marks_negative(user, logged_in, customer, items, cut):
    first, second = items[0], items[1]
    workorder = _issue(user, _line(user, customer, first), cut)
    services.record_finished(workorder, Decimal("5"), user)
    services.record_issued(first, Decimal("2"), timezone.localdate(), user)
    services.adjust(second, Decimal("4"), timezone.localdate(), user)

    body = logged_in.get(reverse("stock:stockmove_list")).content.decode()
    assert "製造の完了" in body and "払い出し" in body and "棚卸しの調整" in body
    assert workorder.wo_no in body  # 元の記録の字
    assert '<span class="text-error">-2' in body  # 払い出しは赤の数

    filtered = logged_in.get(reverse("stock:stockmove_list"), {"item": str(first.pk)})
    assert {row["obj"].item_id for row in filtered.context["rows"]} == {first.pk}
    assert len(filtered.context["rows"]) == 2

    unreadable = logged_in.get(reverse("stock:stockmove_list"), {"item": "abc"})
    assert unreadable.status_code == 200
    assert len(unreadable.context["rows"]) == 3


@pytest.mark.django_db
def test_finished_form_prefills_from_workorder(user, logged_in, customer, items, cut):
    workorder = _issue(user, _line(user, customer, items[0]), cut)
    step = workorder.steps.get()
    progress_services.record_progress(step, Decimal("6"), timezone.now(), user)
    url = reverse("stock:finished_create")

    response = logged_in.get(url, {"workorder": str(workorder.pk)})
    assert response.context["form"].initial["qty"] == Decimal("6")
    services.record_finished(workorder, Decimal("4"), user)
    response = logged_in.get(url, {"workorder": str(workorder.pk)})
    assert response.context["form"].initial["qty"] == Decimal("2")
    assert "qty" not in logged_in.get(url, {"workorder": "abc"}).context["form"].initial

    response = logged_in.post(url, {"workorder": workorder.pk, "qty": "2", "moved_on": _today(), "note": ""})
    move = StockMove.objects.order_by("-pk").first()
    assert response.status_code == 302
    assert response["Location"] == reverse("stock:stockmove_detail", args=[move.pk])
    assert move.kind == KIND_FINISHED and move.source == workorder and move.qty == Decimal("2")

    cancelled = _issue(user, _line(user, customer, items[0]), cut)
    workorders_services.cancel_workorder(cancelled, user)
    count = StockMove.objects.count()
    response = logged_in.post(url, {"workorder": cancelled.pk, "qty": "1", "moved_on": _today(), "note": ""})
    body = response.content.decode()
    assert response.status_code == 200
    assert "alert-error" in body and "取り消した指示書" in body
    assert StockMove.objects.count() == count
    # 候補は発行の指示書だけ
    options = logged_in.get(url).content.decode()
    assert workorder.wo_no in options and cancelled.wo_no not in options


@pytest.mark.django_db
def test_issue_and_adjust_from_screen(user, logged_in, customer, items, cut):
    item = items[0]
    workorder = _issue(user, _line(user, customer, item), cut)
    services.record_finished(workorder, Decimal("10"), user)

    response = logged_in.post(
        reverse("stock:issue_create"), {"item": item.pk, "qty": "3", "moved_on": _today(), "note": "検査用"}
    )
    assert response.status_code == 302
    issued = StockMove.objects.get(kind=KIND_ISSUED)
    assert issued.qty == Decimal("-3") and issued.note == "検査用"

    response = logged_in.post(
        reverse("stock:adjust_create"), {"item": item.pk, "counted_qty": "5", "moved_on": _today(), "note": "棚卸し"}
    )
    assert response.status_code == 302
    assert StockMove.objects.get(kind=KIND_ADJUSTED).qty == Decimal("-2")
    assert services.balance(item) == Decimal("5")

    count = StockMove.objects.count()
    response = logged_in.post(
        reverse("stock:adjust_create"), {"item": item.pk, "counted_qty": "5", "moved_on": _today(), "note": ""}
    )
    body = response.content.decode()
    assert response.status_code == 200
    assert "alert-error" in body and "調整は要りません" in body
    assert StockMove.objects.count() == count


@pytest.mark.django_db
def test_adjust_form_uses_remote_select(logged_in):
    select_url = reverse("masters:item_select")
    for name in ("stock:issue_create", "stock:adjust_create"):
        body = logged_in.get(reverse(name)).content.decode()
        assert f'data-select-url="{select_url}"' in body, name
    assert select_url == "/masters/items/select/"


def _row_tds(body, pk):
    """一覧の行 (<tr id="row-<pk>">) の <td> の HTML の並び。"""
    match = re.search(rf'<tr id="row-{pk}".*?</tr>', body, re.S)
    assert match, f"row-{pk} が無い"
    return re.findall(r"<td[^>]*>.*?</td>", match.group(0), re.S)


@pytest.mark.django_db
def test_stock_list_marks_numbers_and_sorts(user, logged_in, customer, items, cut):
    seven, minus = items[0], items[1]
    workorder = _issue(user, _line(user, customer, seven), cut)
    services.record_finished(workorder, Decimal("10"), user)
    services.record_issued(seven, Decimal("3"), timezone.localdate(), user)
    services.adjust(minus, Decimal("4"), timezone.localdate(), user)
    services.record_issued(minus, Decimal("6"), timezone.localdate(), user)  # 在庫 −2 (払い出しは在庫より多くても止めない)
    url = reverse("stock:stock_list")

    response = logged_in.get(url)
    labels = [col.label for col in response.context["columns"]]
    balance = response.context["columns"][labels.index("在庫数")]
    moved_on = response.context["columns"][labels.index("最終の動きの日")]
    assert balance.numeric and balance.sortable
    assert moved_on.is_date and moved_on.sortable
    body = response.content.decode()
    index = labels.index("在庫数")
    assert "text-error" in _row_tds(body, minus.pk)[index]
    assert "text-error" not in _row_tds(body, seven.pk)[index]

    def balances(sort):
        rows = logged_in.get(url, {"sort": sort}).context["rows"]
        return [Decimal(str(row["cells"][index]["value"])) for row in rows]

    assert balances("stock_balance") == [Decimal("-2"), Decimal("7")]
    assert balances("-stock_balance") == [Decimal("7"), Decimal("-2")]

    response = logged_in.get(url, {"sort": "last_moved_on"})
    assert response.status_code == 200
    assert response.context["sort"] == "last_moved_on"

    lines = logged_in.get(url, {"format": "csv", "sort": "-stock_balance"}).content.decode("utf-8").lstrip("﻿").splitlines()
    assert "在庫数" in lines[0]
    assert lines[1].startswith("T-100,")


@pytest.mark.django_db
def test_count_from_screen_with_past_shipments(user, logged_in, customer, items):
    item = items[0]
    line = _line(user, customer, item, qty="70")
    today = timezone.localdate()
    yesterday = today.fromordinal(today.toordinal() - 1)  # 数え始める日より前の出荷
    shipping_services.ship(line, Decimal("40"), yesterday, user)
    shipping_services.ship(line, Decimal("30"), yesterday, user)
    url = reverse("stock:adjust_create")

    response = logged_in.post(url, {"item": item.pk, "counted_qty": "5", "moved_on": _today(), "note": "棚卸し"})
    assert response.status_code == 302
    assert StockMove.objects.get(kind=KIND_ADJUSTED).qty == Decimal("5")

    listed = logged_in.get(reverse("stock:stock_list"))
    labels = [col.label for col in listed.context["columns"]]
    index = labels.index("在庫数")
    row = next(row for row in listed.context["rows"] if row["obj"].pk == item.pk)
    assert Decimal(str(row["cells"][index]["value"])) == Decimal("5")  # −65 でない
    cell = _row_tds(listed.content.decode(), item.pk)[index]
    assert "text-error" not in cell and "-65" not in cell

    count = StockMove.objects.count()
    response = logged_in.post(url, {"item": item.pk, "counted_qty": "5", "moved_on": _today(), "note": ""})
    body = response.content.decode()
    assert response.status_code == 200
    assert "alert-error" in body and "調整は要りません" in body
    assert StockMove.objects.count() == count
