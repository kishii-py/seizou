"""R33: 協力企業に出した工程の戻りの数の食い違い (標準のポカよけ。会社の設定 subcontract_return)。

指示書の工程を指す発注の明細で、渡した日の無い明細を受け入れたら・受入の合計が明細の数量を超えたら・渡した明細を受入の合計が数量に届かないまま
完了にしたら。協力企業に出していて終わっていない明細がある工程に、実績の入力で実績を入れたら。
検査の層 (seizou.core.checks) を設定の 3 つの値 (warn・stop・off) で、受入の新規登録 (purchasing.receive)・発注の明細の「完了にする」
(purchasing.mark_completed)・実績の新規登録 (progress.record_progress) の画面から回す。
作り物のデータは services で作る (tests/test_purchasing_subcontract.py の build_workorder)。
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.core import checks
from seizou.core.exceptions import CheckViolation
from seizou.progress.models import Progress
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrderLine, Receipt
from tests.rules_helpers import rules_md_text
from tests.test_purchasing_subcontract import ORDER_DATE, build_workorder, line_of, subcontract_po

DAY = date(2026, 11, 5)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
RECEIVE = "purchasing.receive"
COMPLETE = "purchasing.mark_completed"
RECORD = "progress.record_progress"
WARN_TAIL = "数と記録に間違いが無ければ、このまま保存できます。"
STOP_TAIL = "数と渡した日を確かめてから、もう一度保存してください。"


@pytest.fixture
def wo(user):
    return build_workorder(user)


def _receipt_post(line, qty, **extra):
    return {"line": line.pk, "qty": qty, "received_on": DAY.isoformat(), **extra}


def _progress_post(step, qty="4", **extra):
    return {"workorder_step": step.pk, "qty": qty, "recorded_at": "2026-11-05T15:00", "note": "", **extra}


def _complete_url(line):
    return reverse("purchasing:purchaseorderline_complete", args=[line.pk])


def _assert_sentences(body, message):
    """文を「。」で切り、1 文ずつ画面にあるかを見る (知らせは最初の 1 文を太字の枠に分けて描くため)。"""
    for sentence in message.split("。"):
        if sentence:
            assert sentence + "。" in body, sentence


def _not_handed(po):
    return f"{po.po_no}-1 は、まだ協力企業に渡した日が入っていません。"


def _over(po, total, qty, diff):
    return f"{po.po_no}-1 は、この受入で戻りが {total} になり、発注の数 {qty} より {diff} 多くなります。"


def _short(po, qty, diff):
    return f"{po.po_no}-1 は、渡した {qty} のうち {diff} が戻らないまま完了になります。"


def _out_text(wo, *pos):
    labels = "・".join(f"{po.po_no}-1" for po in pos)
    return (
        f"指示書 {wo.workorder.wo_no} の工程 2 めっき は、協力企業に出しています ({labels})。"
        "戻りは受入で入れると、この工程の実績になります。"
    )


# ---------------------------------------------------------------- 決まりの行と設定の鍵


def test_r33_row_in_rules_md_and_setting_key():
    assert "渡した日の無い明細を受け入れたら" in rules_md_text("R33")
    assert checks.setting_key_for("R33") == "subcontract_return"
    actions = {check.action for action in (RECEIVE, COMPLETE, RECORD) for check in checks.registered(action) if check.name == "R33"}
    assert actions == {RECEIVE, COMPLETE, RECORD}


@pytest.mark.django_db
def test_r33_setting_on_settings_screen(logged_in):
    body = logged_in.get(reverse("core:settings")).content.decode()
    assert "subcontract_return" in body
    assert "協力企業に出した工程の戻りの数の食い違い (R33)" in body


# ---------------------------------------------------------------- 既定 (warn): 黄の知らせと「このまま保存」


@pytest.mark.django_db
def test_r33_warn_receipt_without_handed_on_then_confirm(settings, logged_in, user, wo):
    """(a) 渡した日の無い明細の受入は黄の知らせ。受入も実績も残らない。「このまま保存」で両方が残る。"""
    settings.SEIZOU = {}
    po, line = subcontract_po(user, wo.subcontractor, wo.step, handed_on=None)
    url = reverse("purchasing:receipt_create")
    first = logged_in.post(url, _receipt_post(line, "4"), **MODAL)
    assert first.status_code == 200
    body = first.content.decode()
    assert "alert-warning" in body and "R33" in body and "このまま保存" in body
    _assert_sentences(body, _not_handed(po) + WARN_TAIL)
    assert not Receipt.objects.exists() and not Progress.objects.exists()

    second = logged_in.post(url, _receipt_post(line, "4", confirmed="1"), **MODAL)
    assert second.status_code == 204
    assert Receipt.objects.count() == 1 and Progress.objects.count() == 1


@pytest.mark.django_db
def test_r33_warn_receipt_over_qty_whole_page(settings, logged_in, user, wo):
    """(b) 数量を超える受入は黄の知らせ (画面の全体)。渡した日も無ければ 2 つの文を続けて出す。"""
    settings.SEIZOU = {}
    po, line = subcontract_po(user, wo.subcontractor, wo.step)
    services.receive(line, Decimal("4"), DAY, user)
    response = logged_in.post(reverse("purchasing:receipt_create"), _receipt_post(line, "8"))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="check"' in body and "R33" in body and 'name="confirmed" value="1"' in body
    _assert_sentences(body, _over(po, 12, 10, 2) + WARN_TAIL)
    assert Receipt.objects.count() == 1

    both_po, both = subcontract_po(user, wo.subcontractor, wo.step, "3", handed_on=None)
    warnings = checks.run(RECEIVE, {"line": both, "qty": Decimal("5")}, user)
    assert [(w.code, w.message) for w in warnings] == [("R33", _not_handed(both_po) + _over(both_po, 5, 3, 2) + WARN_TAIL)]


@pytest.mark.django_db
def test_r33_warn_close_rest_short_then_confirm(settings, logged_in, user, wo):
    """(c) 渡した明細を「残りは受け入れずに完了にする」で数量に届かないまま受け入れると黄の知らせ。「このまま保存」で完了になる。"""
    settings.SEIZOU = {}
    po, line = subcontract_po(user, wo.subcontractor, wo.step)
    url = reverse("purchasing:receipt_create")
    first = logged_in.post(url, _receipt_post(line, "7", close_rest="on"), **MODAL)
    assert first.status_code == 200
    body = first.content.decode()
    assert "alert-warning" in body and "このまま保存" in body
    _assert_sentences(body, _short(po, 10, 3) + WARN_TAIL)
    assert not Receipt.objects.exists()
    assert line_of(line.pk).completed is False

    second = logged_in.post(url, _receipt_post(line, "7", close_rest="on", confirmed="1"), **MODAL)
    assert second.status_code == 204
    assert line_of(line.pk).completed is True
    assert Progress.objects.get().completes_step is True


@pytest.mark.django_db
def test_r33_warn_mark_completed_short_then_confirm(settings, logged_in, user, wo):
    """発注の明細の「完了にする」で、渡した数に受入が届かないまま完了にすると黄の知らせ。「このまま保存」で完了になる。"""
    settings.SEIZOU = {}
    po, line = subcontract_po(user, wo.subcontractor, wo.step)
    services.receive(line, Decimal("4"), DAY, user)
    first = logged_in.post(_complete_url(line), {}, **MODAL)
    assert first.status_code == 200
    body = first.content.decode()
    assert "alert-warning" in body and "R33" in body and "このまま保存" in body
    _assert_sentences(body, _short(po, 10, 6) + WARN_TAIL)
    assert line_of(line.pk).completed is False

    second = logged_in.post(_complete_url(line), {"confirmed": "1"}, **MODAL)
    assert second.status_code == 204
    assert line_of(line.pk).completed is True
    assert Progress.objects.count() == 1


@pytest.mark.django_db
def test_r33_warn_progress_on_subcontracted_step_then_confirm(settings, logged_in, user, wo):
    """実績の入力で、協力企業に出している工程 (出ている数かまだ渡していない数のある明細) に入れると黄の知らせ。"""
    settings.SEIZOU = {}
    first_po, _first = subcontract_po(user, wo.subcontractor, wo.step)
    second_po, _second = subcontract_po(user, wo.subcontractor, wo.step, "3", handed_on=None)
    url = reverse("progress:progress_create")
    response = logged_in.post(url, _progress_post(wo.step))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="check"' in body and "R33" in body and 'name="confirmed" value="1"' in body
    _assert_sentences(body, _out_text(wo, first_po, second_po) + WARN_TAIL)
    assert not Progress.objects.exists()

    saved = logged_in.post(url, _progress_post(wo.step, confirmed="1"))
    assert saved.status_code == 302
    assert Progress.objects.get().qty == Decimal("4")


# ---------------------------------------------------------------- stop: 赤の知らせで止まる


@pytest.mark.django_db
def test_r33_stop_receipt_is_red(settings, logged_in, user, wo):
    settings.SEIZOU = {"subcontract_return": "stop"}
    po, line = subcontract_po(user, wo.subcontractor, wo.step, handed_on=None)
    for extra in ({}, {"confirmed": "1"}):
        response = logged_in.post(reverse("purchasing:receipt_create"), _receipt_post(line, "4", **extra), **MODAL)
        assert response.status_code == 200
        body = response.content.decode()
        assert 'data-notice="stop"' in body and "R33" in body
        _assert_sentences(body, "保存できません。" + _not_handed(po) + STOP_TAIL)
        assert "このまま保存" not in body
    assert not Receipt.objects.exists() and not Progress.objects.exists()


@pytest.mark.django_db
def test_r33_stop_mark_completed_is_red(settings, logged_in, user, wo):
    settings.SEIZOU = {"subcontract_return": "stop"}
    po, line = subcontract_po(user, wo.subcontractor, wo.step)
    services.receive(line, Decimal("4"), DAY, user)
    response = logged_in.post(_complete_url(line), {"confirmed": "1"}, **MODAL)
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body
    _assert_sentences(body, "保存できません。" + _short(po, 10, 6) + STOP_TAIL)
    assert line_of(line.pk).completed is False


@pytest.mark.django_db
def test_r33_stop_progress_is_red(settings, logged_in, user, wo):
    settings.SEIZOU = {"subcontract_return": "stop"}
    po, _line = subcontract_po(user, wo.subcontractor, wo.step)
    response = logged_in.post(reverse("progress:progress_create"), _progress_post(wo.step, confirmed="1"))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body
    _assert_sentences(body, "保存できません。" + _out_text(wo, po) + STOP_TAIL)
    assert not Progress.objects.exists()


@pytest.mark.django_db
def test_r33_stop_message_from_check_layer(settings, user, wo):
    settings.SEIZOU = {"subcontract_return": "stop"}
    po, line = subcontract_po(user, wo.subcontractor, wo.step)
    with pytest.raises(CheckViolation) as caught:
        checks.run(RECEIVE, {"line": line, "qty": Decimal("11")}, user)
    assert caught.value.code == "R33"
    assert caught.value.message == "保存できません。" + _over(po, 11, 10, 1) + STOP_TAIL
    assert "このまま保存" not in caught.value.message


# ---------------------------------------------------------------- off: 知らせ無しで保存される


@pytest.mark.django_db
def test_r33_off_saves_without_notice(settings, logged_in, user, wo):
    settings.SEIZOU = {"subcontract_return": "off"}
    _po, line = subcontract_po(user, wo.subcontractor, wo.step, handed_on=None)
    receipt = logged_in.post(reverse("purchasing:receipt_create"), _receipt_post(line, "12", close_rest="on"), **MODAL)
    assert receipt.status_code == 204
    assert Receipt.objects.count() == 1

    _po, other = subcontract_po(user, wo.subcontractor, wo.step)
    services.receive(other, Decimal("2"), DAY, user)
    assert logged_in.post(_complete_url(other), {}, **MODAL).status_code == 204
    assert line_of(other.pk).completed is True

    _po, _waiting = subcontract_po(user, wo.subcontractor, wo.step, handed_on=None)
    settings.SEIZOU = {"subcontract_return": "off", "over_workorder": "off"}
    assert logged_in.post(reverse("progress:progress_create"), _progress_post(wo.step)).status_code == 302


# ---------------------------------------------------------------- 知らせない物 (stop にして、当たれば止まる形で見る)


@pytest.mark.django_db
def test_r33_silent_for_plain_line_even_over_qty(settings, logged_in, user, wo):
    """指示書の工程を指さない明細の受入と完了は、数量を超えても知らせない (部品の買い入れの受入の動きを変えない)。"""
    settings.SEIZOU = {"subcontract_return": "stop"}
    po = services.create_purchase_order(
        wo.subcontractor,
        [
            {"item": wo.item, "qty": Decimal("5"), "unit_price": Decimal("30")},
            {"item": wo.item, "qty": Decimal("5"), "unit_price": Decimal("30"), "process": wo.processes[1]},
        ],
        user,
        order_date=ORDER_DATE,
    )
    plain, with_process = po.lines.order_by("line_no")
    url = reverse("purchasing:receipt_create")
    assert logged_in.post(url, _receipt_post(plain, "9"), **MODAL).status_code == 204
    assert logged_in.post(url, _receipt_post(with_process, "1", close_rest="on"), **MODAL).status_code == 204
    services.receive(plain, Decimal("1"), DAY, user)
    other = services.create_purchase_order(
        wo.subcontractor, [{"item": wo.item, "qty": Decimal("5"), "unit_price": Decimal("30")}], user, order_date=ORDER_DATE
    ).lines.get()
    services.receive(other, Decimal("1"), DAY, user)
    assert logged_in.post(_complete_url(other), {}, **MODAL).status_code == 204


@pytest.mark.django_db
def test_r33_silent_up_to_qty(settings, logged_in, user, wo):
    """渡した明細の数量までの受入と、数量に届く「残りは受け入れずに完了にする」は知らせない。"""
    settings.SEIZOU = {"subcontract_return": "stop"}
    _po, line = subcontract_po(user, wo.subcontractor, wo.step)
    url = reverse("purchasing:receipt_create")
    assert logged_in.post(url, _receipt_post(line, "4"), **MODAL).status_code == 204
    assert checks.run(RECEIVE, {"line": line, "qty": Decimal("6")}, user) == []
    assert logged_in.post(url, _receipt_post(line, "6", close_rest="on"), **MODAL).status_code == 204
    assert services.received_qty(line) == Decimal("10")


@pytest.mark.django_db
def test_r33_silent_for_cancelled_order_and_bad_qty(settings, user, wo):
    """取消の発注の明細は見ない (services が先に赤で断る)。数が 0 以下の受入も見ない。"""
    settings.SEIZOU = {"subcontract_return": "stop"}
    _po, line = subcontract_po(user, wo.subcontractor, wo.step, handed_on=None)
    assert checks.run(RECEIVE, {"line": line, "qty": Decimal("0")}, user) == []
    assert checks.run(RECEIVE, {"line": line, "qty": Decimal("-1"), "close_rest": True}, user) == []
    cancelled_po, cancelled = subcontract_po(user, wo.subcontractor, wo.step, handed_on=None)
    services.cancel_purchase_order(cancelled_po, user)
    cancelled = line_of(cancelled.pk)
    assert checks.run(RECEIVE, {"line": cancelled, "qty": Decimal("20"), "close_rest": True}, user) == []
    assert checks.run(COMPLETE, {"object": cancelled}, user) == []


@pytest.mark.django_db
def test_r33_silent_mark_completed_cases(settings, user, wo):
    """「完了にする」は、渡した日の無い明細・もう完了の明細・数量まで受け入れた明細には知らせない。"""
    settings.SEIZOU = {"subcontract_return": "stop"}
    _po, waiting = subcontract_po(user, wo.subcontractor, wo.step, handed_on=None)
    assert checks.run(COMPLETE, {"object": waiting}, user) == []
    _po, done = subcontract_po(user, wo.subcontractor, wo.step)
    services.receive(done, Decimal("10"), DAY, user)
    assert checks.run(COMPLETE, {"object": line_of(done.pk)}, user) == []
    _po, completed = subcontract_po(user, wo.subcontractor, wo.step)
    services.receive(completed, Decimal("1"), DAY, user)
    services.mark_completed(completed, user)
    assert checks.run(COMPLETE, {"object": line_of(completed.pk)}, user) == []


@pytest.mark.django_db
def test_r33_silent_progress_on_steps_not_out(settings, logged_in, user, wo):
    """出していない工程 (明細の無い工程と、明細が全部終わった工程) への実績の入力は知らせない。"""
    settings.SEIZOU = {"subcontract_return": "stop"}
    assert logged_in.post(reverse("progress:progress_create"), _progress_post(wo.steps[0])).status_code == 302

    _po, done = subcontract_po(user, wo.subcontractor, wo.step)
    services.receive(done, Decimal("10"), DAY, user)
    _po, closed = subcontract_po(user, wo.subcontractor, wo.step)
    services.receive(closed, Decimal("2"), DAY, user)
    services.mark_completed(closed, user)
    cancelled_po, _cancelled = subcontract_po(user, wo.subcontractor, wo.step)
    services.cancel_purchase_order(cancelled_po, user)
    # 受入から作った実績で予定の数 (10) を超えているので、同じ action の R07 は通すにして R33 だけを見る
    settings.SEIZOU = {"subcontract_return": "stop", "over_workorder": "off"}
    assert checks.run(RECORD, {"workorder_step": wo.step, "qty": Decimal("1")}, user) == []
    assert PurchaseOrderLine.objects.filter(workorder_step=wo.step).count() == 3


@pytest.mark.django_db
def test_r33_saved_message_unchanged_shape(settings, logged_in, user, wo):
    """「このまま保存」の後の済んだ知らせは、受入の字に実績の字を続けた形 (R32)。"""
    settings.SEIZOU = {}
    _po, line = subcontract_po(user, wo.subcontractor, wo.step, handed_on=None)
    response = logged_in.post(reverse("purchasing:receipt_create"), _receipt_post(line, "4", confirmed="1"), **MODAL)
    assert response.status_code == 204
    message = json.loads(response["HX-Trigger"])["seizou:saved"]["message"]
    assert message == f"受入を保存しました。{wo.workorder.wo_no} の工程 2 めっき に実績 4 を記録しました。"
