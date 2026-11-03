"""注文書の見張り (intake の extract_to_inbox と命令 seizou_intake_watch) のテスト。本物の API は呼ばない (_ask_claude を差し替える)。会社名と品番は作り物。

autouse の fixture が _ask_claude を「呼ばれたら AssertionError」に差し替え、返事が要るテストだけが作り物の返事
(tests/fixtures/intake/order_reply.json の buyer.name を書き換えた物) を返す関数に差し替える。
鍵はテストごとに setenv か delenv で明示する。HTTP は requests.post を「呼んだら落とす」に差し替える。
ファイルは先頭のバイトだけを合わせた作り物の bytes。上げたファイルと見張るフォルダは試験の一時のフォルダに置く。
"""

import json

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from seizou.connect.models import Inbox
from seizou.intake import services
from seizou.intake.models import Extraction
from seizou.masters.services import create_partner
from seizou.orders.models import Order
from tests.test_connect_send import FakePost
from tests.test_intake_extract import FAKE_KEY, PDF_BYTES, refuse_api, reply_data

UNKNOWN_SENDER = "知らない作り物の会社"


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(services, "_ask_claude", refuse_api)


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    yield fake
    assert fake.calls == []


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)


@pytest.fixture
def without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def customer(user):
    return create_partner({"code": "IW01", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def subcontractor(user):
    return create_partner({"code": "IW02", "name": "作り物の協力企業", "role": "subcontractor"}, user)


def answer_with(monkeypatch, sender):
    """_ask_claude を、差出人を sender にした作り物の返事を返す関数に差し替える。呼ばれた model の一覧を返す。"""
    data = reply_data()
    data["buyer"]["name"] = sender
    text = json.dumps(data, ensure_ascii=False)
    calls = []

    def _fake(model, block):
        calls.append(model)
        return text

    monkeypatch.setattr(services, "_ask_claude", _fake)
    return calls


def upload(data=PDF_BYTES, name="order.pdf"):
    return SimpleUploadedFile(name, data, content_type="application/pdf")


def test_extract_to_inbox_matches_sender(with_key, monkeypatch, customer, user):
    answer_with(monkeypatch, "作り物の得意先")

    inbox, created = services.extract_to_inbox(upload(), user)

    assert created is True
    extraction = Extraction.objects.get()
    inbox.refresh_from_db()
    assert inbox.partner == customer
    assert inbox.source == Inbox.SOURCE_INTAKE
    assert inbox.extraction == extraction
    assert inbox.sender_name == "作り物の得意先"
    assert inbox.status == Inbox.STATUS_RECEIVED
    assert inbox.payload == extraction.extracted
    assert extraction.customer == customer
    assert extraction.status == Extraction.STATUS_INBOX
    assert extraction.read_by == user
    assert Order.objects.count() == 0  # R10: 見張りは受注を作らない


def test_unknown_sender_is_waiting(with_key, monkeypatch, customer, user):
    answer_with(monkeypatch, UNKNOWN_SENDER)

    inbox, created = services.extract_to_inbox(upload(), user)

    assert created is True
    inbox.refresh_from_db()
    assert inbox.partner is None
    assert inbox.sender_name == UNKNOWN_SENDER
    assert str(inbox).startswith("注文 差出人の確認待ち")
    extraction = Extraction.objects.get()
    assert extraction.customer is None
    assert extraction.status == Extraction.STATUS_INBOX
    assert str(extraction).startswith("差出人の確認待ち")
    assert Order.objects.count() == 0


def test_two_matching_customers_is_waiting(with_key, monkeypatch, customer, user):
    # 取引先の名前は一意でない (一意なのはコードだけ)。同じ名前が 2 つなら人が選ぶ。
    create_partner({"code": "IW03", "name": "作り物の得意先", "role": "both"}, user)
    answer_with(monkeypatch, "作り物の得意先")

    inbox, _created = services.extract_to_inbox(upload(), user)

    inbox.refresh_from_db()
    assert inbox.partner is None
    assert inbox.sender_name == "作り物の得意先"


@pytest.mark.parametrize(
    ("sender", "code"),
    [
        pytest.param("作り物の　得意先", "IW01", id="full-width-space"),
        pytest.param(" ＸＹＺ作り物 ", "IW04", id="full-width-letters"),
        pytest.param("ｘｙｚ作り物", "IW04", id="case"),
        pytest.param("作り物短縮", "IW05", id="short-name"),
        pytest.param("作り物の協力企業", None, id="subcontractor-only"),
        pytest.param("", None, id="empty"),
    ],
)
def test_sender_matches_by_normalized_name_or_short_name(customer, subcontractor, user, sender, code):
    create_partner({"code": "IW04", "name": "XYZ作り物", "role": "customer"}, user)
    create_partner(
        {"code": "IW05", "name": "作り物の長い名前の得意先", "short_name": "作り物短縮", "role": "both"}, user
    )

    found = services._match_sender(sender)

    assert (found.code if found is not None else None) == code


def test_same_content_is_one_inbox_row(with_key, monkeypatch, customer, user):
    calls = answer_with(monkeypatch, "作り物の得意先")

    first, first_created = services.extract_to_inbox(upload(), user)
    second, second_created = services.extract_to_inbox(upload(name="order-again.pdf"), user)

    assert (first_created, second_created) == (True, False)
    assert second.pk == first.pk
    assert len(calls) == 1  # 2 回目は API を呼ばない
    assert Extraction.objects.count() == 1
    assert Inbox.objects.count() == 1  # R11


def test_watched_extraction_is_not_ordered_from_intake_screen(with_key, monkeypatch, client, customer, user):
    answer_with(monkeypatch, UNKNOWN_SENDER)
    services.extract_to_inbox(upload(), user)
    extraction = Extraction.objects.get()

    with pytest.raises(ValueError, match=services.MESSAGE_IN_INBOX):
        services.accept_extracted(extraction, {}, user)
    assert Order.objects.count() == 0

    client.force_login(user)
    response = client.get(reverse("intake:extraction_detail", args=[extraction.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "届いた注文へ回した" in body
    assert reverse("intake:extraction_accept", args=[extraction.pk]) not in body
