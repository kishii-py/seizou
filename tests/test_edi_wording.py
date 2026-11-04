"""EDI の画面と字の言葉のテスト (精査 A1 の想定外 S11・不具合 8、精査 A5 の T5)。

- 注文回答・検収・出荷の知らせの「届いていない: 」は、返事が「届きません」「断られた」で始まるときは付けない (前置きを重ねない)。
- 承認と断るの画面の誤りは、何をすればよいかの日本語にする。
- 取引先との接続の向きの説明は、EDI の 4 段の往復に合わせる。
取引先・品目は作り物。
"""

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services
from seizou.connect.forms import AcceptForm, ConnectionForm, RejectForm
from seizou.connect.models import Inbox
from seizou.masters import services as masters_services
from seizou.purchasing.models import Receipt
from seizou.shipping.models import Shipment

UNREACHABLE = "届きません: 相手につながりません。つながりの URL と、相手が立っているかを確かめてください。"
REFUSED = f"断られた: {services.MESSAGE_SIGNATURE_MISMATCH}"
ACCEPT_REQUIRED = "品番 T-100 の自社の部品を選んでください。"
REASON_TOO_LONG = "断る理由が 500 字を超えています。500 字までに縮めてください。"
DJANGO_REQUIRED = "このフィールドは必須です"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def inbox(user):
    """相手の品番 T-100 の明細が 1 行の受信した注文 (受け口を通さない近道)。"""
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の発注元", "role": "customer"}, user
    )
    payload = {
        "id": str(uuid.uuid4()),
        "kind": "order",
        "sent_at": "2026-11-02T10:00:00+09:00",
        "order_no": "PO-0001",
        "order_date": "2026-11-02",
        "buyer": {"id": "a-company", "name": "作り物の発注する会社"},
        "supplier": {"code": "S001", "name": "作り物の受ける会社"},
        "note": "",
        "lines": [
            {
                "line_no": 1,
                "part_no": "T-100",
                "part_name": "作り物の軸",
                "qty": "10.00",
                "unit": "個",
                "unit_price": "25.50",
                "due_date": "2026-11-20",
                "note": "",
            }
        ],
    }
    return services.record_inbox(customer, payload, user)


@pytest.mark.parametrize("reply", [UNREACHABLE, REFUSED])
def test_states_do_not_double_prefix(reply):
    assert Inbox(status=Inbox.STATUS_ACCEPTED, reply_response=reply).reply_state() == reply
    assert Receipt(acceptance_response=reply).acceptance_state() == reply
    assert Shipment(notice_response=reply).notice_state() == reply


@pytest.mark.parametrize(
    "reply",
    [
        services.MESSAGE_REPLY_NO_CONNECTION,
        services.MESSAGE_REPLY_SEED,
        services.MESSAGE_ACCEPTANCE_NO_CONNECTION,
        services.MESSAGE_SHIPPING_NO_CONNECTION,
        "鍵の置き場 SEIZOU_PARTNER_X_SECRET に値が無いので送っていません。",
    ],
)
def test_states_keep_prefix_for_other_reasons(reply):
    # つながりが無い・種の読み込み・鍵が無いは、前置きが無いと届いていないことが分からないので残す
    expected = f"届いていない: {reply}"
    assert Inbox(status=Inbox.STATUS_REJECTED, reply_response=reply).reply_state() == expected
    assert Receipt(acceptance_response=reply).acceptance_state() == expected
    assert Shipment(notice_response=reply).notice_state() == expected


@pytest.mark.django_db
def test_accept_form_messages(inbox):
    assert AcceptForm(data={}, instance=inbox).errors["item_1"] == [ACCEPT_REQUIRED]
    assert AcceptForm(data={"item_1": "999999"}, instance=inbox).errors["item_1"] == [
        "品番 T-100 の自社の部品が候補にありません。選び直してください。"
    ]


def test_reject_form_messages():
    assert RejectForm(data={"reason": "   "}).errors["reason"] == [services.MESSAGE_NO_REASON]
    assert RejectForm(data={"reason": "あ" * 501}).errors["reason"] == [REASON_TOO_LONG]


@pytest.mark.django_db
def test_accept_and_reject_screens_show_japanese(client, user, inbox):
    client.force_login(user)

    accept = client.post(reverse("connect:inbox_accept", args=[inbox.pk]), {})
    reject = client.post(reverse("connect:inbox_reject", args=[inbox.pk]), {"reason": "   "})

    accept_body = accept.content.decode()
    reject_body = reject.content.decode()
    assert ACCEPT_REQUIRED in accept_body
    assert DJANGO_REQUIRED not in accept_body
    assert services.MESSAGE_NO_REASON in reject_body
    assert DJANGO_REQUIRED not in reject_body
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_RECEIVED


DIRECTION_HELP = (
    "送る = この会社から相手の seizou へ送る (発注・注文回答・出荷の知らせ・検収)。受ける = 相手から届く物を受ける。"
    "往復には 2 社とも両方の向きが要るので、2 行に分けて登録します。"
)


@pytest.mark.django_db
def test_connection_direction_help(client, user):
    assert ConnectionForm().fields["direction"].help_text == DIRECTION_HELP

    client.force_login(user)
    body = client.get(reverse("connect:connection_create")).content.decode()

    assert DIRECTION_HELP in body
    assert "この会社が発注を送る相手" not in body
