"""部署の外の画面へ移るボタンを出さない (決め 11-04 12:5x。精査 A7 の 4 節の 1。依頼書 N101)。

部署の判定が on のとき、ボタンの行き先の部品を部署で開けない人には、押すと部署の 403 になるボタンを出さない。
ボタンを出すかは core の tag can_write (編集の権限 = access.can_write_model と、部署 = access.can_open) だけで決める。
部署の判定が off と log のときは、今までどおり編集の権限だけで決まる (log でも記録の行を書かない)。
部署は作り物の会社の種 (accounts の demo_seed) と同じ部品を、この試験の中で作る。人と名前は作り物。
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.shipping import services as shipping_services
from tests import test_orders_board
from tests.test_billing_payment_views import SHIPPED_AT, _invoice, _order_line
from tests.test_core_access import account
from tests.test_core_entry_buttons import _billing_customer, _page
from tests.test_orders_board import _issue, _line, _tr

# 注残の盤の試験の fixture を借りる (pytest は module の名前で fixture を見つける。N81 の試験と同じ形)。
user = test_orders_board.user
customer = test_orders_board.customer
item = test_orders_board.item
cut = test_orders_board.cut

#: 部署の判定だけを変え、編集の権限と金額の閲覧は on のまま (今までの形 = 編集の権限で決まる、と比べる)
WRITE_AND_MONEY_ON = {"write": "on", "money": "on"}


def _departments():
    """作り物の会社の種と同じ部署 3 つ (部品の並びは accounts/demo_seed.py の DEPARTMENTS の写し)。"""
    return {
        "sales": accounts_services.create_department(
            "受注と出荷", ["orders", "intake", "connect", "shipping", "purchasing", "stock", "masters"], ["orders", "shipping"], None
        ),
        "keiri": accounts_services.create_department("経理", ["billing", "orders", "shipping", "masters"], ["billing"], None),
        "seizo": accounts_services.create_department("製造", ["workorders", "progress", "stock", "masters"], ["workorders"], None),
    }


def _documents(user, customer, item, cut):
    """盤の明細 (指示書あり = 「実績を入れる」が出る)・指示書・納品書 (請求書の前)・請求書 (入金の前) と、ボタンの href。

    workorder_progress は指示書の詳細の工程のタブの「実績を入力」(C16) の href の頭 (閉じの " を含めない。&amp;next= が続く)。
    """
    line = _line(user, customer, item)
    workorder = _issue(user, line, cut, qty="4")
    step = workorder.steps.get(seq=1)
    buyer = _billing_customer(user)
    shipment = shipping_services.ship(_order_line(user, buyer, item), Decimal("4"), SHIPPED_AT, user)
    note = shipping_services.create_delivery_note(buyer, [shipment], user)
    invoice = _invoice(user, buyer, _order_line(user, buyer, item))
    return {
        "line": line,
        "urls": {
            "board": reverse("orders:orderline_list"),
            "workorder": reverse("workorders:workorder_detail", args=[workorder.pk]),
            "note": reverse("shipping:deliverynote_detail", args=[note.pk]),
            "invoice": reverse("billing:invoice_detail", args=[invoice.pk]),
        },
        "progress": f'href="{reverse("progress:progress_create")}?workorder_step={step.pk}"',
        "workorder_progress": f'href="{reverse("progress:progress_create")}?workorder_step={step.pk}&amp;next=',
        "issue": f'href="{reverse("workorders:workorder_create")}?order_line={line.pk}"',
        "ship": f'href="{reverse("shipping:shipment_create")}?order_line={line.pk}"',
        "workorder_cancel": f'href="{reverse("workorders:workorder_cancel", args=[workorder.pk])}"',
        "note_cancel": f'href="{reverse("shipping:deliverynote_cancel", args=[note.pk])}"',
        "note_invoice": f'href="{reverse("billing:invoice_create")}?customer={buyer.pk}&amp;next={reverse("shipping:deliverynote_detail", args=[note.pk])}"',
        "invoice_cancel": f'href="{reverse("billing:invoice_cancel", args=[invoice.pk])}"',
        "payment_create": f'href="{reverse("billing:payment_create", args=[invoice.pk])}?next={reverse("billing:invoice_detail", args=[invoice.pk])}"',
    }


def _row(client, person, docs):
    return _tr(_page(client, person, docs["urls"]["board"]), docs["line"].pk)


@pytest.mark.django_db
def test_department_outsider_buttons_hidden(settings, client, user, customer, item, cut):
    settings.SEIZOU = {"access_control": {"department": "on", **WRITE_AND_MONEY_ON}}
    docs = _documents(user, customer, item, cut)
    depts = _departments()
    # 精査 A7 の手順の 2 人: jimu = 受注と出荷・伝票編集・金額なし、keiri = 経理・請求書・金額あり。
    jimu = account("jimu", departments=[depts["sales"]], permissions=["slip_edit"])
    keiri = account("keiri", departments=[depts["keiri"]], permissions=["billing"], see_money=True)

    # 請求書 (billing は jimu の部署の外): 「入金を記録する」「取り消す」と空のときの案内が無い。
    invoice = _page(client, jimu, docs["urls"]["invoice"])
    assert docs["invoice_cancel"] not in invoice
    assert docs["payment_create"] not in invoice
    assert "「入金を記録する」から" not in invoice
    # 指示書 (workorders は部署の外): 「取り消す」が無い。工程のタブの「実績を入力」(progress も部署の外) も無い (C16)。
    workorder = _page(client, jimu, docs["urls"]["workorder"])
    assert docs["workorder_cancel"] not in workorder
    assert docs["workorder_progress"] not in workorder and "実績を入力" not in workorder
    # 納品書: 「請求書作成」(billing) は無く、「取消」(shipping = 部署の中) は出る。
    note = _page(client, jimu, docs["urls"]["note"])
    assert docs["note_invoice"] not in note and "請求書作成" not in note
    assert docs["note_cancel"] in note
    # 注残の盤: 「実績を入れる」(progress) と「指示書を発行する」(workorders) は無く、「出荷する」(shipping) は出る。
    row = _row(client, jimu, docs)
    assert docs["progress"] not in row and "実績を入れる" not in row
    assert docs["issue"] not in row
    assert docs["ship"] in row

    # keiri の盤 (orders は部署の中・progress は外): 「実績を入れる」が無い。⋮ は空にならない。
    row = _row(client, keiri, docs)
    assert docs["progress"] not in row and "実績を入れる" not in row
    assert "受注の詳細" in row
    # keiri の指示書の詳細 (詳細は部署を見ないので開ける。progress は部署の外): 「実績を入力」が無い。
    workorder = _page(client, keiri, docs["urls"]["workorder"])
    assert docs["workorder_progress"] not in workorder and "実績を入力" not in workorder


@pytest.mark.django_db
def test_department_insider_buttons_shown(settings, client, user, customer, item, cut):
    settings.SEIZOU = {"access_control": {"department": "on", **WRITE_AND_MONEY_ON}}
    docs = _documents(user, customer, item, cut)
    depts = _departments()
    keiri = account("keiri", departments=[depts["keiri"]], permissions=["billing"], see_money=True)
    # 受注と出荷・製造の 2 つの部署 (盤と進捗と指示書を開ける)・伝票編集。
    kenmu = account("kenmu", departments=[depts["sales"], depts["seizo"]], permissions=["slip_edit"])

    invoice = _page(client, keiri, docs["urls"]["invoice"])
    assert docs["invoice_cancel"] in invoice
    assert docs["payment_create"] in invoice
    note = _page(client, keiri, docs["urls"]["note"])
    assert docs["note_invoice"] in note and "請求書作成" in note

    workorder = _page(client, kenmu, docs["urls"]["workorder"])
    assert docs["workorder_cancel"] in workorder
    assert docs["workorder_progress"] in workorder and "実績を入力" in workorder
    row = _row(client, kenmu, docs)
    for entry in (docs["progress"], "実績を入れる", docs["issue"], docs["ship"]):
        assert entry in row, entry


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["off", "log"])
def test_department_off_and_log_keep_write_only(settings, client, caplog, user, customer, item, cut, state):
    settings.SEIZOU = {"access_control": {"department": state, **WRITE_AND_MONEY_ON}}
    docs = _documents(user, customer, item, cut)
    depts = _departments()
    jimu = account("jimu", departments=[depts["sales"]], permissions=["slip_edit"])
    keiri = account("keiri", departments=[depts["keiri"]], permissions=["billing"], see_money=True)

    # 今までどおり編集の権限だけで決まる (伝票編集の jimu には部署の外の部品のボタンも出る)。
    invoice = _page(client, jimu, docs["urls"]["invoice"])
    assert docs["invoice_cancel"] in invoice and docs["payment_create"] in invoice
    assert docs["workorder_cancel"] in _page(client, jimu, docs["urls"]["workorder"])
    note = _page(client, jimu, docs["urls"]["note"])
    assert docs["note_invoice"] in note and docs["note_cancel"] in note
    row = _row(client, jimu, docs)
    for entry in (docs["progress"], docs["issue"], docs["ship"]):
        assert entry in row, entry
    assert docs["progress"] in _row(client, keiri, docs)

    # ボタンの判定は記録の行を書かない (log でも。記録は押した先の画面の門が書く)。
    assert [r for r in caplog.records if r.name == "seizou.access" and "(部署)" in r.getMessage()] == []


@pytest.mark.django_db
def test_department_outsider_sees_invoice_number_without_link_on_note_cancel(settings, client, user, customer, item, cut):
    """請求済みの納品書の取消の確かめ: billing を部署で開けない人には、請求書の番号を字だけで出し、詳細への link を出さない。

    出荷の詳細 (shipping = 部署の中) への link と、黄の断りは出る (初見テスト R5 の候補 C03。依頼書 N196)。
    """
    from seizou.billing.models import Invoice

    settings.SEIZOU = {"access_control": {"department": "on", **WRITE_AND_MONEY_ON}}
    _documents(user, customer, item, cut)
    invoice = Invoice.objects.get()
    shipment = invoice.lines.order_by("pk").first().shipment
    cancel = reverse("shipping:deliverynote_cancel", args=[shipment.delivery_note_id])
    invoice_href = f'href="{reverse("billing:invoice_detail", args=[invoice.pk])}'
    depts = _departments()
    jimu = account("jimu", departments=[depts["sales"]], permissions=["slip_edit"])

    body = _page(client, jimu, cancel)
    assert "alert-warning" in body and "取り消せません。" in body
    assert invoice_href not in body
    assert invoice.invoice_no in body
    assert f'href="{reverse("shipping:shipment_detail", args=[shipment.pk])}"' in body

    # 部署の判定が off なら、同じ人にも請求書の詳細への link を出す (今までどおり)
    settings.SEIZOU = {"access_control": {"department": "off", **WRITE_AND_MONEY_ON}}
    assert invoice_href in _page(client, jimu, cancel)
