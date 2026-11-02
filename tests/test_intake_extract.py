"""intake の読み取り (extract_order と下ごしらえの関数) のテスト。

本物の API は呼ばない。autouse の fixture が _ask_claude を「呼ばれたら AssertionError」に差し替え、
返事が要るテストだけが作り物の返事 (tests/fixtures/intake/order_reply.json) を返す関数に差し替える。
鍵はテストごとに setenv か delenv で明示する (作業場の環境に本物の鍵があっても読まない)。
ファイルは先頭のバイトだけを合わせた作り物の bytes。品番と会社名は作り物。
"""

import base64
import json
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection

from seizou.intake import services
from seizou.intake.models import Extraction
from seizou.masters.services import create_partner
from seizou.orders.models import Order

REPLY_PATH = Path(__file__).resolve().parent / "fixtures" / "intake" / "order_reply.json"
FAKE_KEY = "test-key-for-intake-tests-not-real"
PDF_BYTES = b"%PDF-1.4\n% made for tests only\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16


def reply_data():
    """作り物の返事の辞書 (毎回読み直すので、テストの中で書き換えてよい)。"""
    return json.loads(REPLY_PATH.read_text(encoding="utf-8"))


def upload(data=PDF_BYTES, name="order.pdf", content_type="application/pdf"):
    return SimpleUploadedFile(name, data, content_type=content_type)


def refuse_api(model, block):
    raise AssertionError("テストが本物の API を呼ぼうとした")


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(services, "_ask_claude", refuse_api)


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)


@pytest.fixture
def without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def fake_reply(no_real_api, monkeypatch):
    """_ask_claude を作り物の返事に差し替える。呼ばれた引数を並べた一覧を返す。"""
    calls = []

    def _fake(model, block):
        calls.append({"model": model, "block": block})
        return REPLY_PATH.read_text(encoding="utf-8")

    monkeypatch.setattr(services, "_ask_claude", _fake)
    return calls


