"""進捗の画面 (core の土台の上)。実績の入力は検査の層 (R07) を通ってから services を呼ぶ。"""

import json
import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

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
    assert 'data-notice="stop"' in body and "R07" in body
    assert Progress.objects.count() == 1


@pytest.mark.django_db
def test_record_over_plan_warn_then_confirm(settings, client_logged_in, step, recorded_eight):
    settings.SEIZOU = {"over_workorder": "warn"}
    first = _post(client_logged_in, step, "3")
    assert first.status_code == 200
    body = first.content.decode()
    assert 'data-notice="check"' in body and "R07" in body and "このまま保存" in body
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


DATETIME_VALUE = re.compile(r'<input type="datetime-local" name="recorded_at" value="([^"]*)"')
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}


@pytest.mark.django_db
def test_recorded_at_is_datetime_local_to_minute(client_logged_in):
    """日時の欄はブラウザの日付と時刻の選び (分まで)。初期値は今で、秒は出ない (候補 C65)。"""
    before = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
    response = client_logged_in.get(reverse("progress:progress_create"))
    after = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
    assert response.status_code == 200
    match = DATETIME_VALUE.search(response.content.decode())
    assert match is not None
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", match.group(1))
    assert match.group(1) in {before, after}


@pytest.mark.django_db
def test_recorded_at_reads_browser_format(client_logged_in, step):
    """ブラウザの日時の欄が送る形 (日付と時刻の間が T) を、会社の時刻の日時として保存する。"""
    response = _post(client_logged_in, step, "4", recorded_at="2026-11-03T15:00")
    assert response.status_code == 302
    assert Progress.objects.get().recorded_at == datetime(2026, 11, 3, 15, 0, tzinfo=ZoneInfo("Asia/Tokyo"))


@pytest.mark.django_db
def test_recorded_at_kept_when_stopped(settings, client_logged_in, step, recorded_eight):
    """確かめで止まって描き直したときも、入れた日時が欄に残る (知らせの字と class はここで見ない)。"""
    settings.SEIZOU = {"over_workorder": "warn"}
    response = _post(client_logged_in, step, "3", recorded_at="2026-11-03T15:00")
    assert response.status_code == 200
    assert Progress.objects.count() == 1
    assert 'type="datetime-local" name="recorded_at" value="2026-11-03T15:00"' in response.content.decode()


@pytest.mark.django_db
def test_recorded_at_in_modal(client_logged_in):
    """生産管理ボードの「実績を入れる」で開く小窓でも、日時は同じ日付と時刻の選び。"""
    response = client_logged_in.get(reverse("progress:progress_create"), **MODAL)
    assert response.status_code == 200
    body = response.content.decode()
    assert 'type="datetime-local" name="recorded_at"' in body
    assert "<html" not in body


def _saved_text(step, qty_text):
    """実績を記録した後の知らせの字 (候補 C44 の 1。指示書の番号は fixture が作るので字で決め打ちしない)。"""
    return f"{step.workorder.wo_no} の工程 1 切断 に実績 {qty_text} を記録しました。"


@pytest.mark.django_db
def test_record_message_says_what_was_recorded(client_logged_in, step):
    """全体の画面で実績を記録すると、右上の知らせが何をどれだけ記録したかを書く (「保存しました。」の代わり)。"""
    response = _post(client_logged_in, step, "4")
    assert response.status_code == 302
    messages = [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]
    assert messages == [("success", _saved_text(step, "4"))]


@pytest.mark.django_db
def test_record_message_in_modal(client_logged_in, step):
    """小窓で実績を記録しても、保存の後の知らせ (HX-Trigger の seizou:saved) が同じ字になる。"""
    data = {"workorder_step": step.pk, "qty": "4", "recorded_at": "2026-11-03 15:00", "note": ""}
    response = client_logged_in.post(reverse("progress:progress_create"), data, **MODAL)
    assert response.status_code == 204
    saved = json.loads(response["HX-Trigger"])["seizou:saved"]
    assert saved == {"message": _saved_text(step, "4"), "level": "success"}
    assert list(get_messages(response.wsgi_request)) == []


@pytest.mark.django_db
def test_record_progress_saved_message_drops_trailing_zeros(user, step):
    """service が返す実績の saved_message は、数の末尾の 0 を落とす (500.00 → 500、2.50 → 2.5)。"""
    whole = services.record_progress(step, Decimal("500.00"), RECORDED_AT, user).saved_message
    assert whole == _saved_text(step, "500")
    assert "500.00" not in whole
    half = services.record_progress(step, Decimal("2.50"), RECORDED_AT, user).saved_message
    assert half == _saved_text(step, "2.5")
