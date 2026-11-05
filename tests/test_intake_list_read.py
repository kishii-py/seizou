"""注文書の読み取りの一覧の主のボタンと空の枠の字 (N159。本人の決め 11-05 13:3x の 7)。

主のボタンは「読み取る」(押した先の題「注文書を読み取る」と、その画面の主のボタン「読み取る」に合わせる)。行が無いときの空の枠は
「「読み取る」から 1 枚目の注文書を読み取ります。」。intake の一覧のテンプレートと行の部分で出す (core の土台は変えない)。
検索で 0 件のときと行があるときは土台のまま。会社名は作り物。本物の API は呼ばない。
"""

import pytest
from django.urls import reverse

from seizou.intake import services
from seizou.masters.services import create_partner
from tests.test_intake_extract import FAKE_KEY, REPLY_PATH, refuse_api, upload

HX = {"HTTP_HX_REQUEST": "true"}
EMPTY_HINT = "「読み取る」から 1 枚目の注文書を読み取ります。"


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(services, "_ask_claude", refuse_api)


@pytest.fixture
def extraction(monkeypatch, user):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setattr(services, "_ask_claude", lambda model, block: REPLY_PATH.read_text(encoding="utf-8"))
    customer = create_partner({"code": "IV01", "name": "作り物の得意先", "role": "customer"}, user)
    record = services.extract_order(upload(), customer, user)
    monkeypatch.setattr(services, "_ask_claude", refuse_api)
    return record


def test_empty_list_says_read(client_logged_in):
    body = client_logged_in.get(reverse("intake:extraction_list")).content.decode()
    create = reverse("intake:extraction_create")
    assert f'<a class="btn btn-primary" href="{create}">' in body
    assert f'<a class="btn btn-sm btn-outline mt-4 border-base-300" href="{create}">' in body
    assert body.count("読み取る</a>") == 2
    assert EMPTY_HINT in body
    assert "新規登録</a>" not in body
    assert "「新規登録」から 1 件目を登録します。" not in body


def test_htmx_empty_returns_intake_frame(client_logged_in):
    body = client_logged_in.get(reverse("intake:extraction_list"), **HX).content.decode()
    assert "<html" not in body
    assert EMPTY_HINT in body
    assert 'id="list-meta"' in body and 'hx-swap-oob="true"' in body


def test_search_miss_keeps_core_message(client_logged_in):
    body = client_logged_in.get(reverse("intake:extraction_list"), {"q": "作り物の無い字"}).content.decode()
    assert "合う物はありません。" in body
    assert EMPTY_HINT not in body


def test_list_with_rows_keeps_core_table(client_logged_in, extraction):
    body = client_logged_in.get(reverse("intake:extraction_list")).content.decode()
    assert "作り物の得意先" in body
    assert EMPTY_HINT not in body
    assert f'<a class="btn btn-primary" href="{reverse("intake:extraction_create")}">' in body
