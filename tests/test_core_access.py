"""権限の判定の 1 か所 (seizou.core.access。02 の 7 節)。3 つの判定 (部署・編集の権限・金額の閲覧) と 3 状態。

判定を有効にする試験は settings の fixture で SEIZOU を替える (tests/settings.py は SEIZOU = {} のまま = 3 つとも記録だけ)。
access_for は利用者の物に行を控えるので、行を作り直した後は User.objects.get で読み直す。
"""

import inspect
import logging
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from seizou.accounts import services as accounts_services
from seizou.core import access, conf
from seizou.core.views import BaseFormView
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.parts import PARTS

User = get_user_model()

PASSWORD = "kumo-tests-2611"
ALL_ON = {"department": "on", "write": "on", "money": "on"}
ALL_OFF = {"department": "off", "write": "off", "money": "off"}


@pytest.fixture
def on(settings):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}


@pytest.fixture
def off(settings):
    settings.SEIZOU = {"access_control": dict(ALL_OFF)}


@pytest.fixture
def plain(db):
    """Access の行の無い普通の利用者 (既にある試験の利用者と同じ形)。"""
    return User.objects.create_user("tester", password=PASSWORD)


@pytest.fixture
def seizou_dept(db):
    return accounts_services.create_department("製造", ["workorders", "progress", "masters"], ["workorders"], None)


@pytest.fixture
def sales_dept(db):
    return accounts_services.create_department("受注と出荷", ["orders", "shipping"], ["orders", "shipping"], None)


def account(login_id, *, departments=(), permissions=(), **flags):
    """アカウントを発行し、控えの無い利用者の物を返す。"""
    accounts_services.issue_account(login_id, PASSWORD, list(departments), list(permissions), flags, None)
    return User.objects.get(username=login_id)


def access_records(caplog):
    return [r for r in caplog.records if r.name == "seizou.access"]


def all_actions():
    return {*access.ACTION_KINDS, *access.ACCOUNT_ACTIONS, *access.MONEY_ACTIONS}


def app_names():
    return {"core", *(part.rsplit(".", 1)[-1] for part in PARTS)}


# ---------------------------------------------------------------- 状態


def test_mode_defaults_and_normalize(settings):
    settings.SEIZOU = {}
    assert [access.mode(kind) for kind in access.KINDS] == ["log", "log", "log"]

    settings.SEIZOU = {"access_control": {"department": True, "write": False, "money": "bogus"}}
    assert [access.mode(kind) for kind in access.KINDS] == ["on", "off", "log"]

    settings.SEIZOU = {"access_control": {"department": "on"}}
    assert [access.mode(kind) for kind in access.KINDS] == ["on", "log", "log"]

    settings.SEIZOU = {"access_control": "on"}
    assert [access.mode(kind) for kind in access.KINDS] == ["log", "log", "log"]

    with pytest.raises(KeyError):
        conf.access_control("unknown")
    with pytest.raises(KeyError):
        access.mode("unknown")


# ---------------------------------------------------------------- 制限を受けない人と、行の無い人


@pytest.mark.django_db
def test_unrestricted_pass_all(on, seizou_dept):
    admin = User.objects.create_superuser("admin", password=PASSWORD)
    full = account("zenken", departments=[seizou_dept], full_control=True)

    for user in (admin, full):
        for action in sorted(all_actions()):
            access.require_write(user, action)
        for app in sorted(app_names()):
            assert access.can_open(user, app), (user, app)
        assert access.can_see_money(user)
        assert access.can_manage_accounts(user)


@pytest.mark.django_db
def test_no_access_row_means_no_permission(on, plain):
    with pytest.raises(access.AccessDenied):
        access.require_write(plain, "shipping.ship")
    assert not access.can_see_money(plain)
    for app in sorted(app_names() - set(access.ACCOUNT_APPS)):
        assert access.can_open(plain, app), app
    assert not access.can_open(plain, "accounts")
    assert not access.can_manage_accounts(plain)


