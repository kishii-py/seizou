"""初見テスト 3 回目 (R3) の言葉と小さな動きの直し。Django の test client で HTML と表の状態を見る。

既にある試験のファイルから import せず、fixture は手本の試験の形を写す。品番と会社名は作り物。
"""

import base64
import json
import re
import secrets
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.connect import signing
from seizou.connect.models import Inbox
from seizou.core.screens import all_screens
from seizou.masters import services as masters_services
from seizou.masters.forms import PartnerForm

RECEIVE_ENV = "SEIZOU_PARTNER_POLISHCO_SECRET"

ROUNDING_HELP = "納品書と請求書の金額の端数 (四捨五入・切上・切捨)。空なら会社の既定に従います。"
TAX_HELP = "消費税の端数 (トータル・各行四捨五入・各行切捨)。空なら会社の既定に従います。"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


def text(response):
    return response.content.decode()


# --- 取引先の入力の端数の説明 ---


@pytest.mark.django_db
def test_partner_form_help_texts_for_rounding():
    fields = PartnerForm().fields
    assert fields["rounding"].help_text == ROUNDING_HELP
    assert fields["tax_mode"].help_text == TAX_HELP
    # 今の 2 行は字のまま
    assert fields["closing_day"].help_text == "1〜31。月末締めは 31"
    assert fields["payment_day"].help_text == "1〜31。月末は 31"


@pytest.mark.django_db
def test_partner_create_shows_rounding_help(client_logged_in):
    response = client_logged_in.get(reverse("masters:partner_create"))
    assert response.status_code == 200
    body = text(response)
    assert ROUNDING_HELP in body and TAX_HELP in body
    # 取引先の種類の字は「協力企業」(古い呼び名は tests/test_wording.py が全部のファイルで見るので、ここには書かない)
    assert "協力企業" in body


# --- 受注と注文残の一覧の切り替えの字 ---


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["orders:order_list", "orders:orderline_list"])
def test_order_lists_toggle_says_shipping_remaining(client_logged_in, url_name):
    response = client_logged_in.get(reverse(url_name))
    assert response.status_code == 200
    body = text(response)
    assert 'aria-current="true">出荷の残りがある物だけ</span>' in body
    assert "残のある物だけ" not in body

    everything = text(client_logged_in.get(reverse(url_name), {"all": "1"}))
    assert ">出荷の残りがある物だけ</a>" in everything
    assert 'aria-current="true">すべて</span>' in everything
    assert "残のある物だけ" not in everything


# --- 届いた注文の詳細の「承認」(形は tests/test_connect_inbox_views.py の receive_order を写した) ---


def _new_secret() -> str:
    """公開の仕様の形 (接頭辞つきの base64) の鍵を実行時に作る。"""
    return "whsec_" + base64.b64encode(secrets.token_bytes(32)).decode()


def _order_payload(msg_id, part_no):
    return {
        "id": msg_id,
        "kind": "order",
        "sent_at": "2026-11-02T10:00:00+09:00",
        "order_no": "PO-0001",
        "order_date": "2026-11-02",
        "buyer": {"id": "a-company", "name": "作り物の発注する会社"},
        "supplier": {"code": "PL01", "name": "作り物の受ける会社"},
        "note": "",
        "lines": [
            {
                "line_no": 1,
                "part_no": part_no,
                "part_name": f"作り物の部品 {part_no}",
                "qty": "10.00",
                "unit": "個",
                "unit_price": "250.00",
                "due_date": "2026-11-20",
                "note": "",
            }
        ],
    }


@pytest.fixture
def receive_secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(RECEIVE_ENV, value)
    return value


@pytest.fixture
def receive_connection(user, receive_secret):
    partner = masters_services.create_partner({"code": "PC01", "name": "作り物の得意先", "role": "customer"}, user)
    return connect_services.register_connection(partner, "", RECEIVE_ENV, "receive", user)


@pytest.fixture
def inbox(receive_connection, receive_secret):
    msg_id = str(uuid.uuid4())
    body = json.dumps(_order_payload(msg_id, "X-100"), ensure_ascii=False).encode("utf-8")
    result = connect_services.receive(body, signing.headers_for(receive_secret, msg_id, body))
    assert isinstance(result, Inbox)
    return result


@pytest.mark.django_db
def test_inbox_detail_button_says_approve(client_logged_in, inbox):
    assert inbox.status == "received"
    response = client_logged_in.get(reverse("connect:inbox_detail", args=[inbox.pk]))
    assert response.status_code == 200
    body = text(response)
    assert f'href="{reverse("connect:inbox_accept", args=[inbox.pk])}"' in body
    assert "承認</a>" in body
    assert "受け入れる</a>" not in body


def test_inbox_detail_usecase_says_approve():
    usecases = {screen.url_name: screen.usecase for screen in all_screens()}
    assert "次にすること: 「承認」か「断る」へ" in usecases["connect:inbox_detail"]


# --- 取引先との接続の一覧の先頭の列 (向き) が詳細への link ---


@pytest.mark.django_db
def test_connection_list_first_column_links_to_detail(client_logged_in, receive_connection):
    response = client_logged_in.get(reverse("connect:connection_list"))
    assert response.status_code == 200
    detail_url = reverse("connect:connection_detail", args=[receive_connection.pk])
    links = re.findall(rf'<a class="link link-hover[^"]*" href="{re.escape(detail_url)}"[^>]*>(.*?)</a>', text(response))
    assert links == ["受ける"]
