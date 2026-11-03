"""受入の検収の列と、検収を送る側の services (purchasing)。EDI の 4 段目。

HTTP は no_http で止め、つながりの無い協力企業で発注を作る (送る発注の試験だけ偽の requests.post を使う)。
鍵はテストのたびに作り、環境変数に置く。取引先と品目は作り物。日は固定の値を渡す。
"""

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.connect import services as connect_services
from seizou.connect.models import Outbox
from seizou.core.views import hidden_detail_fields
from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder, Receipt
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

ORDER_DATE = date(2026, 10, 28)
DAY = date(2026, 11, 2)
ENV_NAME = "SEIZOU_PARTNER_TESTSUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def no_http(monkeypatch):
    """HTTP を呼んだら落とす。送った注文の行が無い発注は検収を送らないことを見る。"""

    def fail(*args, **kwargs):
        raise AssertionError("送った注文の行が無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


def _order(partner, item, user, *, send=True):
    return services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("25.5")}],
        user,
        order_date=ORDER_DATE,
        send=send,
    )


@pytest.fixture
def receipt(user, subcontractor, item, no_http):
    po = _order(subcontractor, item, user)
    return services.receive(po.lines.get(), Decimal("4"), DAY, user)


@pytest.mark.django_db
def test_assign_acceptance_number_is_stable(user, receipt):
    first = services.assign_acceptance_number(receipt, user)
    second = services.assign_acceptance_number(receipt, user)

    assert isinstance(first, uuid.UUID)
    assert first == second
    assert Receipt.objects.get(pk=receipt.pk).acceptance_message_id == first


@pytest.mark.django_db
def test_record_acceptance_response_truncates_and_marks_sent(user, receipt):
    saved = services.record_acceptance_response(receipt, "あ" * 600, user)

    assert isinstance(saved, Receipt)
    assert saved is not receipt and saved.pk == receipt.pk
    assert len(saved.acceptance_response) == 500
    assert saved.acceptance_sent_at is None

    sent = services.record_acceptance_response(receipt, "受け取った", user, sent=True)

    assert sent.acceptance_response == "受け取った"
    assert sent.acceptance_sent_at is not None

    # sent が偽の 2 回目は、送った日時を消さない
    again = services.record_acceptance_response(receipt, "返事が読めません (HTTP 500)", user)

    assert again.acceptance_sent_at == sent.acceptance_sent_at
    assert Receipt.objects.get(pk=receipt.pk).acceptance_response == "返事が読めません (HTTP 500)"


def test_acceptance_state_text():
    assert Receipt().acceptance_state() == ""
    assert Receipt(acceptance_response="届きません: 相手につながりません。").acceptance_state() == (
        "届いていない: 届きません: 相手につながりません。"
    )
    sent = Receipt(
        acceptance_sent_at=datetime(2026, 11, 5, 1, 30, tzinfo=UTC), acceptance_response="受け取った"
    )
    # 日時は会社の時刻 (Asia/Tokyo) で出す
    assert sent.acceptance_state() == "送った 2026-11-05 10:30"


@pytest.mark.django_db
def test_receive_without_outbox_calls_no_http(monkeypatch, user, subcontractor, item, no_http):
    other = masters_services.create_partner(
        {"code": "S002", "name": "作り物の送る協力企業", "role": "subcontractor"}, user
    )
    monkeypatch.setenv(ENV_NAME, _new_secret())
    connect_services.register_connection(other, PARTNER_URL, ENV_NAME, "send", user)
    # つながりの無い協力企業の発注と、つながりはあるが送らずに入れた発注 (種の形)
    orders = [_order(subcontractor, item, user), _order(other, item, user, send=False)]

    for po in orders:
        receipt = services.receive(po.lines.get(), Decimal("4"), DAY, user)

        assert services._is_sent_order(po) is False
        assert receipt.acceptance_message_id is None
        assert receipt.acceptance_response == ""


@pytest.mark.django_db
def test_receive_returns_receipt_with_acceptance_columns(monkeypatch, user, subcontractor, item):
    monkeypatch.setenv(ENV_NAME, _new_secret())
    connection = connect_services.register_connection(subcontractor, PARTNER_URL, ENV_NAME, "send", user)
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    po = _order(subcontractor, item, user, send=False)
    Outbox.objects.create(
        message_id=uuid.uuid4(), connection=connection, purchase_order=po, response="受け取った"
    )
    assert services._is_sent_order(po) is True

    receipt = services.receive(po.lines.get(), Decimal("10"), DAY, user)

    (call,) = fake.calls
    assert call["url"] == PARTNER_URL
    assert isinstance(receipt, Receipt)
    assert receipt.acceptance_sent_at is not None
    assert receipt.acceptance_response == "受け取った"
    assert str(receipt.acceptance_message_id) == json.loads(call["data"].decode("utf-8"))["id"]
    # 全部を受け入れたので「納品された」(今までどおり)
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_DELIVERED


def test_receipt_hides_number_in_detail_fields():
    assert Receipt.detail_hidden_fields == ("acceptance_message_id",)
    hidden = hidden_detail_fields(Receipt())
    assert "acceptance_message_id" in hidden
    assert "acceptance_sent_at" not in hidden
    assert "acceptance_response" not in hidden
