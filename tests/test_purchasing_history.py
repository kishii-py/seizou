"""発注と明細の変更の履歴 (決め HIST-1 の伝票の「発注」・PRICE-4f。C106 の その 5)。

発注の書く関数が、発注の詳細の「履歴」に作った・直した・取り消したを残すこと、相手の注文回答で書く所 (user が None) の人の字が
「<取引先の名前> の seizou」になること、明細をまとめて書く所 (取消の版・答えを空に戻す所) が明細ごとに残ること、
送った発注の変更で単価の前と後が残り、金額の閲覧の無い人には単価だけを直した明細の行が出ないこと (決め 5)、詳細の問い合わせの数が履歴の行の数で増えないことを見る。
相手の受け口への HTTP は requests.post を偽の物に差し替える (外へは出ない)。データは作り物だけ。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.connect import services as connect_services
from seizou.core.models import ChangeLog
from seizou.core.services import record_history
from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

ENV_NAME = "SEIZOU_PARTNER_HISTSUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"
MONEY_ON = {"department": "off", "write": "off", "money": "on"}
PASSWORD = "kumo-tests-2611"
PARTNER_BY = "作り物の協力企業 の seizou"


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def items(user):
    return [
        masters_services.create_item({"part_no": f"H-{n}00", "name": f"作り物の部品 {n}", "unit": "個"}, user)
        for n in range(1, 3)
    ]


@pytest.fixture
def connected(monkeypatch, user, subcontractor):
    """向き「送る」のつながりと、相手が受け取ったと返す偽の requests.post。"""
    monkeypatch.setenv(ENV_NAME, _new_secret())
    connect_services.register_connection(subcontractor, PARTNER_URL, ENV_NAME, "send", user)
    monkeypatch.setattr("requests.post", FakePost())
    return subcontractor


def _lines(items):
    return [
        {"item": items[0], "qty": Decimal("10"), "unit_price": Decimal("1100"), "due_date": date(2026, 11, 20)},
        {"item": items[1], "qty": Decimal("5"), "unit_price": Decimal("100"), "due_date": date(2026, 11, 27)},
    ]


def _draft(user, subcontractor, items):
    return services.create_purchase_order(subcontractor, _lines(items), user, order_date=date(2026, 11, 2), send=False)


def _rows(po):
    return list(ChangeLog.objects.filter(owner_model="purchasing.purchaseorder", owner_pk=po.pk))


def _new_rows(count):
    return list(ChangeLog.objects.order_by("pk")[count:])


def _history_part(client, po):
    body = client.get(reverse("purchasing:purchaseorder_detail", args=[po.pk])).content.decode()
    start = body.index('aria-label="履歴"')
    return body[start : body.index("data-last-change", start)]


def _account(login_id):
    accounts_services.issue_account(login_id, PASSWORD, [], [], {}, None)
    return get_user_model().objects.get(username=login_id)


@pytest.mark.django_db
def test_create_records_order_and_lines(user, subcontractor, items):
    po = _draft(user, subcontractor, items)

    rows = _rows(po)
    assert sorted((row.model, row.action) for row in rows) == [
        ("purchasing.purchaseorder", "created"),
        ("purchasing.purchaseorderline", "created"),
        ("purchasing.purchaseorderline", "created"),
    ]


@pytest.mark.django_db
def test_partner_answers_use_partner_name(logged_in, user, subcontractor, items):
    po = _draft(user, subcontractor, items)
    count = ChangeLog.objects.count()

    services.mark_sent(po, None)
    services.mark_accepted(po, None)

    new = _new_rows(count)
    assert [(row.model, row.actor_label) for row in new] == [
        ("purchasing.purchaseorder", PARTNER_BY),
        ("purchasing.purchaseorder", PARTNER_BY),
    ]
    assert new[0].changes == [{"field": "status", "label": "状態", "type": "text", "before": "下書き", "after": "送った"}]
    fields = [change["field"] for change in new[1].changes]
    assert fields == ["status", "replied_at", "reply_type"]  # 番号の uuid (reply_message_id) は残さない
    part = _history_part(logged_in, po)
    assert PARTNER_BY in part
    assert "状態: 送った → 相手が受け入れた" in part

    count = ChangeLog.objects.count()
    services.mark_sent(po, None)  # もう送った = 何も書かない
    assert ChangeLog.objects.count() == count


@pytest.mark.django_db
def test_conditional_then_cleared_lines_are_recorded(user, subcontractor, items):
    po = services.mark_sent(_draft(user, subcontractor, items), user)
    rows = [{"line_no": 2, "reply_type": "CA", "unit_price": Decimal("90"), "qty": Decimal("5"), "due_date": None}]
    count = ChangeLog.objects.count()

    services.mark_conditional(po, None, lines=rows)

    new = _new_rows(count)
    lines = [row for row in new if row.model == "purchasing.purchaseorderline"]
    assert len(lines) == 2 and {row.actor_label for row in new} == {PARTNER_BY}
    second = next(row for row in lines if row.object_pk == po.lines.get(line_no=2).pk)  # lines は明細の行だけ
    assert {"field": "reply_unit_price", "label": "回答単価", "type": "money", "before": "", "after": "90"} in second.changes

    # 条件の話し合いが済んで答えを空に戻す (_clear_line_replies) と、明細ごとに前と後が残る
    count = ChangeLog.objects.count()
    services.mark_change_accepted(po, None)
    new = _new_rows(count)
    cleared = next(row for row in new if row.model == "purchasing.purchaseorderline" and row.object_pk == po.lines.get(line_no=2).pk)
    assert {"field": "reply_unit_price", "label": "回答単価", "type": "money", "before": "90", "after": ""} in cleared.changes
    assert all(row.owner_model == "purchasing.purchaseorder" and row.owner_pk == po.pk for row in new)


@pytest.mark.django_db
def test_on_hold_clears_line_replies_only_when_changed(user, subcontractor, items):
    """答えを空に戻しても、空だった明細は増えない (変わった明細だけ)。"""
    po = services.mark_sent(_draft(user, subcontractor, items), user)
    count = ChangeLog.objects.count()

    services.mark_on_hold(po, None)

    assert [row.model for row in _new_rows(count)] == ["purchasing.purchaseorder"]


@pytest.mark.django_db
def test_change_unit_price_shows_before_after_and_masked(settings, client, logged_in, user, connected, items):
    po = services.create_purchase_order(connected, _lines(items), user, order_date=date(2026, 11, 2))
    assert po.status == PurchaseOrder.STATUS_SENT
    first, second = po.lines.order_by("line_no")
    count = ChangeLog.objects.count()

    services.change_purchase_order(po, {"note": ""}, [{"line": first, "unit_price": Decimal("1180")}, {"line": second}], user)

    new = _new_rows(count)
    own = next(row for row in new if row.model == "purchasing.purchaseorder")
    assert {"field": "revision_no", "label": "注文履歴番号", "type": "number", "before": "1", "after": "2"} in own.changes
    line = next(row for row in new if row.model == "purchasing.purchaseorderline" and row.object_pk == first.pk)
    assert {"field": "unit_price", "label": "単価", "type": "money", "before": "1100", "after": "1180"} in line.changes
    assert "単価: ¥1,100 → ¥1,180" in _history_part(logged_in, po)

    # 金額の閲覧の無い人: 単価だけを直した明細の行は出ず、発注の行 (注文履歴番号) は出る (決め 5)。
    settings.SEIZOU = {"access_control": dict(MONEY_ON)}
    client.force_login(_account("genba"))
    part = _history_part(client, po)
    assert "注文履歴番号: 1 → 2" in part
    assert "(非表示)" not in part and "単価: " not in part and "1,180" not in part


@pytest.mark.django_db
def test_cancel_sent_order_records_cancelled_and_line_codes(user, connected, items):
    po = services.create_purchase_order(connected, _lines(items), user, order_date=date(2026, 11, 2))
    count = ChangeLog.objects.count()

    services.cancel_purchase_order(po, user, reason="作り物の理由")

    new = _new_rows(count)
    own = next(row for row in new if row.model == "purchasing.purchaseorder")
    assert own.action == "cancelled"
    lines = [row for row in new if row.model == "purchasing.purchaseorderline"]
    assert len(lines) == 2
    assert all(any(change["field"] == "line_status_code" for change in row.changes) for row in lines)


@pytest.mark.django_db
def test_draft_cancel_records_cancelled(logged_in, user, subcontractor, items):
    po = _draft(user, subcontractor, items)

    services.cancel_purchase_order(po, user)

    assert _rows(po)[0].action == "cancelled"
    assert "取り消した" in _history_part(logged_in, po)


@pytest.mark.django_db
def test_receipt_delivers_order_and_records_status(user, subcontractor, items):
    po = services.mark_accepted(services.mark_sent(_draft(user, subcontractor, items), user), user)
    first, second = po.lines.order_by("line_no")
    services.receive(first, Decimal("10"), date(2026, 11, 3), user)
    count = ChangeLog.objects.count()

    services.receive(second, Decimal("5"), date(2026, 11, 3), user)

    new = _new_rows(count)
    assert [row.changes for row in new if row.model == "purchasing.purchaseorder"] == [
        [{"field": "status", "label": "状態", "type": "text", "before": "一部納品", "after": "納品された"}]
    ]
    assert not [row for row in new if row.model == "purchasing.receipt"]  # 受入の行そのものは残さない (作らない物)


def _queries(client, url) -> int:
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200
    return len(ctx.captured_queries)


@pytest.mark.django_db
def test_detail_queries_do_not_grow_with_history_rows(logged_in, user, subcontractor, items):
    few = _draft(user, subcontractor, items)
    many = _draft(user, subcontractor, items)
    # 状態と明細を同じにしたまま、履歴の行だけを増やす (金額の項目も描く)
    for n in range(10):
        record_history(many, user, changes=[{"field": "note", "label": "備考", "type": "text", "before": str(n), "after": str(n + 1)}])
        line = many.lines.first()
        record_history(line, None, by=PARTNER_BY, changes=[{"field": "reply_unit_price", "label": "回答単価", "type": "money", "before": "", "after": str(n)}])
    assert len(_rows(many)) > len(_rows(few)) + 10

    few_url = reverse("purchasing:purchaseorder_detail", args=[few.pk])
    many_url = reverse("purchasing:purchaseorder_detail", args=[many.pk])
    for url in (few_url, many_url):  # 最初の 1 回の控えを外す
        assert logged_in.get(url).status_code == 200

    assert _queries(logged_in, many_url) == _queries(logged_in, few_url)
