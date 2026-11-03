"""発注に注文回答を当てる関数 (purchasing.services の mark_accepted・mark_rejected) のテスト。

取引先と品目は作り物。相手への送信は無い (つながりを置かない)。
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def po(user):
    partner = masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user
    )
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    return services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal("2"), "unit_price": Decimal("100")}],
        user,
        order_date=date(2026, 11, 2),
    )


@pytest.mark.django_db
def test_mark_accepted_records_replied_at_and_number(user, po):
    number = uuid.uuid4()
    sent = services.mark_sent(po, user)

    accepted = services.mark_accepted(sent, user, reply_message_id=number)

    assert accepted.status == PurchaseOrder.STATUS_ACCEPTED
    stored = PurchaseOrder.objects.get(pk=po.pk)
    assert stored.status == PurchaseOrder.STATUS_ACCEPTED
    assert stored.replied_at is not None
    assert stored.reply_message_id == number

    # 番号を渡さない今の呼び方もそのまま動く
    other = services.create_purchase_order(
        po.partner,
        [{"item": po.lines.get().item, "qty": Decimal("1"), "unit_price": Decimal("10")}],
        user,
        order_date=date(2026, 11, 3),
    )
    accepted = services.mark_accepted(services.mark_sent(other, user), user)
    stored = PurchaseOrder.objects.get(pk=other.pk)
    assert stored.status == PurchaseOrder.STATUS_ACCEPTED
    assert stored.replied_at is not None
    assert stored.reply_message_id is None


@pytest.mark.django_db
def test_mark_rejected_only_from_sent(user, po):
    with pytest.raises(ValueError, match="「送った」の発注だけ"):
        services.mark_rejected(po, user, reason="作り物の理由")
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_DRAFT

    sent = services.mark_sent(po, user)
    number = uuid.uuid4()
    rejected = services.mark_rejected(sent, user, reason="あ" * 501, reply_message_id=number)

    assert rejected.status == PurchaseOrder.STATUS_REJECTED
    stored = PurchaseOrder.objects.get(pk=po.pk)
    assert stored.status == PurchaseOrder.STATUS_REJECTED
    assert stored.reply_note == "あ" * 500
    assert stored.replied_at is not None
    assert stored.reply_message_id == number

    with pytest.raises(ValueError, match="「断られた」の発注は「断られた」にできません"):
        services.mark_rejected(rejected, user, reason="")
    with pytest.raises(ValueError, match="「断られた」の発注は「相手が受け入れた」にできません"):
        services.mark_accepted(rejected, user)


@pytest.mark.django_db
def test_mark_rejected_accepts_empty_reason(user, po):
    rejected = services.mark_rejected(services.mark_sent(po, user), user, reason="")

    assert rejected.status == PurchaseOrder.STATUS_REJECTED
    assert PurchaseOrder.objects.get(pk=po.pk).reply_note == ""


@pytest.mark.django_db
def test_status_labels(user, po):
    assert (PurchaseOrder.STATUS_REJECTED, "断られた") in PurchaseOrder.STATUS_CHOICES
    assert PurchaseOrder.STATUS_REJECTED == "rejected"
    assert len(PurchaseOrder.STATUS_REJECTED) <= PurchaseOrder._meta.get_field("status").max_length

    accepted = services.mark_accepted(services.mark_sent(po, user), user)
    assert accepted.get_status_display() == "相手が受け入れた"

    other = services.create_purchase_order(
        po.partner,
        [{"item": po.lines.get().item, "qty": Decimal("1"), "unit_price": Decimal("10")}],
        user,
        order_date=date(2026, 11, 3),
    )
    rejected = services.mark_rejected(services.mark_sent(other, user), user, reason="作り物の理由")
    assert rejected.get_status_display() == "断られた"
    # 注文回答の番号は詳細と小窓に出さない
    assert PurchaseOrder.detail_hidden_fields == ("reply_message_id",)


@pytest.mark.django_db
def test_cancel_refuses_rejected(user, po):
    rejected = services.mark_rejected(services.mark_sent(po, user), user, reason="作り物の理由")

    with pytest.raises(ValueError, match="「作成」の発注だけ"):
        services.cancel_purchase_order(rejected, user)
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_REJECTED