@pytest.mark.django_db
def test_buttons_hidden_without_write_permission(settings, client, on, plain):
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, plain)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, plain)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        plain,
        order_date=date(2026, 11, 2),
    )
    create_link = f'href="{reverse("workorders:workorder_create")}"'
    order_detail = reverse("orders:order_detail", args=[order.pk])
    edit_link = f'href="{reverse("orders:order_update", args=[order.pk])}?next={order_detail}"'
    workorder_list = reverse("workorders:workorder_list")

    def page(user, url):
        client.force_login(user)
        response = client.get(url)
        assert response.status_code == 200
        return response.content.decode()

    assert create_link not in page(plain, workorder_list)
    assert edit_link not in page(plain, order_detail)

    planner = account("planner", permissions=["workorders"])
    assert create_link in page(planner, workorder_list)
    assert edit_link not in page(planner, order_detail)

    full = account("zenken", full_control=True)
    assert create_link in page(full, workorder_list)
    assert edit_link in page(full, order_detail)

    # 記録だけでは権限が無くてもボタンを出す (断りは保存のときだけ)。
    settings.SEIZOU = {}
    assert create_link in page(plain, workorder_list)
    assert edit_link in page(plain, order_detail)


# ---------------------------------------------------------------- 編集の権限


@pytest.mark.django_db
def test_kinds_and_groups(on):
    shipper = account("shipper", permissions=["shipping"])
    access.require_write(shipper, "shipping.ship")
    with pytest.raises(access.AccessDenied):
        access.require_write(shipper, "masters.update_item")

    master = account("master", permissions=["master_edit"])
    access.require_write(master, "masters.update_item")
    access.require_write(master, "connect.register_connection")
    with pytest.raises(access.AccessDenied):
        access.require_write(master, "shipping.ship")

    slip = account("slip", permissions=["slip_edit"])
    access.require_write(slip, "shipping.ship")
    access.require_write(slip, "connect.send_order")
    with pytest.raises(access.AccessDenied):
        access.require_write(slip, "masters.update_item")

    assert access.expand(["slip_edit"]) == frozenset(access.GROUPS["slip_edit"][1])


@pytest.mark.django_db
def test_free_actions_need_nothing(on, plain):
    for action in ("progress.record_progress", "attachments.add_attachment", "attachments.remove_attachment"):
        assert action in access.FREE_ACTIONS
        access.require_write(plain, action)
    # 3 つの表のどれにも無い action は止めない。
    access.require_write(plain, "tests.unknown_action")


def _url_patterns(patterns):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _url_patterns(pattern.url_patterns)
        elif isinstance(pattern, URLPattern):
            yield pattern


def test_every_form_action_is_classified():
    known = {*access.ACTION_KINDS, *access.FREE_ACTIONS, *access.ACCOUNT_ACTIONS}
    seen = set()
    for pattern in _url_patterns(get_resolver().url_patterns):
        view_class = getattr(pattern.callback, "view_class", None)
        if view_class is None or not issubclass(view_class, BaseFormView):
            continue
        if getattr(view_class, "service", None) is None:
            continue
        action = view_class().get_action()
        seen.add(action)
        assert action in known, f"{pattern.name} の action {action} が権限の表に無い (core/access.py に足す)"
    # 表の名前が今の画面の action と同じ形であること (書き違いで黙って素通りしないため)。
    assert {"shipping.ship", "masters.update_item", "purchasing.resend_purchase_order", "workorders.issue_workorder"} <= seen


