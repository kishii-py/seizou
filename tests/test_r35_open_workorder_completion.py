"""R35: 受注の明細を完了にするときに、その明細の終わっていない指示書があったら (標準のポカよけ。会社の設定 open_workorder_completion)。

本人の決め 11-11 22:5x (完了にする時の黄の確かめ) と 11-11 12:5x (ポカよけはまず分かるようにし、止めるかは会社が選ぶ)。
受注の明細を「完了にする」と、残りを出さずに終える。その明細の指示書が終わっていなくても、完了の処理は指示書に触らず、指示書は発行のまま残る
(現場がもう要らない物を作り続けるかもしれない)。完了を押す時に知らせる。
検査の層 (seizou.core.checks) を設定の 3 つの値 (warn・stop・off) で、受注の明細の「完了にする」の小窓 (orders.mark_completed) と、
出荷の新規登録の「残りは出さずに完了にする」(shipping.ship の close_rest) から回す。
終わっていない指示書 = 取消でなく、進みの状態が完了でない指示書 (最後の工程に完了の印の付いた実績が無い)。
作り物のデータは services で作る (工程の行だけは、ほかの決まりの試験と同じく表に直に作る)。
"""

import json
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from seizou.core import checks
from seizou.core.exceptions import CheckViolation
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.orders.models import OrderLine
from seizou.progress import services as progress_services
from seizou.shipping import services as shipping_services
from seizou.shipping.models import Shipment
from seizou.workorders import services as workorders_services
from tests.rules_helpers import rules_md_text

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
RECORDED_AT = datetime(2026, 11, 4, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
COMPLETE = "orders.mark_completed"
SHIP = "shipping.ship"
KEY = "open_workorder_completion"
WARN_TAIL = "完了にしても指示書は残ります。作らないときは、指示書を取り消してください。このまま保存できます。"
STOP_TAIL = "指示書を終えるか取り消してから、完了にしてください。"


def _head(*numbers):
    return f"この明細には、終わっていない指示書が {len(numbers)} 件あります ({'・'.join(numbers)})。"


@pytest.fixture
def line(user):
    """数量 10 の作り物の受注の明細 (出荷は無い)。"""
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}], user, order_date=ORDER_DATE
    )
    return order.lines.get()


@pytest.fixture
def processes(db):
    return [Process.objects.create(code="CUT", name="切断"), Process.objects.create(code="INSP", name="検査")]


def _issue(user, line, processes, qty="4"):
    return workorders_services.issue_workorder(
        line, Decimal(qty), [{"process": process, "planned_qty": Decimal(qty)} for process in processes], user
    )


def _finish(user, workorder, qty="4"):
    """最後の工程に、完了の印の付いた実績を入れる (指示書の進みの状態が完了になる)。"""
    last = workorder.steps.order_by("-seq").first()
    return progress_services.record_progress(last, Decimal(qty), RECORDED_AT, user, completes_step=True)


def _complete_url(line):
    return reverse("orders:orderline_complete", args=[line.pk])


def _ship_data(line, qty, **extra):
    return {"order_line": line.pk, "qty": qty, "shipped_at": SHIPPED_AT.isoformat(), "note": "", "close_rest": "on", **extra}


def _completed(line):
    return OrderLine.objects.get(pk=line.pk).completed


def _saved(response):
    return json.loads(response["HX-Trigger"])["seizou:saved"]


# ---------------------------------------------------------------- 決まりの行と設定の鍵


def test_r35_row_in_rules_md_and_setting_key():
    assert "終わっていない指示書" in rules_md_text("R35")
    assert checks.setting_key_for("R35") == KEY
    assert {check.action for check in checks.registered(COMPLETE) + checks.registered(SHIP) if check.name == "R35"} == {COMPLETE, SHIP}


@pytest.mark.django_db
def test_r35_setting_on_settings_screen(logged_in):
    body = logged_in.get(reverse("core:settings")).content.decode()
    assert KEY in body
    assert "終わっていない指示書のある受注の明細の完了 (R35)" in body


