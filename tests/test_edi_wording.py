"""EDI の画面と字の言葉のテスト (精査 A1 の想定外 S11・不具合 8、精査 A5 の T5)。

- 注文回答・検収・出荷の知らせの「届いていない: 」は、返事が「届きません」「断られた」で始まるときは付けない (前置きを重ねない)。
- 承認と断るの画面の誤りは、何をすればよいかの日本語にする。
- 取引先との接続の向きの説明は、EDI の 4 段の往復に合わせる。
取引先・品目は作り物。
"""

import pytest

from seizou.connect import services
from seizou.connect.models import Inbox
from seizou.purchasing.models import Receipt
from seizou.shipping.models import Shipment

UNREACHABLE = "届きません: 相手につながりません。つながりの URL と、相手が立っているかを確かめてください。"
REFUSED = f"断られた: {services.MESSAGE_SIGNATURE_MISMATCH}"


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
