"""受注の取消の確かめに、この受注から作った指示書・出荷・納品書・請求書を並べる (初見テスト R5 の候補 C59。依頼書 N242)。

- 受注と発注の取消の全体の画面が、小窓と同じカード (題の下の黄の 1 行) を出す。
- 受注の取消のカードの「この受注に付いている物」(orders.timeline.linked_for と tag order_cancel_linked)。読むだけで、取消は止めない。
作り物のデータは全部 services で作る。fixture と関数は tests/test_orders_timeline.py から借りる (tests/test_core_department_buttons.py と同じ借り方)。
"""

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.billing import services as billing_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.models import Order
from seizou.orders.timeline import linked_for
from seizou.orders.views import OrderCancelView
from seizou.progress import services as progress_services
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.views import PurchaseOrderCancelView
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services
from seizou.workorders.models import WorkOrder
from tests import test_orders_timeline
from tests.test_core_access import account
from tests.test_core_department_buttons import _departments
from tests.test_core_entry_buttons import _page
from tests.test_orders_timeline import _issue, _order

# 受注の流れの枠の試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
user = test_orders_timeline.user
logged_in = test_orders_timeline.logged_in
customer = test_orders_timeline.customer
item = test_orders_timeline.item
cut = test_orders_timeline.cut

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
LINKED = 'id="order-cancel-linked"'
#: 入力のカードの開き (上の帯と左のメニューのログアウトの form が先に出るので、<form method="post" だけで探さない)
FORM_CARD = '<form method="post" class="rounded-xl'
WORKORDER_NOTE = "受注を取り消しても、指示書は取消になりません。生産計画と進捗の盤から外れます。"
WORKORDER_HOW = "指示書も取消にするときは、指示書の詳細から 1 件ずつ取り消してください。"
PROGRESS_NOTE = "実績の入った指示書は取り消せません。"
KEEP_NOTE = "受注を取り消しても、そのまま残ります。"


@pytest.fixture
def maker(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


def _new_order(user, customer, item):
    return _order(user, customer, item, timezone.localdate() - timedelta(days=3))


def _flowed(user, customer, item, cut):
    """指示書 (発行)・出荷・納品書・請求書がある受注 (受注日は 3 日前、ほかは今日)。"""
    today = timezone.localdate()
    order = _new_order(user, customer, item)
    line = order.lines.get()
    workorder = _issue(user, line, cut)
    shipment = shipping_services.ship(line, Decimal("10"), today, user)
    note = shipping_services.create_delivery_note(customer, [shipment], user)
    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=today)
    return order, workorder, shipment, note, invoice


def _cancel_url(order):
    return reverse("orders:order_cancel", args=[order.pk])


def _get(client, url, **headers):
    response = client.get(url, **headers)
    assert response.status_code == 200, url
    return response.content.decode()


def _alert(body):
    """最初の黄の知らせ (alert-warning から最初の </div> まで)。"""
    alert = body[body.index("alert-warning") :]
    return alert[: alert.index("</div>")]


def _linked(body):
    """「この受注に付いている物」の section の HTML。"""
    start = body.index('<section id="order-cancel-linked"')
    return body[start : body.index("</section>", start)]


def _href(name, pk):
    return f'href="{reverse(name, args=[pk])}"'


# --- (a) 全体の画面の黄 ---


def test_whole_pages_show_yellow_like_modal(logged_in, user, customer, item, maker):
    order = _new_order(user, customer, item)
    po = purchasing_services.create_purchase_order(
        maker,
        [{"item": item, "qty": Decimal("5"), "unit_price": Decimal("300")}],
        user,
        order_date=timezone.localdate(),
        send=False,
    )
    for url, screen_help in (
        (_cancel_url(order), OrderCancelView.screen_help),
        (reverse("purchasing:purchaseorder_cancel", args=[po.pk]), PurchaseOrderCancelView.screen_help),
    ):
        body = _get(logged_in, url)
        assert "<html" in body, url
        assert screen_help in _alert(body), url
        assert "キャンセル" in body, url
        assert "取消の理由" in body, url


# --- (b)〜(d) 指示書 ---