#: POST の関数の view (module の名前.関数の名前) → 頭で require_write に渡す権限の表の action
POST_FUNCTION_ACTIONS = {
    "seizou.manuals.views.image_upload": "manuals.add_image",
    "seizou.manuals.views.image_delete": "manuals.remove_image",
    "seizou.workorders.views.plan_place": "workorders.plan_step",
    "seizou.shipping.shipping_notice.resend_shipping_notice": "connect.send_shipping_notice",
    "seizou.purchasing.acceptance.resend_acceptance": "connect.send_acceptance",
    "seizou.connect.reply.resend_reply": "connect.accept",
    "seizou.connect.sender.choose_sender": "connect.accept",
    "seizou.notes.views.image_upload": "notes.add_image",
}
#: POST の関数の view のうち、権限の表の action を通さない口 → 理由
POST_FUNCTION_WITHOUT_ACTION = {
    "seizou.core.settings_screen.setting_clear": "設定の門 (settings_denied = アカウント管理の権限)",
    "seizou.connect.views.receive_order": "取引先の受け口 (ログインの外。署名の確かめ = R12)",
    "seizou.assistant.views.ask": "会話の控え (session) だけ",
    "seizou.assistant.views.clear": "会話の控え (session) だけ",
    "seizou.assistant.views.approve": "承認の中の assistant.services._carry_out が require_write を通す",
    "seizou.manuals.views.manual_preview": "表に書かない (本文を描くだけ)",
    "seizou.bookmarks.views.toggle": "自分のブックマークだけ",
    "seizou.feedback.views.confirm": "feedback の services が世話役だけに絞る",
    "seizou.feedback.views.sent": "feedback の services が世話役だけに絞る",
    "seizou.feedback.views.fixed": "feedback の services が世話役だけに絞る",
    "seizou.feedback.views.github_disconnect": "feedback の services が世話役だけに絞る (N185)",
    "seizou.feedback.views.github_send": "feedback の services が世話役だけに絞る (N185)",
    "seizou.feedback.views.github_sync": "feedback の services が世話役だけに絞る (N185)",
    "seizou.feedback.views.github_device_start": "feedback の services が世話役だけに絞る (N320)",
    "seizou.feedback.views.github_device_poll": "feedback の services が世話役だけに絞る (N320)",
    "seizou.feedback.views.github_device_cancel": "feedback の services が世話役だけに絞る (N320)",
    "seizou.feedback.views.delete": "feedback の services が書いた人と世話役に絞る (対象は一覧と同じ絞りの中から引く)",
    "seizou.feedback.views.ask": "会話の控え (session) だけ",
    "seizou.feedback.views.reset": "会話の控え (session) だけ",
}


def test_every_post_function_view_is_classified():
    # 関数の view (土台を継がない口) の POST は test_every_form_action_is_classified が見ないので、ここで分ける。
    known = {*access.ACTION_KINDS, *access.FREE_ACTIONS, *access.ACCOUNT_ACTIONS}
    seen = set()
    for pattern in _url_patterns(get_resolver().url_patterns):
        if getattr(pattern.callback, "view_class", None) is not None:
            continue
        function = inspect.unwrap(pattern.callback)
        source = inspect.getsource(function)
        if "@require_POST" not in source:
            continue
        name = f"{function.__module__}.{function.__name__}"
        seen.add(name)
        if name in POST_FUNCTION_ACTIONS:
            assert POST_FUNCTION_ACTIONS[name] in known, f"{name} の action が権限の表に無い (core/access.py に足す)"
            assert "require_write(" in source, f"{name} は require_write を呼ばない"
        else:
            assert name in POST_FUNCTION_WITHOUT_ACTION, f"{pattern.name} ({name}) の POST の口を分ける (この 2 つの表のどちらかに足す)"
    # 表の名前の書き違いで黙って素通りしないため。
    assert set(POST_FUNCTION_ACTIONS) | set(POST_FUNCTION_WITHOUT_ACTION) <= seen


# ---------------------------------------------------------------- 記録だけ・使わない


@pytest.mark.django_db
def test_log_mode_records_once_and_passes(settings, caplog, plain):
    settings.SEIZOU = {}
    caplog.set_level(logging.WARNING, logger="seizou.access")

    access.require_write(plain, "shipping.ship")
    access.require_write(plain, "shipping.ship")

    records = access_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    message = records[0].getMessage()
    assert message.startswith("記録だけ (編集の権限): tester が ")
    assert "shipping.ship" in message
    assert "出荷と納品書" in message

    # 金額も通して 1 行。
    assert access.can_see_money(plain)
    assert access.can_see_money(plain)
    assert len(access_records(caplog)) == 2


