"""受注の画面 (orders:order_list) の入口 (02 の 8 節の決め 6 = 1 つの目的に 1 つの画面) のテスト。

受注の画面から、手入力の「新規登録」・「読み取る (OCR)」(intake の読み取りの画面へ)・「届いた注文を開く」の 3 つに入る。
左のメニューから「注文書の読み取り」の入口を外した (URL は残る)。手入力の受注は保存した時点で注残に入る (承認の段は無い)。
届いた注文の行は、送る側と同じ形の注文のデータを署名して services.receive に渡して作る。
鍵はテストのたびに作り、環境変数に置く。固定の鍵の文字列をファイルに書かない。品番と会社名は作り物。
"""

import json
import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.connect import signing
from seizou.connect.models import Inbox
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret
from tests.test_r10_inbox_accept import _line, _payload

ENV_NAME = "SEIZOU_PARTNER_ENTRYCO_SECRET"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "EC01", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def receive_order(monkeypatch, customer, user):
    secret = _new_secret()
    monkeypatch.setenv(ENV_NAME, secret)
    monkeypatch.setattr("requests.post", FakePost())
    connect_services.register_connection(customer, "", ENV_NAME, "receive", user)

    def _receive():
        msg_id = str(uuid.uuid4())
        body = json.dumps(_payload(msg_id, [_line(1, "A-100")]), ensure_ascii=False).encode("utf-8")
        result = connect_services.receive(body, signing.headers_for(secret, msg_id, body))
        assert isinstance(result, Inbox)
        return result

    return _receive


def _entrance(body):
    """受注の画面の、届いた注文の入口の段の HTML。"""
    match = re.search(r'<div class="mb-4 flex[^"]*">.*?届いた注文を開く</a>\s*</div>', body, re.S)
    assert match, "届いた注文の入口の段が無い"
    return match.group(0)


def test_order_list_has_ocr_button(client_logged_in):
    body = client_logged_in.get(reverse("orders:order_list")).content.decode()

    assert "読み取る (OCR)" in body
    assert f'href="{reverse("intake:extraction_create")}"' in body
    # 主色のボタンは土台の「新規登録」の 1 つだけ (読み取るは枠だけ)
    primary = re.findall(r'<a class="btn btn-primary"[^>]*>.*?</a>', body, re.S)
    assert len(primary) == 1 and "新規登録" in primary[0]
    assert 'class="btn btn-outline border-base-300" href="/intake/new/"' in body


def test_order_list_shows_pending_count_and_link(client_logged_in, receive_order, user):
    # 0 件でも段と link は出す (link が入口のため)
    empty = _entrance(client_logged_in.get(reverse("orders:order_list")).content.decode())
    assert "届いた注文 (未承認)" in empty
    assert '<span class="font-medium tabular-nums">0</span>' in empty
    assert f'href="{reverse("connect:inbox_list")}"' in empty

    receive_order()
    connect_services.reject(receive_order(), "作り物の断りの理由", user)

    entrance = _entrance(client_logged_in.get(reverse("orders:order_list")).content.decode())

    assert "届いた注文 (未承認)" in entrance
    assert '<span class="font-medium tabular-nums">1</span>' in entrance
    assert "届いた注文を開く" in entrance
    assert f'href="{reverse("connect:inbox_list")}"' in entrance


def test_intake_is_not_in_menu_but_url_works(client_logged_in):
    home = client_logged_in.get(reverse("core:home")).content.decode()

    assert "注文書の読み取り" not in home
    assert f'href="{reverse("intake:extraction_list")}"' not in home
    assert client_logged_in.get(reverse("intake:extraction_list")).status_code == 200
    assert client_logged_in.get(reverse("intake:extraction_create")).status_code == 200


def test_manual_order_goes_to_backlog_without_approval(client_logged_in, customer, user):
    item = masters_services.create_item({"part_no": "M-300", "name": "作り物の軸受", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("5"), "unit_price": Decimal("100"), "due_date": date(2026, 11, 30)}],
        user,
        order_date=date(2026, 11, 3),
    )

    response = client_logged_in.get(reverse("orders:orderline_list"))

    # 手入力の受注は保存した時点で注残に出る (承認の段は無い = 今のまま)
    assert response.status_code == 200
    assert order.lines.get().pk in [row["obj"].pk for row in response.context["rows"]]
    assert order.order_no in response.content.decode()
