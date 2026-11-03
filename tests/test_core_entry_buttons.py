"""入口の釦の隠す形 (決め 11-04 00:0x「隠す側でそろえよう」。精査 A2 の S2)。

編集の判定が on のとき、種類の権限の無い人には押すと断られる釦を出さない (押したときの断りは今のまま)。
釦を出すかは core の tag can_write (core.access.can_write_model と同じ判定) と、印 seizou.hide_money だけで決める。
人は accounts の services で作る。名前は作り物。
"""

from django.apps import apps
from django.urls import resolve, reverse

from seizou.core import access
from seizou.core.templatetags import seizou_tags
from seizou.orders.templatetags import orders_board

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
