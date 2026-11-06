"""発注の詳細の頭の右の緑のボタン = その伝票で次にする操作 (UX-2 a。11-06 22:1x。初見テスト R5 の候補 C93)。

「作成」の発注は今のまま緑の「再送」。ほかは、まだ受け入れていない出荷の知らせがあれば緑の「受入を記録する」(受入の小窓)。
行き先は明細の表の一番上の「受入を記録する」と同じ (明細の順で最初・知らせの古い順で最初)。取消と断られた発注には出さない。
作り物のデータは services で作る (発注と出荷の知らせは tests/test_purchasing_shipping_notice.py の物を借りる)。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder
from seizou.purchasing.templatetags.purchasing_tags import next_receipt
from tests import test_purchasing_shipping_notice
from tests.test_detail_header import _actions
from tests.test_purchasing_shipping_notice import _detail, _notice, _notice_cell, _order

# 出荷の知らせの試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
user = test_purchasing_shipping_notice.user
client_logged_in = test_purchasing_shipping_notice.client_logged_in
subcontractor = test_purchasing_shipping_notice.subcontractor
item = test_purchasing_shipping_notice.item
fake_post = test_purchasing_shipping_notice.fake_post


def _sent(subcontractor, item, user):
    """「送った」の発注 (つながりの無い協力企業の発注は「作成」のまま返るので、送った印を services で付ける)。"""
    return services.mark_sent(_order(subcontractor, item, user, send=False), user)


def _receipt_link(po, line, qty):
    detail = reverse("purchasing:purchaseorder_detail", args=[po.pk])
    return f'{reverse("purchasing:receipt_create")}?line={line.pk}&amp;qty={qty}&amp;next={detail}'


@pytest.mark.django_db
def test_sent_order_with_notice_has_green_receipt(client_logged_in, user, subcontractor, item, fake_post):
    po = _sent(subcontractor, item, user)
    line = po.lines.get()
    _notice(line, "4", date(2026, 11, 5), "SH-0001")
    link = _receipt_link(po, line, "4")

    body = _detail(client_logged_in, po)
    head = _actions(body)

    assert f'<a class="btn btn-primary" href="{link}" data-modal-form="{link}">' in head
    assert "受入を記録する" in head
    assert head.count("btn-primary") == 1
    # 表の「出荷の知らせ」の列にも同じ行き先が残る
    assert f'href="{link}"' in _notice_cell(body)
    assert fake_post.calls == 0


@pytest.mark.django_db
def test_head_receipt_opens_oldest_rest(client_logged_in, user, subcontractor, item, fake_post):
    po = _sent(subcontractor, item, user)
    line = po.lines.get()
    _notice(line, "4", date(2026, 11, 5), "SH-0001")
    _notice(line, "6", date(2026, 11, 7), "SH-0002")
    services.receive(line, Decimal("3"), date(2026, 11, 8), user)

    head = _actions(_detail(client_logged_in, po))

    # 受け入れた 3 は古い知らせ (4) に当たるので、残りは古い知らせの 1
    assert f'href="{_receipt_link(po, line, "1")}"' in head
    receipt = next_receipt(PurchaseOrder.objects.get(pk=po.pk))
    assert receipt["line"].pk == line.pk and receipt["rest_text"] == "1"


@pytest.mark.django_db
def test_no_green_receipt_when_nothing_to_receive(client_logged_in, user, subcontractor, item, fake_post):
    # 全部受け入れた発注
    done = _sent(subcontractor, item, user)
    _notice(done.lines.get(), "10", date(2026, 11, 5), "SH-0001")
    services.receive(done.lines.get(), Decimal("10"), date(2026, 11, 6), user)
    # 知らせの無い送った発注
    quiet = _sent(subcontractor, item, user)
    # 断られた発注 (知らせはある)
    sent = _sent(subcontractor, item, user)
    _notice(sent.lines.get(), "4", date(2026, 11, 5), "SH-0003")
    rejected = services.mark_rejected(sent, user, reason="作り物の断り")

    for po in (done, quiet, rejected):
        head = _actions(_detail(client_logged_in, po))
        assert "受入を記録する" not in head, po.po_no
        assert "btn-primary" not in head, po.po_no
        assert next_receipt(PurchaseOrder.objects.get(pk=po.pk)) is None, po.po_no
    assert fake_post.calls == 0


@pytest.mark.django_db
def test_draft_order_keeps_resend_green(client_logged_in, user, subcontractor, item, fake_post):
    po = _order(subcontractor, item, user, send=False)
    assert po.status == PurchaseOrder.STATUS_DRAFT
    _notice(po.lines.get(), "4", date(2026, 11, 5), "SH-0001")

    head = _actions(_detail(client_logged_in, po))

    assert "再送" in head
    assert "受入を記録する" not in head
    assert head.count("btn-primary") == 1
    assert fake_post.calls == 0
