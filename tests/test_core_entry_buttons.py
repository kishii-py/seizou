"""入口のボタンの隠す形 (決め 11-04 00:0x「隠す側でそろえよう」。精査 A2 の S2)。

編集の判定が on のとき、種類の権限の無い人には押すと断られるボタンを出さない (押したときの断りは今のまま)。
ボタンを出すかは core の tag can_write (core.access.can_write_model と同じ判定) と、印 seizou.hide_money だけで決める。
人は accounts の services で作る。名前は作り物。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.urls import resolve, reverse

from seizou.billing import services as billing_services
from seizou.core import access
from seizou.core.templatetags import seizou_tags
from seizou.manuals import services as manuals_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.templatetags import orders_board
from seizou.purchasing.models import PurchaseOrder
from seizou.shipping import services as shipping_services
from tests import test_orders_board, test_purchasing_receipt_views
from tests.test_billing_payment_views import RECEIVED_ON, SHIPPED_AT, _customer, _invoice, _order_line
from tests.test_core_access import account
from tests.test_orders_board import _issue, _line, _tr
from tests.test_purchasing_receipt_views import _order as _purchase_order
from tests.test_purchasing_shipping_notice import _notice

User = get_user_model()

# 注残の盤の試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
# user = Access の行の無い普通の利用者 tester (精査 A2 と N76 の名前)。customer と item と工程の cut は作り物。
user = test_orders_board.user
customer = test_orders_board.customer
item = test_orders_board.item
cut = test_orders_board.cut
# 相手への HTTP を呼んだら落とす requests.post (つながりの無い協力企業の発注は送らない)。
fake_post = test_purchasing_receipt_views.fake_post

ALL_ON = {"department": "on", "write": "on", "money": "on"}

# ボタンの action → tag に渡す model (その model の新規登録の action と同じ種類の権限で決まる)。
BUTTON_ACTIONS = {
    # 2 注残の盤と受注の詳細
    "shipping.ship": "shipping.Shipment",
    "purchasing.create_purchase_order": "purchasing.PurchaseOrder",
    "orders.cancel_order": "orders.Order",
    # 3 指示書の詳細
    "workorders.cancel_workorder": "workorders.WorkOrder",
    # 4 納品書と請求書
    "shipping.cancel_delivery_note": "shipping.DeliveryNote",
    "billing.create_invoice": "billing.Invoice",
    "billing.cancel_invoice": "billing.Invoice",
    "billing.record_payment": "billing.Invoice",
    "billing.cancel_payment": "billing.Invoice",
    # 5 発注の詳細
    "connect.send_order": "purchasing.PurchaseOrder",
    "purchasing.cancel_purchase_order": "purchasing.PurchaseOrder",
    "purchasing.change_purchase_order": "purchasing.PurchaseOrder",
    "purchasing.receive": "purchasing.PurchaseOrder",
    "connect.send_acceptance": "purchasing.PurchaseOrder",
    # 6 マニュアル
    "manuals.create_folder": "manuals.Manual",
    "manuals.create_manual": "manuals.Manual",
    "manuals.update_folder": "manuals.Manual",
    "manuals.move_folder": "manuals.Manual",
    "manuals.protect_folder": "manuals.Manual",
    "manuals.delete_folder": "manuals.Manual",
    "manuals.move_manual": "manuals.Manual",
    "manuals.protect_manual": "manuals.Manual",
    "manuals.delete_manual": "manuals.Manual",
}


def _create_action(label):
    """tag に渡す model の新規登録の画面 (<app>:<model>_create) の service から作る action。"""
    meta = apps.get_model(label)._meta
    view_class = resolve(reverse(f"{meta.app_label}:{meta.model_name}_create")).func.view_class
    return access.action_name(view_class.service)


# ---------------------------------------------------------------- 1 tag


def test_can_write_tag_is_in_core_only():
    assert "can_write" in seizou_tags.register.tags
    assert "can_write" not in orders_board.register.tags
    assert "board_totals" in orders_board.register.tags


def test_action_kinds_match_create():
    """ボタンの action の種類が、tag に渡す model の新規登録の action の種類と同じ (tag の答え = 押したときの答え)。"""
    for action, label in BUTTON_ACTIONS.items():
        assert access.ACTION_KINDS[action] == access.ACTION_KINDS[_create_action(label)], action
        assert action not in access.ACCOUNT_ACTIONS, action
    # 入金の記録は金額が主な登録 (保存で金額の門が断る)。tag の答えは金額を見ない。
    assert "billing.record_payment" in access.MONEY_ACTIONS


# ---------------------------------------------------------------- 共通


@pytest.fixture
def on(settings):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}


@pytest.fixture
def admin(db):
    return User.objects.create_superuser("admin-tester", password="pass-for-tests-only")


def _page(client, person, url):
    """person でログインし直して url の HTML を返す (access_for の控えは利用者の物ごとなので、毎回表から取り直す)。"""
    client.force_login(User.objects.get(pk=person.pk))
    response = client.get(url)
    assert response.status_code == 200, url
    return response.content.decode()


def _section(body, heading_id):
    """aria-labelledby が heading_id の section の HTML。"""
    start = body.index(f'aria-labelledby="{heading_id}"')
    return body[start : body.index("</section>", start)]


# ---------------------------------------------------------------- 2 注残の盤と受注の詳細


def _line_entries(line):
    """明細の行の ⋮ の書く入口 (出荷・指示書の発行・発注) の href と字。"""
    return (
        f'href="{reverse("shipping:shipment_create")}?order_line={line.pk}"',
        f'href="{reverse("workorders:workorder_create")}?order_line={line.pk}"',
        f'href="{reverse("purchasing:purchaseorder_create")}?from_line={line.pk}"',
        "出荷する",
        "指示書を発行する",
        "発注する",
    )


@pytest.mark.django_db
def test_board_menu_hides_ship_and_po(settings, client, on, user, customer, item, admin):
    line = _line(user, customer, item)
    entries = _line_entries(line)
    board_url = reverse("orders:orderline_list")

    def row_for(person):
        return _tr(_page(client, person, board_url), line.pk)

    denied = row_for(user)
    for entry in entries:
        assert entry not in denied, entry
    # ⋮ は空にならない (受注の詳細はいつも出る)。
    assert "受注の詳細" in denied

    jimu = account("jimu", permissions=["slip_edit"])
    for person in (jimu, admin):
        allowed = row_for(person)
        for entry in entries:
            assert entry in allowed, (person.username, entry)

    # 既定 (log) では権限が無くても出る (断りは保存のとき)。
    settings.SEIZOU = {}
    logged = row_for(user)
    for entry in entries:
        assert entry in logged, entry


@pytest.mark.django_db
def test_order_detail_hides_cancel_ship_and_po(client, on, user, customer, item, admin):
    line = _line(user, customer, item)
    order = line.order
    entries = _line_entries(line)
    detail_url = reverse("orders:order_detail", args=[order.pk])
    cancel_href = f'href="{reverse("orders:order_cancel", args=[order.pk])}"'
    list_href = f'href="{reverse("orders:order_list")}"'

    denied = _page(client, user, detail_url)
    assert cancel_href not in denied
    lines_html = _section(denied, "order-lines-title")
    for entry in entries:
        assert entry not in lines_html, entry
    # ⋮ は空にならない (「部品の情報を見る」はいつも出る)。一覧へはパンくずで戻る (見るだけの口)。
    assert "部品の情報を見る" in lines_html
    assert list_href in denied and "一覧に戻る" not in denied  # 一覧へはパンくずで戻る (N163)

    jimu = account("jimu", permissions=["slip_edit"])
    for person in (jimu, admin):
        allowed = _page(client, person, detail_url)
        assert cancel_href in allowed, person.username
        lines_html = _section(allowed, "order-lines-title")
        for entry in entries:
            assert entry in lines_html, (person.username, entry)

    # 取消の受注は、システム管理者にも「取消」を出さない (今の形のまま)。
    orders_services.cancel_order(order, admin)
    assert cancel_href not in _page(client, admin, detail_url)


@pytest.mark.django_db
def test_order_detail_head_ship_button_by_permission(client, on, user, customer, item, admin):
    """受注の詳細の頭の緑の「出荷登録」(候補 C15 の 4。C15-1 b・UX-2 a) は、出荷の書く権限のある人だけに出す。

    出すときの「編集」は白。受注の書く権限の無い人には「編集」を出さない (今のまま)。止める処理は足さない。
    """
    line = _line(user, customer, item)
    order = line.order
    detail_url = reverse("orders:order_detail", args=[order.pk])
    ship_url = f'{reverse("shipping:shipment_create")}?order_line={line.pk}'
    ship_button = f'<a class="btn btn-primary" href="{ship_url}" data-modal-form="{ship_url}">'
    edit_href = f'href="{reverse("orders:order_update", args=[order.pk])}'
    green_edit = f'<a class="btn btn-primary" {edit_href}'
    white_edit = f'<a class="btn btn-outline border-base-300" {edit_href}'

    # Access の行の無い人と、受注だけの権限の人 (出荷は別の「出荷と納品書」) には無い。受注だけの人の「編集」は緑のまま。
    assert "出荷登録" not in _page(client, user, detail_url)
    eigyo = account("eigyo", permissions=["orders"])
    page = _page(client, eigyo, detail_url)
    assert "出荷登録" not in page
    assert green_edit in page and white_edit not in page

    # 伝票編集 (受注と出荷を含む) の人とシステム管理者には緑の「出荷登録」(明細 1 行なので小窓を直に開く) と白の「編集」
    jimu = account("jimu", permissions=["slip_edit"])
    for person in (jimu, admin):
        page = _page(client, person, detail_url)
        assert ship_button in page and "出荷登録" in page, person.username
        assert white_edit in page and green_edit not in page, person.username

    # 出荷だけの人には緑の「出荷登録」だけが出て、受注の「編集」は無い
    shukka = account("shukka", permissions=["shipping"])
    page = _page(client, shukka, detail_url)
    assert ship_button in page
    assert edit_href not in page


# ---------------------------------------------------------------- 3 指示書の詳細


@pytest.mark.django_db
def test_workorder_cancel_hidden_no_accept(settings, client, on, user, customer, item, cut):
    workorder = _issue(user, _line(user, customer, item), cut, qty="4")
    detail_url = reverse("workorders:workorder_detail", args=[workorder.pk])
    cancel_href = f'href="{reverse("workorders:workorder_cancel", args=[workorder.pk])}"'

    denied = _page(client, user, detail_url)
    assert cancel_href not in denied
    # 「着手」のボタンは誰にも出ない (状態は実績から出す。初見テスト R5 の候補 C27)。
    assert "着手</button>" not in denied

    planner = account("planner", permissions=["workorders"])
    allowed = _page(client, planner, detail_url)
    assert cancel_href in allowed and "着手</button>" not in allowed

    settings.SEIZOU = {}
    assert cancel_href in _page(client, user, detail_url)


# ---------------------------------------------------------------- 4 納品書と請求書


def _billing_customer(user):
    """締め日と支払の決まりのある得意先 (請求書の試験と同じ形)。"""
    return _customer(user, code="C101", closing_day=20, payment_month="next", payment_day=25)


@pytest.mark.django_db
def test_delivery_note_hides_cancel_and_invoice(client, on, user, item):
    buyer = _billing_customer(user)
    shipment = shipping_services.ship(_order_line(user, buyer, item), Decimal("4"), SHIPPED_AT, user)
    note = shipping_services.create_delivery_note(buyer, [shipment], user)
    detail_url = reverse("shipping:deliverynote_detail", args=[note.pk])
    cancel_href = f'href="{reverse("shipping:deliverynote_cancel", args=[note.pk])}"'
    invoice_href = f'href="{reverse("billing:invoice_create")}?customer={buyer.pk}&amp;next={detail_url}"'
    documents = (
        f'href="{reverse("shipping:deliverynote_print", args=[note.pk])}"',
        f'href="{reverse("shipping:deliverynote_pdf", args=[note.pk])}"',
    )

    denied = _page(client, user, detail_url)
    assert cancel_href not in denied
    assert invoice_href not in denied and "請求書作成" not in denied
    # 納品書は金額の門の書面ではないので、印刷用の画面と PDF は出す。
    for href in documents:
        assert href in denied, href

    jimu = account("jimu", permissions=["slip_edit"])
    allowed = _page(client, jimu, detail_url)
    assert cancel_href in allowed
    assert invoice_href in allowed and "請求書作成" in allowed


@pytest.mark.django_db
def test_invoice_hides_cancel_payment_and_documents(settings, client, on, user, item):
    buyer = _billing_customer(user)
    invoice = _invoice(user, buyer, _order_line(user, buyer, item))
    payment = billing_services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), user)
    unpaid = _invoice(user, buyer, _order_line(user, buyer, item))
    detail_url = reverse("billing:invoice_detail", args=[invoice.pk])
    unpaid_url = reverse("billing:invoice_detail", args=[unpaid.pk])
    writes = (
        f'href="{reverse("billing:invoice_cancel", args=[invoice.pk])}"',
        f'href="{reverse("billing:payment_create", args=[invoice.pk])}?next={detail_url}"',
        f'href="{reverse("billing:payment_cancel", args=[payment.pk])}"',
    )
    documents = (
        f'href="{reverse("billing:invoice_print", args=[invoice.pk])}"',
        f'href="{reverse("billing:invoice_pdf", args=[invoice.pk])}"',
    )
    guide = "「入金を記録する」から"

    denied = _page(client, user, detail_url)
    for href in (*writes, *documents):
        assert href not in denied, href
    assert guide not in _page(client, user, unpaid_url)

    # 伝票編集で金額の閲覧が無い人: 書く入口は出し、金額の書面 (印刷と PDF) は出さない。
    jimu = account("jimu", permissions=["slip_edit"])
    clerk = _page(client, jimu, detail_url)
    for href in writes:
        assert href in clerk, href
    for href in documents:
        assert href not in clerk, href
    assert guide in _page(client, jimu, unpaid_url)

    keiri = account("keiri", permissions=["billing"], see_money=True)
    full = _page(client, keiri, detail_url)
    for href in (*writes, *documents):
        assert href in full, href

    settings.SEIZOU = {}
    logged = _page(client, user, detail_url)
    for href in (*writes, *documents):
        assert href in logged, href
    assert guide in _page(client, user, unpaid_url)


# ---------------------------------------------------------------- 5 発注の詳細


@pytest.mark.django_db
def test_purchase_order_hides_receipt_and_actions(settings, client, on, user, item, fake_post):
    partner = masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user
    )
    # つながりの無い協力企業の発注は送られず「作成」のまま = 「送り直す」「取り消す」が出る形。
    po = _purchase_order(partner, user, (item, "10", "25"))
    assert po.status == PurchaseOrder.STATUS_DRAFT
    line = po.lines.get()
    _notice(line, "4", date(2026, 11, 5), "SH-0001")
    detail_url = reverse("purchasing:purchaseorder_detail", args=[po.pk])
    receipt_url = reverse("purchasing:receipt_create")
    entries = (
        f'href="{receipt_url}?line={line.pk}&amp;next={detail_url}"',
        f'href="{receipt_url}?line={line.pk}&amp;qty=',
        "受入を登録",
        "受入を記録する",
        f'action="{reverse("purchasing:purchaseorder_send", args=[po.pk])}"',
        f'href="{reverse("purchasing:purchaseorder_cancel", args=[po.pk])}"',
    )

    denied = _page(client, user, detail_url)
    for entry in entries:
        assert entry not in denied, entry
    # 出荷の知らせの行 (日と数) は権限に依らず出る。
    assert "2026-11-05" in denied

    buyer = account("buyer", permissions=["purchasing"])
    allowed = _page(client, buyer, detail_url)
    for entry in entries:
        assert entry in allowed, entry

    settings.SEIZOU = {}
    logged = _page(client, user, detail_url)
    for entry in entries:
        assert entry in logged, entry
    assert fake_post.calls == 0


# ---------------------------------------------------------------- 6 マニュアル


@pytest.mark.django_db
def test_manual_buttons_hidden_without_permission(settings, client, on, user):
    folder = manuals_services.create_folder(None, "作り物の置き場", "gray", user)
    manual = manuals_services.create_manual(folder, "作り物の手順", "steps", user)
    list_url = reverse("manuals:manual_list")
    folder_url = f"{list_url}?folder={folder.pk}"
    detail_url = reverse("manuals:manual_detail", args=[manual.pk])
    list_entries = (
        f'href="{reverse("manuals:manualfolder_create")}',
        f'href="{reverse("manuals:manual_create")}',
    )
    folder_entries = (
        'aria-label="フォルダの操作"',
        f'href="{reverse("manuals:manualfolder_update", args=[folder.pk])}"',
        f'href="{reverse("manuals:manualfolder_move", args=[folder.pk])}"',
        f'href="{reverse("manuals:manualfolder_protect", args=[folder.pk])}"',
        f'href="{reverse("manuals:manualfolder_delete", args=[folder.pk])}"',
    )
    detail_entries = (
        f'href="{reverse("manuals:manual_move", args=[manual.pk])}"',
        f'href="{reverse("manuals:manual_protect", args=[manual.pk])}"',
        f'href="{reverse("manuals:manual_delete", args=[manual.pk])}"',
    )
    # 見るだけの口 (PDF と版の履歴) は権限に依らず出る。
    detail_reads = (
        f'href="{reverse("manuals:manual_pdf", args=[manual.pk])}"',
        f'href="{reverse("manuals:manualversion_list", args=[manual.pk])}"',
    )

    def pages(person):
        return {
            "list": _page(client, person, list_url),
            "folder": _page(client, person, folder_url),
            "detail": _page(client, person, detail_url),
        }

    denied = pages(user)
    for entry in list_entries:
        assert entry not in denied["list"], entry
    for entry in folder_entries:
        assert entry not in denied["folder"], entry
    assert "作り物の置き場" in denied["folder"]
    for entry in detail_entries:
        assert entry not in denied["detail"], entry
    for entry in detail_reads:
        assert entry in denied["detail"], entry

    def assert_all_shown(shown):
        for entry in list_entries:
            assert entry in shown["list"], entry
        for entry in folder_entries:
            assert entry in shown["folder"], entry
        for entry in (*detail_entries, *detail_reads):
            assert entry in shown["detail"], entry

    assert_all_shown(pages(account("manual_editor", permissions=["manuals"])))

    settings.SEIZOU = {}
    assert_all_shown(pages(user))
