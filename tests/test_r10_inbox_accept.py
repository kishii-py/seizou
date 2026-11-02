"""R10: 受信箱の注文は、人が受け入れるまで受注にならない。

守り方は、services.receive が受注を作らないことと、受注を作るのが services.accept だけであること。
受け入れの 2 回目は R11 (同じ注文のデータから受注を 2 件作らない)。
鍵はテストのたびに作り、環境変数に置く。固定の鍵の文字列をファイルに書かない。
注文のデータは送る側と同じ形の辞書を作り、署名して receive に渡す。品番と会社名は作り物。
"""

import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.connect import services, signing
from seizou.connect.models import Inbox
from seizou.core.exceptions import RuleViolation
from seizou.masters.services import (
    create_item,
    create_partner,
    map_partner_item,
    resolve_partner_item,
)
from seizou.orders.models import Order
from tests.test_connect_signing import _new_secret

ENV_NAME = "SEIZOU_PARTNER_R10CO_SECRET"
PARTNER_PART_NO = "A-100"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(ENV_NAME, value)
    return value


@pytest.fixture
def partner(user):
    return create_partner({"code": "R10C", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def connection(partner, user, secret):
    return services.register_connection(partner, "", ENV_NAME, "receive", user)


@pytest.fixture
def item(user):
    return create_item(
        {"part_no": "B-200", "name": "作り物のブラケット", "unit": "個", "standard_price": Decimal("240")},
        user,
    )


def _line(line_no, part_no, *, qty="10.00", unit_price="250.00", due_date="2026-11-20"):
    return {
        "line_no": line_no,
        "part_no": part_no,
        "part_name": f"作り物の部品 {part_no}",
        "qty": qty,
        "unit": "個",
        "unit_price": unit_price,
        "due_date": due_date,
        "note": "",
    }


def _payload(msg_id, lines):
    return {
        "id": msg_id,
        "kind": "order",
        "sent_at": "2026-11-02T10:00:00+09:00",
        "order_no": "PO-0001",
        "order_date": "2026-11-02",
        "buyer": {"id": "a-company", "name": "作り物の発注する会社"},
        "supplier": {"code": "R10C", "name": "作り物の受ける会社"},
        "note": "",
        "lines": lines,
    }


@pytest.fixture
def receive_order(connection, secret):
    def _receive(lines):
        msg_id = str(uuid.uuid4())
        body = json.dumps(_payload(msg_id, lines), ensure_ascii=False).encode("utf-8")
        result = services.receive(body, signing.headers_for(secret, msg_id, body))
        assert isinstance(result, Inbox)
        return result

    return _receive


def test_received_order_is_not_an_order_yet(receive_order):
    inbox = receive_order([_line(1, PARTNER_PART_NO)])

    assert Inbox.objects.count() == 1
    assert Order.objects.count() == 0
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_RECEIVED
    assert inbox.order is None


def test_accept_creates_order_from_inbox(receive_order, partner, item, user):
    inbox = receive_order([_line(1, PARTNER_PART_NO)])

    order = services.accept(inbox, {PARTNER_PART_NO: item}, user)

    assert Order.objects.count() == 1
    assert order.customer == partner
    assert order.source == Order.SOURCE_INBOX
    assert order.customer_order_no == "PO-0001"
    assert order.order_date == date(2026, 11, 2)
    line = order.lines.get()
    assert line.item == item
    assert line.customer_part_no == PARTNER_PART_NO
    assert line.qty == Decimal("10.00")
    assert line.unit_price == Decimal("250.00")
    assert line.due_date == date(2026, 11, 20)
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_ACCEPTED
    assert inbox.order == order


def test_accept_twice_makes_one_order(receive_order, item, user):
    inbox = receive_order([_line(1, PARTNER_PART_NO)])
    services.accept(inbox, {PARTNER_PART_NO: item}, user)

    with pytest.raises(RuleViolation) as excinfo:
        services.accept(inbox, {PARTNER_PART_NO: item}, user)

    assert excinfo.value.code == "R11"
    assert "受注の一覧" in excinfo.value.message
    assert Order.objects.count() == 1


def test_accept_uses_partner_item_table(receive_order, partner, item, user):
    map_partner_item(partner, item, PARTNER_PART_NO, user)
    inbox = receive_order([_line(1, PARTNER_PART_NO)])

    order = services.accept(inbox, {}, user)

    assert order.lines.get().item == item


def test_accept_adds_missing_mapping(receive_order, partner, item, user):
    inbox = receive_order([_line(1, PARTNER_PART_NO), _line(2, PARTNER_PART_NO, qty="5")])
    assert resolve_partner_item(partner, PARTNER_PART_NO) is None

    order = services.accept(inbox, {PARTNER_PART_NO: item}, user)

    assert resolve_partner_item(partner, PARTNER_PART_NO) == item
    assert [line.item for line in order.lines.all()] == [item, item]


def test_accept_without_mapping_is_refused(receive_order, user):
    inbox = receive_order([_line(1, PARTNER_PART_NO)])

    with pytest.raises(ValueError) as excinfo:
        services.accept(inbox, {}, user)

    assert PARTNER_PART_NO in str(excinfo.value)
    assert Order.objects.count() == 0
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_RECEIVED


def test_failed_order_rolls_back_mapping_and_status(receive_order, partner, item, user):
    # 数量 0 は create_order が R02 で止める。足した対応も受信箱の状態も戻る。
    inbox = receive_order([_line(1, PARTNER_PART_NO, qty="0")])

    with pytest.raises(RuleViolation) as excinfo:
        services.accept(inbox, {PARTNER_PART_NO: item}, user)

    assert excinfo.value.code == "R02"
    assert Order.objects.count() == 0
    assert resolve_partner_item(partner, PARTNER_PART_NO) is None
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_RECEIVED
    assert inbox.order is None


def test_reject_with_reason(receive_order, user):
    inbox = receive_order([_line(1, PARTNER_PART_NO)])

    result = services.reject(inbox, "納期に間に合わないため", user)

    assert result.status == Inbox.STATUS_REJECTED
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_REJECTED
    assert inbox.reason == "納期に間に合わないため"
    assert Order.objects.count() == 0


def test_reject_without_reason_is_refused(receive_order, user):
    inbox = receive_order([_line(1, PARTNER_PART_NO)])

    with pytest.raises(ValueError):
        services.reject(inbox, "  ", user)

    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_RECEIVED
    assert inbox.reason == ""


def test_accept_after_reject_is_refused(receive_order, item, user):
    inbox = receive_order([_line(1, PARTNER_PART_NO)])
    services.reject(inbox, "作り物の理由", user)

    with pytest.raises(ValueError):
        services.accept(inbox, {PARTNER_PART_NO: item}, user)

    assert Order.objects.count() == 0
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_REJECTED