# ---------------------------------------------------------------- (a)〜(c) 受注の明細の「完了にする」


@pytest.mark.django_db
def test_r35_warn_on_complete_then_confirm(settings, logged_in, user, line, processes):
    """(a) 既定 (warn): 黄の知らせと「このまま保存」が出て印は偽のまま。「このまま保存」で印が真になり、指示書は発行のまま残る。"""
    settings.SEIZOU = {}
    workorder = _issue(user, line, processes)
    first = logged_in.post(_complete_url(line), {}, **MODAL)
    assert first.status_code == 200
    body = first.content.decode()
    assert "alert-warning" in body and "R35" in body and "このまま保存" in body
    assert _head(workorder.wo_no) in body and WARN_TAIL in body
    assert 'data-notice="stop"' not in body
    assert _completed(line) is False

    second = logged_in.post(_complete_url(line), {"confirmed": "1"}, **MODAL)
    assert second.status_code == 204
    assert "完了にしました" in _saved(second)["message"]
    assert _completed(line) is True
    workorder.refresh_from_db()
    assert workorder.status == type(workorder).STATUS_ISSUED


@pytest.mark.django_db
def test_r35_stop_on_complete_is_red(settings, logged_in, user, line, processes):
    """(b) 止める (stop): 赤の知らせで止まり、「このまま保存」を送っても印は付かない。"""
    settings.SEIZOU = {KEY: "stop"}
    workorder = _issue(user, line, processes)
    for data in ({}, {"confirmed": "1"}):
        response = logged_in.post(_complete_url(line), data, **MODAL)
        assert response.status_code == 200
        body = response.content.decode()
        assert _head(workorder.wo_no) in body and STOP_TAIL in body
        assert "このまま保存" not in body and WARN_TAIL not in body
        assert _completed(line) is False


@pytest.mark.django_db
def test_r35_off_on_complete_saves(settings, logged_in, user, line, processes):
    """(c) 通す (off): 知らせずに完了にする。"""
    settings.SEIZOU = {KEY: "off"}
    _issue(user, line, processes)
    response = logged_in.post(_complete_url(line), {}, **MODAL)
    assert response.status_code == 204
    assert _completed(line) is True


# ---------------------------------------------------------------- (d)〜(e) 出荷の「残りは出さずに完了にする」


@pytest.mark.django_db
def test_r35_warn_on_ship_close_rest_then_confirm(settings, logged_in, user, line, processes):
    """(d) 既定 (warn): 残りの残る出荷で「残りは出さずに完了にする」を選ぶと黄の知らせ。「このまま保存」で出荷と印が入る。"""
    settings.SEIZOU = {}
    workorder = _issue(user, line, processes)
    url = reverse("shipping:shipment_create")
    first = logged_in.post(url, _ship_data(line, "3"))
    assert first.status_code == 200
    body = first.content.decode()
    assert _head(workorder.wo_no) in body and WARN_TAIL in body and "このまま保存" in body
    assert not Shipment.objects.exists() and _completed(line) is False

    second = logged_in.post(url, _ship_data(line, "3", confirmed="1"))
    assert second.status_code == 302
    assert Shipment.objects.count() == 1 and _completed(line) is True


@pytest.mark.django_db
def test_r35_stop_on_ship_close_rest_keeps_no_shipment(settings, logged_in, user, line, processes):
    """(e) 止める (stop): 出荷も印も入らない。"""
    settings.SEIZOU = {KEY: "stop"}
    workorder = _issue(user, line, processes)
    response = logged_in.post(reverse("shipping:shipment_create"), _ship_data(line, "3", confirmed="1"))
    assert response.status_code == 200
    body = response.content.decode()
    assert _head(workorder.wo_no) in body and STOP_TAIL in body
    assert not Shipment.objects.exists() and _completed(line) is False


# ---------------------------------------------------------------- (f) 知らせない場合