def reply_with(monkeypatch, text):
    """_ask_claude を、決めた字を返す関数に差し替える。"""
    monkeypatch.setattr(services, "_ask_claude", lambda model, block: text)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def customer(user):
    return create_partner({"code": "IN01", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def subcontractor(user):
    return create_partner({"code": "IN02", "name": "作り物の外注先", "role": "subcontractor"}, user)


def test_extract_saves_record(with_key, fake_reply, customer, user):
    result = services.extract_order(upload(), customer, user)

    assert Extraction.objects.count() == 1
    saved = Extraction.objects.get()
    assert saved.pk == result.pk
    assert saved.status == Extraction.STATUS_EXTRACTED
    assert saved.customer == customer
    assert saved.read_by == user
    assert saved.order is None
    assert saved.extracted["order_no"] == "TEST-PO-001"
    assert [line["part_no"] for line in saved.extracted["lines"]] == ["X-100", "X-200"]
    assert saved.extracted["lines"][1]["unit_price"] is None
    assert saved.file.name.startswith("intake/") and saved.file.name.endswith(".pdf")
    with saved.file.open("rb") as stored:
        assert stored.read() == PDF_BYTES
    assert saved.summary() == "X-100 ×10 ほか 1 行"
    assert Order.objects.count() == 0


def test_no_key_is_intake_unavailable(without_key, customer, user):
    with pytest.raises(services.IntakeUnavailable) as caught:
        services.extract_order(upload(), customer, user)

    assert isinstance(caught.value, ValueError)
    assert "鍵が無い" in str(caught.value)
    assert Extraction.objects.count() == 0


def test_is_available_follows_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    assert services.is_available() is True
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert services.is_available() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  ")
    assert services.is_available() is False


def test_unreadable_type_is_refused(with_key, customer, user):
    # Office の生のファイル (zip の頭)。拡張子と送られた種類が PDF でも、先頭のバイトで断る。
    office = upload(b"PK\x03\x04" + b"\x00" * 16, name="order.pdf", content_type="application/pdf")

    with pytest.raises(ValueError) as caught:
        services.extract_order(office, customer, user)

    assert "読めない形式" in str(caught.value)
    assert Extraction.objects.count() == 0


def test_too_large_is_refused(with_key, customer, user, monkeypatch):
    monkeypatch.setattr(services, "MAX_BYTES", 16)

    with pytest.raises(ValueError) as caught:
        services.extract_order(upload(PDF_BYTES + b"0" * 32), customer, user)

    assert "超えています" in str(caught.value)
    assert Extraction.objects.count() == 0


def test_pdf_and_image_blocks(with_key, fake_reply, customer, user):
    services.extract_order(upload(PDF_BYTES, "a.pdf", "application/pdf"), customer, user)
    services.extract_order(upload(PNG_BYTES, "b.png", "image/png"), customer, user)
    services.extract_order(upload(JPEG_BYTES, "c.jpg", "image/jpeg"), customer, user)

    blocks = [call["block"] for call in fake_reply]
    assert [(b["type"], b["source"]["media_type"]) for b in blocks] == [
        ("document", "application/pdf"),
        ("image", "image/png"),
        ("image", "image/jpeg"),
    ]
    assert all(b["source"]["type"] == "base64" for b in blocks)
    assert base64.standard_b64decode(blocks[1]["source"]["data"]) == PNG_BYTES
    assert Extraction.objects.count() == 3


def test_model_from_setting(with_key, fake_reply, customer, user, settings):
    settings.SEIZOU = {"intake_model": "test-model"}

    services.extract_order(upload(), customer, user)

    assert fake_reply[0]["model"] == "test-model"


def test_default_model(with_key, fake_reply, customer, user, settings):
    settings.SEIZOU = {}

    services.extract_order(upload(), customer, user)

    assert fake_reply[0]["model"] == services.DEFAULT_MODEL


@pytest.mark.django_db(transaction=True)
def test_api_called_outside_atomic(with_key, monkeypatch):
    user = get_user_model().objects.create_user("tester-tx", password="pass-for-tests-only")
    customer = create_partner({"code": "IN09", "name": "作り物の得意先", "role": "customer"}, user)
    seen = []

    def _fake(model, block):
        seen.append(connection.in_atomic_block)
        return REPLY_PATH.read_text(encoding="utf-8")

    monkeypatch.setattr(services, "_ask_claude", _fake)

    services.extract_order(upload(), customer, user)

    assert seen == [False]
    assert Extraction.objects.count() == 1


def _refused_reply(monkeypatch, customer, user, text):
    reply_with(monkeypatch, text)
    with pytest.raises(ValueError) as caught:
        services.extract_order(upload(), customer, user)
    assert Extraction.objects.count() == 0
    assert "手入力か受信箱で受けてください" in str(caught.value)
    return str(caught.value)


def test_broken_json_is_value_error(with_key, customer, user, monkeypatch):
    message = _refused_reply(monkeypatch, customer, user, "これは JSON ではない")
    assert "JSON" in message


def test_missing_lines_is_value_error(with_key, customer, user, monkeypatch):
    data = reply_data()
    del data["lines"]
    message = _refused_reply(monkeypatch, customer, user, json.dumps(data, ensure_ascii=False))
    assert "明細がありません" in message


def test_bad_qty_is_value_error(with_key, customer, user, monkeypatch):
    data = reply_data()
    data["lines"][0]["qty"] = "十個"
    message = _refused_reply(monkeypatch, customer, user, json.dumps(data, ensure_ascii=False))
    assert "1 行目の数量" in message


def test_bad_date_is_value_error(with_key, customer, user, monkeypatch):
    data = reply_data()
    data["lines"][1]["due_date"] = "2026/11/27"
    message = _refused_reply(monkeypatch, customer, user, json.dumps(data, ensure_ascii=False))
    assert "2 行目の納期" in message and "YYYY-MM-DD" in message


def test_too_long_part_no_is_value_error(with_key, customer, user, monkeypatch):
    data = reply_data()
    data["lines"][0]["part_no"] = "X" * 36
    message = _refused_reply(monkeypatch, customer, user, json.dumps(data, ensure_ascii=False))
    assert "1 行目の品番" in message and "35 字" in message


def test_non_customer_is_refused(with_key, subcontractor, user):
    with pytest.raises(ValueError) as caught:
        services.extract_order(upload(), subcontractor, user)

    assert "得意先ではありません" in str(caught.value)
    assert Extraction.objects.count() == 0
