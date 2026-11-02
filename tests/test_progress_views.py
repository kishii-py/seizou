"""進捗の画面 (core の土台の上)。実績の入力は検査の層 (R07) を通ってから services を呼ぶ。"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress import services
from seizou.progress.models import Progress
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def step(user):
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    item = masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user
    )
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    process = Process.objects.create(code="CUT", name="切断")
    workorder = workorders_services.issue_workorder(
        order.lines.get(), Decimal("10"), [{"process": process, "planned_qty": Decimal("10")}], user
    )
    return workorder.steps.get()


@pytest.fixture
def recorded_eight(user, step):
    return services.record_progress(step, Decimal("8"), RECORDED_AT, user)


def _post(client, step, qty, **extra):
    data = {"workorder_step": step.pk, "qty": qty, "recorded_at": "2026-11-03 15:00", "note": "", **extra}
    return client.post(reverse("progress:progress_create"), data)


@pytest.mark.django_db
def test_record_over_plan_stop_is_red(settings, client_logged_in, step, recorded_eight):
    settings.SEIZOU = {"over_workorder": "stop"}
    response = _post(client_logged_in, step, "3")
    assert response.status_code == 200
    body = response.content.decode()
    assert "alert-error" in body and "R07" in body
    assert Progress.objects.count() == 1


@pytest.mark.django_db
def test_record_over_plan_warn_then_confirm(settings, client_logged_in, step, recorded_eight):
    settings.SEIZOU = {"over_workorder": "warn"}
    first = _post(client_logged_in, step, "3")
    assert first.status_code == 200
    body = first.content.decode()
    assert "alert-warning" in body and "R07" in body and "このまま保存" in body
    assert Progress.objects.count() == 1

    second = _post(client_logged_in, step, "3", confirmed="1")
    assert second.status_code == 302
    assert Progress.objects.count() == 2
    assert services.recorded_qty(step) == Decimal("11")


@pytest.mark.django_db
def test_record_sets_user(client_logged_in, user, step):
    response = _post(client_logged_in, step, "4", note="作り物の備考")
    assert response.status_code == 302
    assert response["Location"] == reverse("progress:progress_list")
    record = Progress.objects.get()
    assert record.recorded_by == user
    assert record.qty == Decimal("4")
    assert record.recorded_at == datetime(2026, 11, 3, 15, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert record.note == "作り物の備考"

    listing = client_logged_in.get(reverse("progress:record_list"))
    assert listing.status_code == 200
    body = listing.content.decode()
    assert "指示書の工程" in body and str(step) in body and "tester" in body


@pytest.mark.django_db
def test_menu_has_progress_entry(client_logged_in):
    response = client_logged_in.get(reverse("core:home"))
    body = response.content.decode()
    assert reverse("progress:progress_list") in body and "進捗" in body