def test_issued_workorder_is_listed(logged_in, user, customer, item, cut):
    order = _new_order(user, customer, item)
    workorder = _issue(user, order.lines.get(), cut)

    body = _get(logged_in, _cancel_url(order))

    assert LINKED in body
    section = _linked(body)
    assert "この受注に付いている物" in section
    assert workorder.wo_no in section
    assert _href("workorders:workorder_detail", workorder.pk) in section
    assert "(1 件)" in section
    assert WORKORDER_NOTE in section
    assert WORKORDER_HOW in section
    assert "実績あり" not in body
    assert PROGRESS_NOTE not in body
    # 並びは黄の知らせの後で、入力のカードの前
    assert body.index("alert-warning") < body.index(LINKED) < body.index(FORM_CARD)


def test_workorder_with_progress_is_marked(logged_in, user, customer, item, cut):
    order = _new_order(user, customer, item)
    workorder = _issue(user, order.lines.get(), cut)
    progress_services.record_progress(workorder.steps.get(), Decimal("1"), timezone.now(), user)

    section = _linked(_get(logged_in, _cancel_url(order)))

    assert f"{workorder.wo_no}</a> (実績あり)" in section
    assert PROGRESS_NOTE in section


def test_cancelled_workorder_is_not_listed(logged_in, user, customer, item, cut):
    order = _new_order(user, customer, item)
    workorder = _issue(user, order.lines.get(), cut)
    workorders_services.cancel_workorder(workorder, user)

    body = _get(logged_in, _cancel_url(order))

    assert workorder.wo_no not in body
    assert LINKED not in body


# --- (e) 出荷・納品書・請求書 ---


def test_shipment_note_invoice_are_listed_in_flow_order(logged_in, user, customer, item, cut):
    order, workorder, shipment, note, invoice = _flowed(user, customer, item, cut)

    section = _linked(_get(logged_in, _cancel_url(order)))

    for href in (
        _href("workorders:workorder_detail", workorder.pk),
        _href("shipping:shipment_detail", shipment.pk),
        _href("shipping:deliverynote_detail", note.pk),
        _href("billing:invoice_detail", invoice.pk),
    ):
        assert href in section, href
    assert KEEP_NOTE in section
    # 群の名前の字は 2 段目の文にも出るので、番号の字の位置で順を見る
    positions = [section.index(no) for no in (workorder.wo_no, shipment.shipment_no, note.note_no, invoice.invoice_no)]
    assert positions == sorted(positions)

    billing_services.cancel_invoice(invoice, user)
    body = _get(logged_in, _cancel_url(order))
    assert invoice.invoice_no not in _linked(body)
    assert note.note_no in _linked(body)


# --- (f) 何も無い受注 ---


def test_order_without_documents_has_no_list(logged_in, user, customer, item):
    order = _new_order(user, customer, item)

    assert LINKED not in _get(logged_in, _cancel_url(order))
    assert LINKED not in _get(logged_in, _cancel_url(order), **MODAL)


# --- (g) 小窓 ---


def test_modal_lists_with_new_tab_links(logged_in, user, customer, item, cut):
    order, workorder, shipment, note, invoice = _flowed(user, customer, item, cut)

    body = _get(logged_in, _cancel_url(order), **MODAL)

    assert "<html" not in body
    assert LINKED in body
    assert "キャンセル" not in body
    section = _linked(body)
    tags = re.findall(r"<a [^>]*>", section)
    assert len(tags) == 4
    for tag in tags:
        assert 'target="_blank" rel="noopener"' in tag, tag


# --- (h) 部署 ---


def test_department_outsider_numbers_are_not_links(settings, client, user, customer, item, cut):
    settings.SEIZOU = {"access_control": {"department": "on", "write": "on", "money": "on"}}
    order, workorder, shipment, note, invoice = _flowed(user, customer, item, cut)
    depts = _departments()
    # 「受注と出荷」の部署は orders と shipping を開け、workorders と billing を開けない。
    # 受注の取消は金額に関わる操作なので、金額の閲覧のある人で開く (無い人には、開いた時点で 403 の画面。tests/test_core_form_open_gate.py)
    jimu = account("jimu", departments=[depts["sales"]], permissions=["slip_edit"], see_money=True)

    body = _page(client, jimu, _cancel_url(order))

    section = _linked(body)
    for no in (workorder.wo_no, shipment.shipment_no, note.note_no, invoice.invoice_no):
        assert no in section, no
    assert _href("workorders:workorder_detail", workorder.pk) not in body
    assert _href("billing:invoice_detail", invoice.pk) not in body
    assert _href("shipping:shipment_detail", shipment.pk) in section
    assert _href("shipping:deliverynote_detail", note.pk) in section


