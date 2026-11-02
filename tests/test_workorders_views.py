"""指示書の画面 (core の土台の上)。発行は検査の層 (R06) を通ってから services を呼ぶ。"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.masters.models import ItemProcess, Process
from seizou.orders import services as orders_services
from seizou.workorders import services
from seizou.workorders.models import WorkOrder

ORDER_DATE = date(2026, 11, 2)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user
    )


@pytest.fixture
def order(user, customer, item):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )


@pytest.fixture
def line(order):
    return order.lines.get()


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="切断", sort_order=1)


@pytest.fixture
def bend(db):
    return Process.objects.create(code="BEND", name="曲げ", sort_order=2)


@pytest.mark.django_db
def test_list_requires_login(client):
    response = client.get(reverse("workorders:workorder_list"))
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


@pytest.mark.django_db
def test_issue_copies_standard_processes(client_logged_in, item, line, cut, bend):
    # 標準の並びは順 (seq) どおり。工程の並び順 (sort_order) とは逆にしておく。
    ItemProcess.objects.create(item=item, process=bend, seq=1)
    ItemProcess.objects.create(item=item, process=cut, seq=2)

    response = client_logged_in.post(
        reverse("workorders:workorder_create"), {"order_line": line.pk, "qty": "4"}
    )
    workorder = WorkOrder.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("workorders:workorder_detail", args=[workorder.pk])
    assert workorder.qty == Decimal("4")
    assert [(s.seq, s.process, s.planned_qty) for s in workorder.steps.all()] == [
        (1, bend, Decimal("4")),
        (2, cut, Decimal("4")),
    ]


@pytest.mark.django_db
def test_issue_with_selected_processes(client_logged_in, line, cut, bend):
    response = client_logged_in.post(
        reverse("workorders:workorder_create"),
        {"order_line": line.pk, "qty": "3", "processes": [bend.pk, cut.pk]},
    )
    assert response.status_code == 302
    workorder = WorkOrder.objects.get()
    assert [s.process for s in workorder.steps.all()] == [cut, bend]


@pytest.mark.django_db
def test_issue_without_any_process_is_red(client_logged_in, line):
    response = client_logged_in.post(
        reverse("workorders:workorder_create"), {"order_line": line.pk, "qty": "4"}
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert "alert-error" in body and "工程がありません" in body
    assert WorkOrder.objects.count() == 0


@pytest.mark.django_db
def test_issue_form_hides_cancelled_lines(client_logged_in, user, customer, item, line):
    other = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("5"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    orders_services.cancel_order(other, user)
    response = client_logged_in.get(reverse("workorders:workorder_create"))
    choices = [value for value, _label in response.context["form"].fields["order_line"].choices if value]
    assert [int(str(value)) for value in choices] == [line.pk]


@pytest.mark.django_db
def test_issue_over_order_stop_is_red(settings, client_logged_in, line, cut):
    settings.SEIZOU = {"over_workorder": "stop"}
    response = client_logged_in.post(
        reverse("workorders:workorder_create"),
        {"order_line": line.pk, "qty": "11", "processes": [cut.pk]},
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert "alert-error" in body and "R06" in body
    assert WorkOrder.objects.count() == 0


@pytest.mark.django_db
def test_issue_over_order_warn_then_confirm(settings, client_logged_in, line, cut):
    settings.SEIZOU = {"over_workorder": "warn"}
    data = {"order_line": line.pk, "qty": "11", "processes": [cut.pk]}
    first = client_logged_in.post(reverse("workorders:workorder_create"), data)
    assert first.status_code == 200
    body = first.content.decode()
    assert "alert-warning" in body and "R06" in body and "このまま保存" in body
    assert WorkOrder.objects.count() == 0

    second = client_logged_in.post(reverse("workorders:workorder_create"), {**data, "confirmed": "1"})
    assert second.status_code == 302
    assert WorkOrder.objects.get().qty == Decimal("11")


@pytest.mark.django_db
def test_list_and_detail_render(client_logged_in, user, line, cut):
    workorder = services.issue_workorder(line, Decimal("2"), [{"process": cut, "planned_qty": 2}], user)
    listing = client_logged_in.get(reverse("workorders:workorder_list"))
    assert listing.status_code == 200
    body = listing.content.decode()
    assert workorder.wo_no in body and "指示書番号" in body
    # 品目の名前は小窓を開く link (master_link) になる
    assert reverse("masters:item_modal", args=[line.item.pk]) in body

    detail = client_logged_in.get(reverse("workorders:workorder_detail", args=[workorder.pk]))
    assert detail.status_code == 200
    assert f"{workorder.wo_no} 1 切断" in detail.content.decode()


@pytest.mark.django_db
def test_menu_has_workorder_entry(client_logged_in):
    response = client_logged_in.get(reverse("core:home"))
    body = response.content.decode()
    assert reverse("workorders:workorder_list") in body and "指示書" in body
