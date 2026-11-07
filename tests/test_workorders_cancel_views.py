"""指示書を取り消す画面 (core の土台の上)。Django の test client で HTML と表の状態を見る。

指示書の詳細には、発行のときだけ「取り消す」が出る。理由は「取消の理由」に残る。
実績の入った指示書の取消は赤の知らせで発行のまま。取消の指示書は生産計画の盤から外れる。
実績の入った指示書の取消の画面は、押す前に黄の断りを出し、理由の欄と取り消すボタンを出さない。
"""

import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core.views import hidden_detail_fields
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
    assert "取消</a>" in body

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
    assert 'data-notice="stop"' in body
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


@pytest.mark.django_db
def test_cancel_screen_says_cancel(logged_in, user, workorder):
    url = reverse("workorders:workorder_cancel", args=[workorder.pk])
    page = logged_in.get(url)
    assert page.status_code == 200
    body = page.content.decode()
    assert "取り消す</button>" in body
    assert "保存</button>" not in body
    # 理由は任意の欄なので、「必須」の欄の案内の行は今までどおり出ない。
    assert "「必須」の欄を埋めて" not in body

    progress_services.record_progress(workorder.steps.get(), Decimal("1"), RECORDED_AT, user)
    response = logged_in.post(url, {"reason": ""})

    assert response.status_code == 200
    stopped = response.content.decode()
    assert 'data-notice="stop"' in stopped
    # 実績の入った指示書で止まった画面は、赤の知らせと黄の断りだけで、取り消すボタンを出さない (初見テスト R5 の候補 C79)
    assert "取り消す</button>" not in stopped
    assert "保存</button>" not in stopped
    workorder.refresh_from_db()
    assert workorder.status == WorkOrder.STATUS_ISSUED


# --- 基本情報の「取消の理由」は取り消した指示書だけ (初見テスト R5 の候補 C18 の A1) ---

REASON_DT = re.compile(r"<dt[^>]*>取消の理由</dt>\s*<dd[^>]*>(.*?)</dd>", re.S)


def test_cancel_reason_is_hidden_unless_cancelled():
    assert "cancel_reason" in hidden_detail_fields(WorkOrder())
    assert "cancel_reason" not in hidden_detail_fields(WorkOrder(status=WorkOrder.STATUS_CANCELLED))
    # 状態は今までどおり、どちらでも出さない (題の横の印で出す)
    assert "status" in hidden_detail_fields(WorkOrder())
    assert "status" in hidden_detail_fields(WorkOrder(status=WorkOrder.STATUS_CANCELLED))


@pytest.mark.django_db
def test_issued_detail_and_modal_have_no_cancel_reason_row(logged_in, workorder):
    detail = logged_in.get(reverse("workorders:workorder_detail", args=[workorder.pk]))
    assert "取消の理由" not in [field["label"] for field in detail.context["fields"]]
    assert REASON_DT.search(detail.content.decode()) is None

    modal = logged_in.get(reverse("workorders:workorder_modal", args=[workorder.pk]), HTTP_HX_REQUEST="true")
    assert modal.status_code == 200
    assert "取消の理由" not in [field["label"] for field in modal.context["fields"]]
    assert REASON_DT.search(modal.content.decode()) is None


@pytest.mark.django_db
def test_cancelled_detail_shows_cancel_reason_row_even_if_empty(logged_in, user, workorder):
    services.cancel_workorder(workorder, user)

    body = logged_in.get(reverse("workorders:workorder_detail", args=[workorder.pk])).content.decode()

    # 理由を書かずに取り消した指示書も、行を出して「—」
    reason = REASON_DT.search(body)
    assert reason and "—" in reason.group(1)


# --- 実績の入った指示書の取消は、押す前に断りの文を出す (初見テスト R5 の候補 C79) ---

#: 小窓の頭 (tests/test_small_inputs_modal.py の MODAL と同じ字)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
#: 入力のカード (core/_form_card.html) の form の頭 (画面の全体には上の帯のログアウトの form もあるので、class まで見る)
FORM_CARD = '<form method="post" class="rounded-xl'