@pytest.mark.django_db
def test_off_mode_skips(off, caplog, seizou_dept):
    caplog.set_level(logging.WARNING, logger="seizou.access")
    genba = account("genba", departments=[seizou_dept])

    access.require_write(genba, "shipping.ship")
    access.require_write(genba, "billing.create_invoice")
    assert access.can_open(genba, "orders")
    assert access.can_see_money(genba)
    assert access_records(caplog) == []


# ---------------------------------------------------------------- 断りの文


@pytest.mark.django_db
def test_denied_message_names_kind_group_contact(on, plain):
    with pytest.raises(access.AccessDenied) as caught:
        access.require_write(plain, "shipping.ship")

    assert caught.value.code == "権限"
    message = caught.value.message
    assert "出荷と納品書" in message
    assert "伝票編集" in message
    assert "アカウント管理の担当者" in message

    with pytest.raises(access.AccessDenied) as accounts_caught:
        access.require_write(plain, "accounts.issue_account")
    assert accounts_caught.value.message.startswith("アカウント管理の権限がありません。")


@pytest.mark.django_db
def test_money_actions_refused_without_money(on):
    clerk = account("clerk", permissions=["billing", "orders"])
    with pytest.raises(access.AccessDenied) as invoice:
        access.require_write(clerk, "billing.create_invoice")
    assert "請求書は金額が主な内容" in invoice.value.message
    with pytest.raises(access.AccessDenied) as order:
        access.require_write(clerk, "orders.create_order")
    assert "受注は金額が主な内容" in order.value.message
    # 金額が主な登録でなければ通る。
    access.require_write(clerk, "orders.update_order")

    keiri = account("keiri", permissions=["billing", "orders"], see_money=True)
    access.require_write(keiri, "billing.create_invoice")
    access.require_write(keiri, "orders.create_order")


# ---------------------------------------------------------------- 部署とアカウント管理の門


@pytest.mark.django_db
def test_department_can_open(on, plain, seizou_dept, sales_dept):
    genba = account("genba", departments=[seizou_dept])
    assert not access.can_open(genba, "orders")
    assert access.can_open(genba, "workorders")
    for app in ("core", "assistant", "manuals", "attachments"):
        assert access.can_open(genba, app), app

    both = account("kenmu", departments=[seizou_dept, sales_dept])
    assert access.can_open(both, "orders")
    assert access.can_open(both, "workorders")
    assert not access.can_open(both, "billing")

    for app in sorted(app_names() - set(access.ACCOUNT_APPS)):
        assert access.can_open(plain, app), app


@pytest.mark.django_db
def test_account_gate_ignores_mode(off, plain):
    assert not access.can_open(plain, "accounts")

    manager = account("soumu", manage_accounts=True)
    assert access.can_open(manager, "accounts")
    access.require_write(manager, "accounts.issue_account")
    with pytest.raises(access.AccessDenied):
        access.require_write(plain, "accounts.issue_account")


@pytest.mark.django_db
def test_reissue_invoice_is_a_money_action(on):
    """請求書の作り直しは請求書を作る操作の別の入口なので、金額を見られない人は作れない (請求書を作るのと同じ扱い)。"""
    assert access.MONEY_ACTIONS["billing.reissue_invoice"] == access.MONEY_ACTIONS["billing.create_invoice"]
    assert access.ACTION_KINDS["billing.reissue_invoice"] == "billing"
    clerk = account("clerk", permissions=["billing"])
    with pytest.raises(access.AccessDenied) as caught:
        access.require_write(clerk, "billing.reissue_invoice")
    assert "請求書は金額が主な内容" in caught.value.message

    keiri = account("keiri", permissions=["billing"], see_money=True)
    access.require_write(keiri, "billing.reissue_invoice")
