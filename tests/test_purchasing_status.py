"""発注の状態を変える関数 (取消・送った・相手が受けた)。取引先と品目は作り物。"""

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
def test_cancel_only_in_draft(user, po):
    sent = services.mark_sent(po, user)
    # 送った発注は、相手へ取消を送れるときだけ取り消せる (この取引先にはつながりが無いので断る)
    with pytest.raises(ValueError, match="^取り消せません。取引先 .* へのつながりが無いので、取消を相手へ送れません。"):
        services.cancel_purchase_order(sent, user)
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_SENT

    partner = po.partner
    item = po.lines.get().item
    draft = services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal("1"), "unit_price": Decimal("10")}],
        user,
        order_date=date(2026, 11, 3),
    )
    cancelled = services.cancel_purchase_order(draft, user)
    assert cancelled.status == PurchaseOrder.STATUS_CANCELLED
    with pytest.raises(ValueError, match="^取り消せません。状態が「取消」の発注は取り消せません。"):
        services.cancel_purchase_order(cancelled, user)


@pytest.mark.django_db
def test_cancel_reason_goes_to_note(user, po):
    PurchaseOrder.objects.filter(pk=po.pk).update(note="作り物の備考")
    cancelled = services.cancel_purchase_order(po, user, reason="数を間違えた")
    assert cancelled.note == "作り物の備考\n取消の理由: 数を間違えた"
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_CANCELLED


@pytest.mark.django_db
def test_cancel_reason_too_long_is_value_error(user, po):
    with pytest.raises(ValueError, match="500 字を超えます"):
        services.cancel_purchase_order(po, user, reason="あ" * 500)
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_DRAFT


@pytest.mark.django_db
def test_mark_sent_and_accepted(user, po):
    with pytest.raises(ValueError, match="「送った」の発注だけ"):
        services.mark_accepted(po, user)

    sent = services.mark_sent(po, user)
    assert sent.status == PurchaseOrder.STATUS_SENT
    # 既に「送った」ならそのまま
    assert services.mark_sent(sent, user).status == PurchaseOrder.STATUS_SENT

    accepted = services.mark_accepted(sent, user)
    assert accepted.status == PurchaseOrder.STATUS_ACCEPTED
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_ACCEPTED
    with pytest.raises(ValueError):
        services.mark_accepted(accepted, user)


@pytest.mark.django_db
def test_mark_sent_refuses_cancelled(user, po):
    cancelled = services.cancel_purchase_order(po, user)
    with pytest.raises(ValueError, match="取り消した発注"):
        services.mark_sent(cancelled, user)
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_CANCELLED
