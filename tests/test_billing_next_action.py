"""請求書の詳細の頭の右の緑のボタン = その伝票で次にする操作 (UX-2 a。11-06 22:1x。初見テスト R5 の候補 C93)。

発行で残のある請求書は、頭の右に緑の「入金を記録する」(入金の小窓)。入金の枠の同じボタンは白のまま残す (全額入金済みでも押せる)。
小窓で保存した後は、同じ詳細の GET を描き直す (seizou:refresh) ので、同じ GET で頭の形を見る。
作り物のデータは services で作る (請求書は tests/test_billing_payment_views.py の物を借りる)。
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.billing import services as billing_services
from tests import test_billing_payment_views
from tests.test_billing_payment_views import RECEIVED_ON, _invoice, _order_line, _panel
from tests.test_detail_header import _actions

# 入金の画面の試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
user = test_billing_payment_views.user
client_logged_in = test_billing_payment_views.client_logged_in
customer = test_billing_payment_views.customer
item = test_billing_payment_views.item
line = test_billing_payment_views.line
invoice = test_billing_payment_views.invoice

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}


def _detail(client, invoice):
    response = client.get(reverse("billing:invoice_detail", args=[invoice.pk]))
    assert response.status_code == 200
    return response


def _payment_link(invoice):
    detail = reverse("billing:invoice_detail", args=[invoice.pk])
    return f'{reverse("billing:payment_create", args=[invoice.pk])}?next={detail}'


@pytest.mark.django_db
def test_unpaid_invoice_head_has_green_payment(client_logged_in, invoice):
    response = _detail(client_logged_in, invoice)
    body = response.content.decode()
    link = _payment_link(invoice)
    head = _actions(body)

    assert f'<a class="btn btn-primary" href="{link}" data-modal-form="{link}">' in head
    assert "入金を記録する" in head
    # 入金の枠の同じ行き先のボタンは白 (緑は頭の 1 つだけ)
    assert f'<a class="btn btn-sm btn-outline border-base-300" href="{link}" data-modal-form="{link}">' in _panel(response)
    assert body.count("btn-primary") == 1


@pytest.mark.django_db
def test_paid_invoice_head_has_no_green_but_panel_keeps_white(client_logged_in, user, invoice):
    billing_services.record_payment(invoice, RECEIVED_ON, Decimal("16500"), user)

    response = _detail(client_logged_in, invoice)
    body = response.content.decode()
    link = _payment_link(invoice)

    assert "入金を記録する" not in _actions(body)
    assert f'<a class="btn btn-sm btn-outline border-base-300" href="{link}" data-modal-form="{link}">' in _panel(response)
    assert body.count("btn-primary") == 0


@pytest.mark.django_db
def test_cancelled_invoice_has_no_payment_button(client_logged_in, user, customer, item, invoice):
    # 入金のある請求書は取り消せないので、入金の無い 2 枚目を作って取り消す
    unpaid = _invoice(user, customer, _order_line(user, customer, item))
    billing_services.cancel_invoice(unpaid, user)

    response = _detail(client_logged_in, unpaid)
    body = response.content.decode()

    assert reverse("billing:payment_create", args=[unpaid.pk]) not in body
    assert "入金を記録する" not in _actions(body)
    assert "入金を記録する" not in _panel(response)
    assert body.count("btn-primary") == 0


@pytest.mark.django_db
def test_head_green_goes_away_after_modal_payment(client_logged_in, invoice):
    head = _actions(_detail(client_logged_in, invoice).content.decode())
    assert "btn-primary" in head

    response = client_logged_in.post(
        _payment_link(invoice),
        {"received_on": "2026-12-25", "amount": "16500", "method": "transfer", "note": "作り物の入金"},
        **MODAL,
    )
    assert response.status_code == 204

    head = _actions(_detail(client_logged_in, invoice).content.decode())
    assert "入金を記録する" not in head
    assert "btn-primary" not in head