# --- (i) 押すと今までどおり ---


def test_post_cancels_order_and_keeps_workorder(logged_in, user, customer, item, cut):
    order = _new_order(user, customer, item)
    workorder = _issue(user, order.lines.get(), cut)

    response = logged_in.post(_cancel_url(order), {"reason": "作り物の取消の理由"})

    assert response.status_code == 302
    order.refresh_from_db()
    workorder.refresh_from_db()
    assert order.status == Order.STATUS_CANCELLED
    assert workorder.status == WorkOrder.STATUS_ISSUED


# --- linked_for を直に ---


def test_linked_for_groups_in_flow_order(user, customer, item, cut):
    assert linked_for(_new_order(user, customer, item), user) == []

    order, workorder, shipment, note, invoice = _flowed(user, customer, item, cut)
    groups = linked_for(order, user)
    assert [(g.kind, g.label) for g in groups] == [
        ("workorder", "指示書"),
        ("shipment", "出荷"),
        ("delivery_note", "納品書"),
        ("invoice", "請求書"),
    ]
    assert [[d.no for d in g.docs] for g in groups] == [
        [workorder.wo_no],
        [shipment.shipment_no],
        [note.note_no],
        [invoice.invoice_no],
    ]


def test_linked_for_queries_do_not_grow_with_lines(user, customer, item, cut):
    """付いている物は関係ごとに 1 回だけ読む (明細 2 行・記録 2 件ずつでも、明細 1 行と同じ問い合わせの数)。"""
    today = timezone.localdate()
    one, workorder, _, _, _ = _flowed(user, customer, item, cut)
    progress_services.record_progress(workorder.steps.get(), Decimal("1"), timezone.now(), user)
    # 2 つ目の受注は別の得意先 (同じ得意先の同じ締め期間の請求書は 1 枚だけ = R23。出荷日は今日のまま)
    other = masters_services.create_partner({"code": "C002", "name": "作り物の別の得意先", "role": "customer"}, user)
    two = orders_services.create_order(
        other,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")} for _ in range(2)],
        user,
        order_date=today - timedelta(days=3),
    )
    shipments = []
    for line in two.lines.all():
        issued = _issue(user, line, cut)
        progress_services.record_progress(issued.steps.get(), Decimal("1"), timezone.now(), user)
        shipments.append(shipping_services.ship(line, Decimal("10"), today, user))
    shipping_services.create_delivery_note(other, shipments, user)
    billing_services.create_invoice(other, shipments, user, issued_on=today)

    linked_for(Order.objects.get(pk=one.pk), user)  # 利用者の権限の控え (accounts の Access。初回だけ読む) を先に読ませておく
    counts = []
    for order in (Order.objects.get(pk=one.pk), Order.objects.get(pk=two.pk)):
        with CaptureQueriesContext(connection) as queries:
            groups = linked_for(order, user)
        counts.append(len(queries))
        assert [g.kind for g in groups] == ["workorder", "shipment", "delivery_note", "invoice"]
    assert counts[0] == counts[1]
    assert [len(g.docs) for g in linked_for(two, user)] == [2, 2, 1, 1]


def test_linked_for_sorts_numbers_ascending(logged_in, user, customer, item, cut):
    order = _new_order(user, customer, item)
    line = order.lines.get()
    first = _issue(user, line, cut, qty="5")
    second = _issue(user, line, cut, qty="5")

    (group,) = linked_for(order, user)
    assert [d.no for d in group.docs] == sorted([first.wo_no, second.wo_no])
    assert not group.has_progress

    section = _linked(_get(logged_in, _cancel_url(order)))
    assert "(2 件)" in section
    assert section.index(first.wo_no) < section.index(second.wo_no)
