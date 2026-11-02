"""指示書を取り消す画面 (core の土台の上)。Django の test client で HTML と表の状態を見る。

指示書の詳細には、発行のときだけ「取り消す」が出る。理由は「取消の理由」に残る。
実績の入った指示書の取消は赤の知らせで発行のまま。取消の指示書は生産計画の盤から外れる。
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress import services as progress_services
from seizou.workorders import services
from seizou.workorders.models import WorkOrder

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
WEEK = "?start=2026-11-02"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="切断", sort_order=1, use_in_plan=True)


@pytest.fixture
def workorder(user, cut):
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    return services.issue_workorder(
        order.lines.get(), Decimal("4"), [{"process": cut, "planned_qty": Decimal("4")}], user
    )


@pytest.mark.django_db
def test_detail_has_cancel_link_only_when_issued(logged_in, user, workorder):
    cancel_url = f"/workorders/{workorder.pk}/cancel/"
    issued = logged_in.get(reverse("workorders:workorder_detail", args=[workorder.pk]))
    assert issued.status_code == 200
    body = issued.content.decode()
    assert f'href="{cancel_url}"' in body
    assert "取り消す" in body

    services.cancel_workorder(workorder, user)
    cancelled = logged_in.get(reverse("workorders:workorder_detail", args=[workorder.pk]))
    assert cancel_url not in cancelled.content.decode()


@pytest.mark.django_db
def test_cancel_on_screen_with_reason(logged_in, workorder):
    url = reverse("workorders:workorder_cancel", args=[workorder.pk])
    page = logged_in.get(url)
    assert page.status_code == 200
    assert "この指示書を取り消します。" in page.content.decode()

    response = logged_in.post(url, {"reason": "作り物の理由: 数の間違い"})

    assert response.status_code == 302
    assert response["Location"] == reverse("workorders:workorder_detail", args=[workorder.pk])
    workorder.refresh_from_db()
    assert workorder.status == WorkOrder.STATUS_CANCELLED
    assert workorder.cancel_reason == "作り物の理由: 数の間違い"
    detail = logged_in.get(response["Location"]).content.decode()
    assert "</span>取消</span>" in detail
    assert "作り物の理由: 数の間違い" in detail


@pytest.mark.django_db
def test_cancel_with_progress_is_red(logged_in, user, workorder):
    progress_services.record_progress(workorder.steps.get(), Decimal("1"), RECORDED_AT, user)

    response = logged_in.post(reverse("workorders:workorder_cancel", args=[workorder.pk]), {"reason": ""})

    assert response.status_code == 200
    body = response.content.decode()
    assert "alert-error" in body
    assert workorder.wo_no in body
    workorder.refresh_from_db()
    assert workorder.status == WorkOrder.STATUS_ISSUED


@pytest.mark.django_db
def test_plan_board_drops_cancelled(settings, logged_in, user, cut, workorder):
    settings.SEIZOU = {"production_plan": True}
    services.plan_step(workorder.steps.get(), date(2026, 11, 4), date(2026, 11, 4), user)
    board_url = reverse("workorders:plan_board", args=[cut.pk]) + WEEK
    before = logged_in.get(board_url)
    assert before.status_code == 200
    assert workorder.wo_no in before.content.decode()

    logged_in.post(reverse("workorders:workorder_cancel", args=[workorder.pk]), {"reason": ""})

    after = logged_in.get(board_url)
    assert after.status_code == 200
    assert workorder.wo_no not in after.content.decode()