@pytest.mark.django_db
def test_r35_silent_without_workorders(settings, user, line):
    settings.SEIZOU = {}
    assert checks.run(COMPLETE, {"object": line}, user) == []


@pytest.mark.django_db
def test_r35_silent_when_every_workorder_is_finished_or_cancelled(settings, user, line, processes):
    """完了の指示書 (最後の工程に完了の印) と取消の指示書は数えない。途中の工程だけに実績のある指示書は数える。"""
    settings.SEIZOU = {}
    finished = _issue(user, line, processes, qty="2")
    _finish(user, finished, qty="2")
    cancelled = _issue(user, line, processes, qty="2")
    workorders_services.cancel_workorder(cancelled, user)
    assert checks.run(COMPLETE, {"object": line}, user) == []

    started = _issue(user, line, processes, qty="2")
    progress_services.record_progress(started.steps.order_by("seq").first(), Decimal("2"), RECORDED_AT, user)
    warnings = checks.run(COMPLETE, {"object": line}, user)
    assert [(warning.code, warning.message) for warning in warnings] == [("R35", _head(started.wo_no) + WARN_TAIL)]


@pytest.mark.django_db
def test_r35_silent_for_a_line_that_is_already_finished(settings, user, line, processes):
    """もう完了の印が付いた明細と、数量まで出荷して終わっている明細は見ない (完了の処理が何も書かないため)。"""
    settings.SEIZOU = {}
    _issue(user, line, processes)
    done = orders_services.mark_completed(line, user)
    assert done.completed is True
    assert checks.run(COMPLETE, {"object": OrderLine.objects.get(pk=line.pk)}, user) == []

    orders_services.clear_completed(OrderLine.objects.get(pk=line.pk), user)
    shipping_services.ship(OrderLine.objects.get(pk=line.pk), Decimal("10"), SHIPPED_AT, user)
    assert checks.run(COMPLETE, {"object": OrderLine.objects.get(pk=line.pk)}, user) == []


@pytest.mark.django_db
def test_r35_silent_on_ship_without_close_rest_or_when_the_shipment_reaches_qty(settings, user, line, processes):
    """「残りは出さずに完了にする」を選ばない出荷と、今回の数で数量に届く出荷 (印を付けずに終わる) は見ない。"""
    settings.SEIZOU = {}
    _issue(user, line, processes)
    assert [w.code for w in checks.run(SHIP, {"order_line": line, "qty": Decimal("3"), "close_rest": False}, user)] == []
    assert [w.code for w in checks.run(SHIP, {"order_line": line, "qty": Decimal("10"), "close_rest": True}, user)] == []
    assert [w.code for w in checks.run(SHIP, {"order_line": line, "qty": Decimal("3"), "close_rest": True}, user)] == ["R35"]


# ---------------------------------------------------------------- (g) 文の形


@pytest.mark.django_db
def test_r35_message_lists_three_numbers_then_the_rest(settings, user, line, processes):
    settings.SEIZOU = {}
    made = [_issue(user, line, processes, qty="1") for _ in range(5)]
    numbers = sorted(workorder.wo_no for workorder in made)
    [warning] = checks.run(COMPLETE, {"object": line}, user)
    assert warning.message == f"この明細には、終わっていない指示書が 5 件あります ({'・'.join(numbers[:3])}・ほか 2 件)。" + WARN_TAIL


@pytest.mark.django_db
def test_r35_stop_raises_from_check_layer(settings, user, line, processes):
    """検査の層の文は 2 つの action で同じ (止めるのときは run が頭に「保存できません。」を付ける)。"""
    settings.SEIZOU = {KEY: "stop"}
    workorder = _issue(user, line, processes)
    for action, data in ((COMPLETE, {"object": line}), (SHIP, {"order_line": line, "qty": Decimal("3"), "close_rest": True})):
        with pytest.raises(CheckViolation) as caught:
            checks.run(action, data, user)
        assert caught.value.code == "R35"
        assert caught.value.message == "保存できません。" + _head(workorder.wo_no) + STOP_TAIL
