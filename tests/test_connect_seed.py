"""届いた注文を受け口を通さずに入れる関数 (connect.services.record_inbox)。

record_inbox が状態「届いた」・種類「注文」の行を 1 回だけ作ること (同じ番号の 2 回目は 1 回目の行を返す) と、
辞書でない注文のデータを断ることを見る。取引先は作り物。
"""

import uuid

import pytest
from django.contrib.auth import get_user_model

from seizou.connect import services
from seizou.connect.models import Inbox
from seizou.masters import services as masters_services


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.mark.django_db
def test_record_inbox_makes_row_once(user):
    customer = masters_services.create_partner({"code": "C901", "name": "作り物の発注元", "role": "customer"}, user)

    inbox = services.record_inbox(customer, {"order_no": "X-1", "lines": []}, user)

    assert isinstance(inbox, Inbox)
    assert inbox.status == Inbox.STATUS_RECEIVED
    assert inbox.kind == Inbox.KIND_ORDER
    assert inbox.partner == customer
    assert inbox.payload == {"order_no": "X-1", "lines": []}
    assert isinstance(inbox.message_id, uuid.UUID)

    again = services.record_inbox(customer, {"order_no": "X-2", "lines": []}, user, message_id=inbox.message_id)

    assert again.pk == inbox.pk
    assert again.payload == {"order_no": "X-1", "lines": []}  # 2 回目は上書きしない
    assert Inbox.objects.count() == 1

    with pytest.raises(ValueError, match="辞書"):
        services.record_inbox(customer, "X-3", user)
    assert Inbox.objects.count() == 1