def _warning(body):
    """最初の alert-warning の箱 (次の </div> まで)。"""
    warning = body[body.index("alert-warning") :]
    return warning[: warning.index("</div>")]


@pytest.mark.django_db
def test_progress_cancel_screen_refuses_before_press(logged_in, user, workorder):
    """全体の画面を GET すると、黄の断りと「指示書へ戻る」が出て、確かめの文と理由の欄と取り消すボタンは出ない。"""
    url = reverse("workorders:workorder_cancel", args=[workorder.pk])
    back = f'href="{reverse("workorders:workorder_detail", args=[workorder.pk])}"'

    # 実績が無い時は、今のまま form と理由の欄があり、「指示書へ戻る」は無い
    fresh = logged_in.get(url).content.decode()
    assert FORM_CARD in fresh
    assert 'name="reason"' in fresh
    assert f"{workorder.wo_no} を取り消す</button>" in fresh
    assert "指示書へ戻る" not in fresh

    progress_services.record_progress(workorder.steps.get(), Decimal("1"), RECORDED_AT, user)
    page = logged_in.get(url)

    assert page.status_code == 200
    body = page.content.decode()
    warning = _warning(body)
    assert '<span class="font-semibold">取り消せません。</span>' in warning
    assert services.cancel_refusal(workorder).partition("。")[2].strip() in warning
    assert "この指示書を取り消します。" not in body
    assert FORM_CARD not in body
    assert 'name="reason"' not in body
    assert f"{workorder.wo_no} を取り消す</button>" not in body
    assert back in body
    assert "指示書へ戻る" in body
    workorder.refresh_from_db()
    assert workorder.status == WorkOrder.STATUS_ISSUED


@pytest.mark.django_db
def test_progress_cancel_post_shows_refusal_without_button(logged_in, user, workorder):
    """URL を直に開いて POST すると、赤の知らせの下に同じ黄の断りが出て、確かめの文と理由の欄と取り消すボタンは残らない。"""
    progress_services.record_progress(workorder.steps.get(), Decimal("1"), RECORDED_AT, user)

    response = logged_in.post(reverse("workorders:workorder_cancel", args=[workorder.pk]), {"reason": "作り物の理由"})

    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body
    assert "alert-warning" in body
    assert "この指示書を取り消します。" not in body
    assert FORM_CARD not in body
    assert 'name="reason"' not in body
    assert f"{workorder.wo_no} を取り消す</button>" not in body
    workorder.refresh_from_db()
    assert workorder.status == WorkOrder.STATUS_ISSUED


@pytest.mark.django_db
def test_progress_cancel_refusal_in_modal(logged_in, user, workorder):
    """小窓は黄の断りだけで、form と取り消すボタンと「指示書へ戻る」を出さない (小窓の下は指示書の詳細)。全体の画面は「指示書へ戻る」を出す。"""
    progress_services.record_progress(workorder.steps.get(), Decimal("1"), RECORDED_AT, user)
    url = reverse("workorders:workorder_cancel", args=[workorder.pk])
    back = f'href="{reverse("workorders:workorder_detail", args=[workorder.pk])}"'

    modal = logged_in.get(url, **MODAL).content.decode()
    assert "<html" not in modal
    warning = _warning(modal)
    assert '<span class="font-semibold">取り消せません。</span>' in warning
    assert services.cancel_refusal(workorder).partition("。")[2].strip() in warning
    assert '<form method="post"' not in modal
    assert f"{workorder.wo_no} を取り消す</button>" not in modal
    assert "指示書へ戻る" not in modal

    whole = logged_in.get(url).content.decode()
    assert "<html" in whole
    assert back in whole
    assert "指示書へ戻る" in whole
    workorder.refresh_from_db()
    assert workorder.status == WorkOrder.STATUS_ISSUED
