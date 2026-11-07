"""指示書を取り消す関数 (workorders.services.cancel_workorder)。

状態を取消にし、理由を「取消の理由」の列に残す。2 回目と、実績の入った指示書と、500 字を超える理由は ValueError。
取消の指示書は issued_qty に数えず、生産計画と進捗の盤から外れる (今の main の動きを確かめる)。
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model

from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress import boards
from seizou.progress import services as progress_services
from seizou.workorders import plans, services
from seizou.workorders.models import WorkOrder

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 30, tzinfo=ZoneInfo("Asia/Tokyo"))


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def line(user):
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
    return order.lines.get()


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="切断", sort_order=1, use_in_plan=True)


def _issue(user, line, cut, qty):
    return services.issue_workorder(line, Decimal(qty), [{"process": cut, "planned_qty": Decimal(qty)}], user)


@pytest.fixture
def workorder(user, line, cut):
    return _issue(user, line, cut, "4")


@pytest.mark.django_db
def test_cancel_sets_status_and_reason(user, workorder):
    result = services.cancel_workorder(workorder, user, reason="作り物の理由: 数の間違い")

    assert isinstance(result, WorkOrder)
    assert result.pk == workorder.pk
    workorder.refresh_from_db()
    assert workorder.status == WorkOrder.STATUS_CANCELLED
    assert workorder.cancel_reason == "作り物の理由: 数の間違い"


@pytest.mark.django_db
def test_cancel_twice_is_refused(user, workorder):
    services.cancel_workorder(workorder, user)

    with pytest.raises(ValueError, match="この指示書は取り消し済みです。"):
        services.cancel_workorder(workorder, user, reason="2 回目")
    workorder.refresh_from_db()
    assert workorder.cancel_reason == ""


@pytest.mark.django_db
def test_cancel_with_progress_is_refused(user, workorder):
    progress_services.record_progress(workorder.steps.get(), Decimal("1"), RECORDED_AT, user)

    with pytest.raises(ValueError) as caught:
        services.cancel_workorder(workorder, user)
    assert workorder.wo_no in str(caught.value)
    assert "実績が 1 件" in str(caught.value)
    workorder.refresh_from_db()
    assert workorder.status == WorkOrder.STATUS_ISSUED


@pytest.mark.django_db
def test_long_reason_is_refused(user, workorder):
    with pytest.raises(ValueError, match="500"):
        services.cancel_workorder(workorder, user, reason="あ" * 501)
    workorder.refresh_from_db()
    assert workorder.status == WorkOrder.STATUS_ISSUED

    # ちょうど 500 字なら残る
    result = services.cancel_workorder(workorder, user, reason="あ" * 500)
    assert len(result.cancel_reason) == 500


@pytest.mark.django_db
def test_cancelled_is_not_counted_in_issued_qty(user, line, cut):
    _issue(user, line, cut, "6")
    four = _issue(user, line, cut, "4")
    assert services.issued_qty(line) == Decimal("10")

    services.cancel_workorder(four, user)

    assert services.issued_qty(line) == Decimal("6")


@pytest.mark.django_db
def test_cancelled_steps_leave_both_boards(user, cut, workorder):
    step = workorder.steps.get()
    assert step in plans.active_steps(cut)
    assert step in boards.active_steps()

    services.cancel_workorder(workorder, user)

    assert step not in plans.active_steps(cut)
    assert step not in boards.active_steps()


# --- 取り消せない理由の文は cancel_refusal の 1 か所 (初見テスト R5 の候補 C79) ---


@pytest.mark.django_db
def test_cancel_refusal_counts_progress(user, workorder):
    """実績が無ければ空の字。実績が入ると、件数を入れた断りの文 (取消の確かめのカードと同じ文)。"""
    assert services.cancel_refusal(workorder) == ""

    progress_services.record_progress(workorder.steps.get(), Decimal("1"), RECORDED_AT, user)
    assert services.cancel_refusal(workorder) == f"取り消せません。指示書 {workorder.wo_no} には実績が 1 件入っています。"

    progress_services.record_progress(workorder.steps.get(), Decimal("1"), RECORDED_AT, user)
    assert services.cancel_refusal(workorder) == f"取り消せません。指示書 {workorder.wo_no} には実績が 2 件入っています。"


@pytest.mark.django_db
def test_cancel_workorder_raises_the_same_refusal(user, workorder):
    """cancel_workorder の ValueError の字は cancel_refusal の字と同じ (文は 1 か所)。状態は発行のまま。"""
    progress_services.record_progress(workorder.steps.get(), Decimal("1"), RECORDED_AT, user)

    with pytest.raises(ValueError) as caught:
        services.cancel_workorder(workorder, user, reason="作り物の理由")

    assert str(caught.value) == services.cancel_refusal(workorder)
    workorder.refresh_from_db()
    assert workorder.status == WorkOrder.STATUS_ISSUED
    assert workorder.cancel_reason == ""
