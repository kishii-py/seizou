"""指示書と指示書の工程の候補の口 (workorders:workorder_select・workorders:workorderstep_select) と、字を打って探す欄にした 3 つの欄。

候補の口は core の BaseSelectView の形 ({"items": [{"id", "text", "title", "sub"}], "more": 真偽}) で 1 ページ 30 件を返す。
指示書の口は発行の指示書だけを発行日の新しい順に返し、字は 2 段 (上 = 指示書番号、下 = 受注の明細)。
工程の口は全部の指示書の全部の工程を返し、字は今の「<指示書番号> <順> <工程の名前>」の 1 段のまま。
欄は実績の新規登録の「指示書の工程」・指示書の発行の「受注の明細」(orders:orderline_select の for=workorder)・
製造の完了を入庫にするの「指示書」(初見テスト R5 の候補 C92)。作り物のデータだけを使う。
"""

import re
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
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


def _select(client, name, **params):
    response = client.get(reverse(name), params)
    assert response.status_code == 200
    return response.json()["items"]


@pytest.mark.django_db
def test_select_requires_login(client):
    for name in ("workorders:workorder_select", "workorders:workorderstep_select"):
        response = client.get(reverse(name))
        assert response.status_code == 302, name


@pytest.mark.django_db
def test_workorder_select_lists_issued_newest_first(user, logged_in, customer, items, cut):
    shaft = _issue(user, _line(user, customer, items[0]), cut)
    plate = _issue(user, _line(user, customer, items[1]), cut)
    cancelled = _issue(user, _line(user, customer, items[0]), cut)
    workorders_services.cancel_workorder(cancelled, user)

    rows = _select(logged_in, "workorders:workorder_select")
    # 発行の 2 枚を新しい順 (発行日が同じなら後に発行した方が先)。取消の指示書は出さない
    assert [row["id"] for row in rows] == [plate.pk, shaft.pk]
    row = rows[1]
    assert row["text"] == shaft.wo_no
    assert row["title"] == shaft.wo_no
    assert row["sub"] == str(shaft.order_line)
    assert row["sub"] == f"{shaft.order_line.order.order_no}-1 T-100 作り物の軸"


@pytest.mark.django_db
def test_workorder_select_search(user, logged_in, customer, items, cut):
    shaft = _issue(user, _line(user, customer, items[0]), cut)
    plate = _issue(user, _line(user, customer, items[1]), cut)

    assert [row["id"] for row in _select(logged_in, "workorders:workorder_select", q="T-200")] == [plate.pk]
    assert [row["id"] for row in _select(logged_in, "workorders:workorder_select", q=shaft.wo_no)] == [shaft.pk]


@pytest.mark.django_db
def test_workorderstep_select_keeps_one_line_text(user, logged_in, customer, items, cut):
    bend = masters_services.create_process({"code": "BEND", "name": "曲げ", "sort_order": 2}, user)
    two_steps = workorders_services.issue_workorder(
        _line(user, customer, items[0]),
        Decimal("10"),
        [{"process": cut, "planned_qty": Decimal("10")}, {"process": bend, "planned_qty": Decimal("10")}],
        user,
    )
    _issue(user, _line(user, customer, items[1]), cut)

    rows = _select(logged_in, "workorders:workorderstep_select", q=two_steps.wo_no)
    wo_no = two_steps.wo_no
    assert [row["text"] for row in rows] == [f"{wo_no} 1 切断", f"{wo_no} 2 曲げ"]
    # 1 段のまま (下の段は空、上の段は欄の字と同じ)
    assert all(row["sub"] == "" and row["title"] == row["text"] for row in rows)


@pytest.mark.django_db
def test_three_fields_use_remote_select(logged_in):
    body = logged_in.get(reverse("workorders:workorder_create")).content.decode()
    assert f'data-select-url="{reverse("orders:orderline_select")}?for=workorder"' in body

    body = logged_in.get(reverse("progress:progress_create")).content.decode()
    assert f'data-select-url="{reverse("workorders:workorderstep_select")}"' in body

    body = logged_in.get(reverse("stock:finished_create")).content.decode()
    assert f'data-select-url="{reverse("workorders:workorder_select")}"' in body
    assert f'data-modal-url="{reverse("workorders:workorder_modal", args=[0])}"' in body


@pytest.mark.django_db
def test_issue_form_selected_line_uses_candidate_text(user, logged_in, customer, items):
    line = _line(user, customer, items[0])

    response = logged_in.get(reverse("workorders:workorder_create") + f"?order_line={line.pk}")
    assert response.status_code == 200
    match = re.search(r'<select name="order_line".*?</select>', response.content.decode(), re.S)
    assert match, "受注の明細の選ぶ欄が無い"
    select = match.group(0)
    assert f'<option value="{line.pk}" selected>{line}</option>' in select
    assert "行目" not in select

    # 候補の口 (for=workorder) の字も同じ str(明細)
    rows = _select(logged_in, "orders:orderline_select", **{"for": "workorder"})
    assert [row["text"] for row in rows if row["id"] == line.pk] == [str(line)]
