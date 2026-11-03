"""入口の釦の隠す形 (決め 11-04 00:0x「隠す側でそろえよう」。精査 A2 の S2)。

編集の判定が on のとき、種類の権限の無い人には押すと断られる釦を出さない (押したときの断りは今のまま)。
釦を出すかは core の tag can_write (core.access.can_write_model と同じ判定) と、印 seizou.hide_money だけで決める。
人は accounts の services で作る。名前は作り物。
"""

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.urls import resolve, reverse

from seizou.core import access
from seizou.core.templatetags import seizou_tags
from seizou.orders import services as orders_services
from seizou.orders.templatetags import orders_board
from tests import test_orders_board
from tests.test_core_access import account
from tests.test_orders_board import _issue, _line, _tr

User = get_user_model()

# 注残の盤の試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
# user = Access の行の無い普通の利用者 tester (精査 A2 と N76 の名前)。customer と item と工程の cut は作り物。
user = test_orders_board.user
customer = test_orders_board.customer
item = test_orders_board.item
cut = test_orders_board.cut

ALL_ON = {"department": "on", "write": "on", "money": "on"}

# 釦の action → tag に渡す model (その model の新規登録の action と同じ種類の権限で決まる)。
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
    """釦の action の種類が、tag に渡す model の新規登録の action の種類と同じ (tag の答え = 押したときの答え)。"""
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
    # ⋮ は空にならない (部品の小窓はいつも出る)。一覧に戻るは見るだけの口。
    assert "部品の小窓" in lines_html
    assert list_href in denied and "一覧に戻る" in denied

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


# ---------------------------------------------------------------- 3 指示書の詳細


@pytest.mark.django_db
def test_workorder_cancel_hidden_accept_kept(settings, client, on, user, customer, item, cut):
    workorder = _issue(user, _line(user, customer, item), cut, qty="4")
    detail_url = reverse("workorders:workorder_detail", args=[workorder.pk])
    cancel_href = f'href="{reverse("workorders:workorder_cancel", args=[workorder.pk])}"'
    accept_form = f'action="{reverse("workorders:workorder_accept", args=[workorder.pk])}"'

    denied = _page(client, user, detail_url)
    assert cancel_href not in denied
    # 「受けた」は現場の記録 (押した先は編集の権限を見ない) なので、権限の無い人にも出す。
    assert accept_form in denied and "受けた" in denied

    planner = account("planner", permissions=["workorders"])
    allowed = _page(client, planner, detail_url)
    assert cancel_href in allowed and accept_form in allowed

    settings.SEIZOU = {}
    assert cancel_href in _page(client, user, detail_url)
