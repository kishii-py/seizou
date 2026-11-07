"""会社どうしの注文の明細の数量単位コード (unit_code。決め UNIT-1 = a。R5 の候補 C108 の その 4)。

送る側: 発注の明細の数量単位コードを、注文の明細の unit_code の字で送る。
受ける側: 一覧にあるコードが届けばそのコードを使い、無い・一覧に無いコードなら単位の名前から引く。受け入れは断らない。
受け入れの形は tests/test_r10_inbox_accept.py、送る形は tests/test_connect_send.py の物を使う。作り物のデータだけを使う。
"""

import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.connect import services, signing
from seizou.connect.models import Inbox
from seizou.masters import services as masters_services
from seizou.purchasing import services as purchasing_services
from tests.test_connect_send import ENV_NAME as SEND_ENV_NAME
from tests.test_connect_send import PARTNER_URL, FakePost
from tests.test_connect_signing import _new_secret
from tests.test_r10_inbox_accept import ENV_NAME, PARTNER_PART_NO, _line, _payload


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(ENV_NAME, value)
    return value


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "B-200", "name": "作り物のブラケット", "unit": "個"}, user)


@pytest.fixture
def receive_order(user, secret):
    partner = masters_services.create_partner({"code": "R10C", "name": "作り物の得意先", "role": "customer"}, user)
    services.register_connection(partner, "", ENV_NAME, "receive", user)

    def _receive(lines):
        msg_id = str(uuid.uuid4())
        body = json.dumps(_payload(msg_id, lines), ensure_ascii=False).encode("utf-8")
        result = services.receive(body, signing.headers_for(secret, msg_id, body))
        assert isinstance(result, Inbox)
        return result

    return _receive


def _accepted_pair(receive_order, item, user, **unit):
    inbox = receive_order([{**_line(1, PARTNER_PART_NO), **unit}])
    order = services.accept(inbox, {PARTNER_PART_NO: item}, user)
    line = order.lines.get()
    return line.unit, line.unit_code


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ({"unit": "kg", "unit_code": "KGM"}, ("kg", "KGM")),
        ({"unit": "キロ", "unit_code": "KGM"}, ("キロ", "KGM")),
        ({"unit": "", "unit_code": "SET"}, ("セット", "SET")),
        ({"unit": "本"}, ("本", "")),
        ({"unit": "kg", "unit_code": "ZZZ"}, ("kg", "KGM")),
    ],
)
def test_accept_uses_known_unit_code(receive_order, item, user, unit, expected):
    assert _accepted_pair(receive_order, item, user, **unit) == expected


@pytest.fixture
def send_secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(SEND_ENV_NAME, value)
    return value


@pytest.mark.django_db
def test_send_order_carries_unit_code(monkeypatch, user, send_secret):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    subcontractor = masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user
    )
    services.register_connection(subcontractor, PARTNER_URL, SEND_ENV_NAME, "send", user)
    wire = masters_services.create_item({"part_no": "T-200", "name": "作り物の線", "unit": "kg"}, user)

    purchasing_services.create_purchase_order(
        subcontractor,
        [{"item": wire, "qty": Decimal("10"), "unit_price": Decimal("25.5")}],
        user,
        order_date=date(2026, 11, 2),
    )

    (call,) = fake.calls
    (line,) = json.loads(call["data"].decode("utf-8"))["lines"]
    assert line["unit"] == "kg"
    assert line["unit_code"] == "KGM"
