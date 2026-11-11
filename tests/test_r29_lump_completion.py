"""R29: 一式の発注の明細を、受入があるのに一式の金額の載った受入が無いうちに完了にしたら (標準のポカよけ。会社の設定 lump_completion)。

本人の決め 11-11 12:5x (ポカよけはまず分かるようにし、止めるかは会社が選ぶ) と 11-11 13:1x (481 回目に入れた一式の完了の断りを、知らせるに変える)。
検査の層 (seizou.core.checks) を設定の 3 つの値 (warn・stop・off) で、発注の詳細の「完了にする」の小窓 (purchasing.mark_completed) と、
受入の新規登録の「残りは受け入れずに完了にする」の小窓 (purchasing.receive の close_rest) から回す。
一式の明細は、数量 10 で 4 を受け入れた (受入の金額は 0) 物。作り物のデータは services で作る
(金額の載った受入だけは、受け入れた後に数量を増やした形を services の道で作ると込み入るので、表に直に作る)。
相手の受け口への HTTP は requests.post を偽の物に差し替える (外へは出ない)。
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.core import checks
from seizou.core.exceptions import CheckViolation
from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrderLine, Receipt
from tests.rules_helpers import rules_md_text
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

ORDER_DATE = date(2026, 11, 2)
DAY = date(2026, 11, 5)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
ENV_NAME = "SEIZOU_PARTNER_LUMPSUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"
COMPLETE = "purchasing.mark_completed"
RECEIVE = "purchasing.receive"
R29_HEAD = "一式の明細で、受け入れた物に一式の金額がまだ載っていません。"
R29_REST = "完了にすると、この明細の支払の金額は 0 円のまま残ります。全部を受け入れたことにするときは、残りの数で受入を入れてください。"
R29_MESSAGE = R29_HEAD + R29_REST
ZERO_NOTE = "一式の金額は載っていないので、この明細の支払の金額は 0 円です。"


@pytest.fixture
def items(user):
    return [
        masters_services.create_item({"part_no": f"T-{n}00", "name": f"作り物の部品 {n}", "unit": "個"}, user)
        for n in range(1, 3)
    ]


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


def _po(user, partner, items, qtys, **extra):
    po = services.create_purchase_order(
        partner,
        [
            {"item": item, "qty": Decimal(qty), "unit_price": Decimal("30"), **extra}
            for item, qty in zip(items, qtys, strict=False)
        ],
        user,
        order_date=ORDER_DATE,
    )
    return po, list(po.lines.order_by("line_no"))


@pytest.fixture
def lump_line(user, subcontractor, items):
    """一式の発注の明細 (数量 10・単価 30 の式) に 4 を受け入れた物 (受入の金額は 0)。"""
    _po_obj, (line,) = _po(user, subcontractor, items, ["10"], price_basis="lump")
    services.receive(line, Decimal("4"), DAY, user)
    assert Receipt.objects.get(line=line).amount == Decimal("0")
    return _line(line.pk)


def _line(pk):
    return PurchaseOrderLine.objects.get(pk=pk)


def _complete_url(line):
    return reverse("purchasing:purchaseorderline_complete", args=[line.pk])


def _receipt_post(line, qty, **extra):
    return {"line": line.pk, "qty": qty, "received_on": DAY.isoformat(), "close_rest": "on", **extra}


def _saved(response):
    return json.loads(response["HX-Trigger"])["seizou:saved"]


# ---------------------------------------------------------------- (f) 決まりの行と設定の鍵


def test_r29_row_in_rules_md_and_setting_key():
    assert "一式の発注の明細" in rules_md_text("R29")
    assert checks.setting_key_for("R29") == "lump_completion"
    assert {check.action for check in checks.registered(COMPLETE) + checks.registered(RECEIVE) if check.name == "R29"} == {
        COMPLETE,
        RECEIVE,
    }


@pytest.mark.django_db
def test_r29_setting_on_settings_screen(logged_in):
    body = logged_in.get(reverse("core:settings")).content.decode()
    assert "lump_completion" in body
    assert "一式の金額の載らない発注の明細の完了 (R29)" in body


# ---------------------------------------------------------------- (a)〜(c) 発注の詳細の「完了にする」


@pytest.mark.django_db
def test_r29_warn_on_complete_then_confirm(settings, logged_in, lump_line):
    """(a) 既定 (warn): 黄の知らせと「このまま保存」が出て印は偽のまま。「このまま保存」で印が真になり、受入の金額は 0 のまま。"""
    settings.SEIZOU = {}
    first = logged_in.post(_complete_url(lump_line), {}, **MODAL)
    assert first.status_code == 200
    body = first.content.decode()
    assert "alert-warning" in body and "R29" in body and "このまま保存" in body
    assert R29_HEAD in body and R29_REST in body
    assert 'data-notice="stop"' not in body
    assert _line(lump_line.pk).completed is False

    second = logged_in.post(_complete_url(lump_line), {"confirmed": "1"}, **MODAL)
    assert second.status_code == 204
    saved = _saved(second)
    assert saved["message"].endswith(ZERO_NOTE)
    assert "支払の金額は 0 円です" in saved["message"]
    assert _line(lump_line.pk).completed is True
    assert Receipt.objects.get(line=lump_line).amount == Decimal("0")


@pytest.mark.django_db
def test_r29_warn_on_complete_whole_page(settings, logged_in, lump_line):
    """(a) 画面の全体 (JS の無いとき) でも、右上の黄の知らせと「このまま保存」が出る。"""
    settings.SEIZOU = {}
    response = logged_in.post(_complete_url(lump_line), {})
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="check"' in body and "R29" in body and "このまま保存" in body
    assert 'name="confirmed" value="1"' in body
    assert _line(lump_line.pk).completed is False


@pytest.mark.django_db
def test_r29_stop_on_complete_is_red(settings, logged_in, lump_line):
    """(b) stop: 赤の知らせ「保存できません。一式の明細で、…」で止まり、印は偽。「このまま保存」の印を付けて送っても保存しない。"""
    settings.SEIZOU = {"lump_completion": "stop"}
    response = logged_in.post(_complete_url(lump_line), {}, **MODAL)
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body and "R29" in body
    assert "保存できません。" in body and R29_MESSAGE in body
    assert "このまま保存" not in body
    assert _line(lump_line.pk).completed is False

    again = logged_in.post(_complete_url(lump_line), {"confirmed": "1"}, **MODAL)
    assert again.status_code == 200
    assert _line(lump_line.pk).completed is False


@pytest.mark.django_db
def test_r29_off_on_complete_saves(settings, logged_in, lump_line):
    """(c) off: 知らせ無しで印が真になる (済んだ知らせの字に、支払の金額が 0 円である事が出る)。"""
    settings.SEIZOU = {"lump_completion": "off"}
    response = logged_in.post(_complete_url(lump_line), {}, **MODAL)
    assert response.status_code == 204
    assert _saved(response)["message"].endswith(ZERO_NOTE)
    assert _line(lump_line.pk).completed is True


# ---------------------------------------------------------------- (d) 受入の新規登録の「残りは受け入れずに完了にする」


@pytest.mark.django_db
def test_r29_warn_on_receipt_close_rest_then_confirm(settings, logged_in, lump_line):
    """(d) warn: 1 を受け入れて残りを完了にすると黄の知らせ。受入はまだ残らない。「このまま保存」で受入 (金額 0) が残り、印が真になる。"""
    settings.SEIZOU = {}
    url = reverse("purchasing:receipt_create")
    first = logged_in.post(url, _receipt_post(lump_line, "1"), **MODAL)
    assert first.status_code == 200
    body = first.content.decode()
    assert "alert-warning" in body and "R29" in body and "このまま保存" in body
    assert R29_HEAD in body and R29_REST in body
    assert Receipt.objects.filter(line=lump_line).count() == 1
    assert _line(lump_line.pk).completed is False

    second = logged_in.post(url, _receipt_post(lump_line, "1", confirmed="1"), **MODAL)
    assert second.status_code == 204
    message = _saved(second)["message"]
    assert message.startswith("受入を保存しました。")
    assert message.endswith(ZERO_NOTE)
    receipts = Receipt.objects.filter(line=lump_line).order_by("pk")
    assert [receipt.qty for receipt in receipts] == [Decimal("4"), Decimal("1")]
    assert [receipt.amount for receipt in receipts] == [Decimal("0"), Decimal("0")]
    assert _line(lump_line.pk).completed is True


@pytest.mark.django_db
def test_r29_stop_on_receipt_close_rest_keeps_no_receipt(settings, logged_in, lump_line):
    """(d) stop: 赤の知らせで止まり、受入も残らない。印は偽。"""
    settings.SEIZOU = {"lump_completion": "stop"}
    url = reverse("purchasing:receipt_create")
    for extra in ({}, {"confirmed": "1"}):
        response = logged_in.post(url, _receipt_post(lump_line, "1", **extra), **MODAL)
        assert response.status_code == 200
        body = response.content.decode()
        assert 'data-notice="stop"' in body and "保存できません。" in body and R29_MESSAGE in body
        assert "このまま保存" not in body
    assert Receipt.objects.filter(line=lump_line).count() == 1
    assert _line(lump_line.pk).completed is False


@pytest.mark.django_db
def test_r29_off_on_receipt_close_rest_saves(settings, logged_in, lump_line):
    """(d) off: 知らせ無しで受入 (金額 0) が残り、印が真になる。"""
    settings.SEIZOU = {"lump_completion": "off"}
    response = logged_in.post(reverse("purchasing:receipt_create"), _receipt_post(lump_line, "1"), **MODAL)
    assert response.status_code == 204
    assert _saved(response)["message"].endswith(ZERO_NOTE)
    assert Receipt.objects.filter(line=lump_line).count() == 2
    assert _line(lump_line.pk).completed is True


# ---------------------------------------------------------------- (e) 知らせない物 (stop にして、当たれば止まる形で見る)


@pytest.mark.django_db
def test_r29_silent_for_lump_line_without_receipt(settings, user, subcontractor, items):
    """受入が 0 件の一式の明細の「完了にする」は知らせない (払う物が無い。今のまま)。"""
    settings.SEIZOU = {"lump_completion": "stop"}
    _po_obj, (line,) = _po(user, subcontractor, items, ["10"], price_basis="lump")
    assert checks.run(COMPLETE, {"object": line}, user) == []
    assert services.completes_without_lump_amount(line) is False


@pytest.mark.django_db
def test_r29_silent_for_lump_line_with_amount(settings, user, lump_line):
    """金額の載った受入のある一式の明細は、完了にしても受入で残りを完了にしても知らせない。"""
    settings.SEIZOU = {"lump_completion": "stop"}
    Receipt.objects.create(line=lump_line, received_on=DAY, qty=Decimal("1"), amount=Decimal("30"))
    line = _line(lump_line.pk)
    assert checks.run(COMPLETE, {"object": line}, user) == []
    assert checks.run(RECEIVE, {"line": line, "qty": Decimal("1"), "close_rest": True}, user) == []


@pytest.mark.django_db
def test_r29_silent_for_unit_price_line(settings, user, subcontractor, items):
    """一式でない明細 (単価の単位が部品の単位) は知らせない。"""
    settings.SEIZOU = {"lump_completion": "stop"}
    _po_obj, (line,) = _po(user, subcontractor, items, ["10"])
    services.receive(line, Decimal("4"), DAY, user)
    line = _line(line.pk)
    assert checks.run(COMPLETE, {"object": line}, user) == []
    assert checks.run(RECEIVE, {"line": line, "qty": Decimal("1"), "close_rest": True}, user) == []


@pytest.mark.django_db
def test_r29_silent_when_this_receipt_reaches_qty(settings, logged_in, user, lump_line):
    """受入の入力で、今回の数で数量に届く (一式の全額が載る) ときは知らせない。合計がちょうど数量の回も全額が載る。"""
    settings.SEIZOU = {"lump_completion": "stop"}
    assert checks.run(RECEIVE, {"line": lump_line, "qty": Decimal("6"), "close_rest": True}, user) == []
    assert checks.run(RECEIVE, {"line": lump_line, "qty": Decimal("7"), "close_rest": True}, user) == []
    response = logged_in.post(reverse("purchasing:receipt_create"), _receipt_post(lump_line, "6"), **MODAL)
    assert response.status_code == 204
    # 一式の全額は単価のまま (RULES.md R03)
    assert Receipt.objects.filter(line=lump_line).order_by("-pk").first().amount == Decimal("30")


@pytest.mark.django_db
def test_r29_silent_for_receipt_without_close_rest(settings, logged_in, user, lump_line):
    """「残りは受け入れずに完了にする」の無い受入は知らせない。"""
    settings.SEIZOU = {"lump_completion": "stop"}
    assert checks.run(RECEIVE, {"line": lump_line, "qty": Decimal("1"), "close_rest": False}, user) == []
    assert checks.run(RECEIVE, {"line": lump_line, "qty": Decimal("1")}, user) == []
    post = _receipt_post(lump_line, "1")
    del post["close_rest"]
    response = logged_in.post(reverse("purchasing:receipt_create"), post, **MODAL)
    assert response.status_code == 204
    assert _line(lump_line.pk).completed is False


@pytest.mark.django_db
def test_r29_silent_for_cancelled_order(settings, user, lump_line):
    """取消の発注の一式の明細は知らせない (services が先に赤で断るので、黄の知らせの後に赤を出さないため)。"""
    settings.SEIZOU = {"lump_completion": "stop"}
    services.cancel_purchase_order(lump_line.purchase_order, user)
    line = _line(lump_line.pk)
    assert checks.run(COMPLETE, {"object": line}, user) == []
    assert checks.run(RECEIVE, {"line": line, "qty": Decimal("1"), "close_rest": True}, user) == []


@pytest.mark.django_db
def test_r29_silent_for_cancelled_line(settings, user, items, monkeypatch):
    """取り消した一式の明細は知らせない (受入の「残りは受け入れずに完了にする」は mark_completed が赤で断るため)。"""
    monkeypatch.setattr("requests.post", FakePost())
    monkeypatch.setenv(ENV_NAME, _new_secret())
    partner = masters_services.create_partner({"code": "S002", "name": "作り物の協力企業 2", "role": "subcontractor"}, user)
    connect_services.register_connection(partner, PARTNER_URL, ENV_NAME, "send", user)
    sent, (first, second) = _po(user, partner, items, ["10", "10"], price_basis="lump")
    services.change_purchase_order(sent, {}, [{"line": first}], user)
    line = _line(second.pk)
    assert line.is_cancelled
    settings.SEIZOU = {"lump_completion": "stop"}
    assert checks.run(RECEIVE, {"line": line, "qty": Decimal("1"), "close_rest": True}, user) == []


@pytest.mark.django_db
def test_r29_silent_for_zero_qty_receipt(settings, user, lump_line):
    """今回の数が 0 以下の受入は知らせない (services が先に赤で断る)。"""
    settings.SEIZOU = {"lump_completion": "stop"}
    assert checks.run(RECEIVE, {"line": lump_line, "qty": Decimal("0"), "close_rest": True}, user) == []


@pytest.mark.django_db
def test_r29_stop_raises_from_check_layer(settings, user, lump_line):
    """検査の層の文は 2 つの action で同じ (止めるのときは run が頭に「保存できません。」を付ける)。"""
    settings.SEIZOU = {"lump_completion": "stop"}
    for action, data in ((COMPLETE, {"object": lump_line}), (RECEIVE, {"line": lump_line, "qty": Decimal("1"), "close_rest": True})):
        with pytest.raises(CheckViolation) as caught:
            checks.run(action, data, user)
        assert caught.value.code == "R29"
        assert caught.value.message == "保存できません。" + R29_MESSAGE
    settings.SEIZOU = {}
    warnings = checks.run(COMPLETE, {"object": lump_line}, user)
    assert [(warning.code, warning.message) for warning in warnings] == [("R29", R29_MESSAGE)]
