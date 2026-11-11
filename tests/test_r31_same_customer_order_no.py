"""R31: 同じ得意先に、得意先の注文番号が同じ受注が既にあったら (標準のポカよけ。会社の設定 same_customer_order_no。本人の決め 11-11 12:5x)。

同じ注文書を 2 人が入れる・同じ PDF を 2 回読み取りに置く・手で入れた注文が後から受信した注文でも届く間違いに、保存の前に気づかせる。
1 枚の注文書の部品を受注に分けて入れる会社では同じ番号が正しいので、既定は知らせる (止めるか通すかは会社が選ぶ)。
受注の新規登録と編集の画面・MCP の道具 create_order・読み取りの受け入れ・受信した注文の受け入れ 2 つを見る。
見ない物 (別の得意先・番号の空・取消の受注・番号も得意先も変えない編集) は、同じ番号の受注を置いたまま、見ないことだけで黙ることを確かめる。
作り物のデータは services で作る。名前と番号は作り物だけ。
"""

import json
import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.connect import signing
from seizou.connect.models import Inbox
from seizou.core import checks
from seizou.core.exceptions import CheckViolation
from seizou.core.mcp_tools import write_tools
from seizou.intake import services as intake_services
from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import Order
from tests.rules_helpers import rules_md_text
from tests.test_connect_signing import _new_secret
from tests.test_intake_extract import refuse_api, reply_data, upload
from tests.test_r10_inbox_accept import _line, _payload

ORDER_DATE = date(2026, 11, 2)
CREATE = "orders.create_order"
UPDATE = "orders.update_order"
EXTRACTED = "intake.accept_extracted"
INBOX = "connect.accept"
INBOX_CONDITIONS = "connect.accept_with_conditions"
ENV_NAME = "SEIZOU_R31_TEST_SECRET"


def _warn(number, orders):
    return (
        f"得意先の注文番号 {number} は、同じ得意先の受注 {orders} にもあります。"
        "同じ注文書を 2 回入れていないか確かめてください。1 枚の注文書を受注に分けて入れたのなら、このまま保存できます。"
    )


def _stop(number, orders):
    return (
        f"保存できません。得意先の注文番号 {number} は、同じ得意先の受注 {orders} にもあります。"
        "同じ注文書を 2 回入れていないか、既にある受注を確かめてください。"
    )


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    """得意先 A。"""
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先 A", "role": "customer"}, user)


@pytest.fixture
def other_customer(user):
    """得意先 B。"""
    return masters_services.create_partner({"code": "C002", "name": "作り物の得意先 B", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _order(customer, item, user, number):
    return services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
        customer_order_no=number,
    )


@pytest.fixture
def existing(customer, item, user):
    """得意先 A の受注 (得意先の注文番号 PO-100)。"""
    return _order(customer, item, user, "PO-100")


def _create_post(customer, item, number, **extra):
    data = {
        "customer": customer.pk,
        "order_date": ORDER_DATE.isoformat(),
        "customer_order_no": number,
        "lines-TOTAL_FORMS": "1",
        "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
        "lines-0-item": item.pk,
        "lines-0-qty": "5",
        "lines-0-unit": "",
        "lines-0-unit_price": "100",
    }
    data.update(extra)
    return data


def _update_post(order, **changes):
    """明細 1 行の受注を直す POST の値 (changes の欄だけを替える。None の欄は送らない)。"""
    line = order.lines.get()
    data = {
        "customer": order.customer_id,
        "order_date": order.order_date.isoformat(),
        "customer_order_no": order.customer_order_no,
        "note": order.note,
        "lines-TOTAL_FORMS": "1",
        "lines-INITIAL_FORMS": "1",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
        "lines-0-order_line": line.pk,
        "lines-0-item": line.item_id,
        "lines-0-qty": str(line.qty),
        "lines-0-unit": line.unit_code,
        "lines-0-unit_price": str(line.unit_price),
        "lines-0-due_date": "",
        "lines-0-note": "",
    }
    data.update(changes)
    return {key: value for key, value in data.items() if value is not None}


def _warning_messages(response):
    return [warning.message for warning in response.context["warnings"]]


def _check_box(body):
    """黄色の知らせ (data-notice="check") の箱の字。"""
    match = re.search(r'<div[^>]*data-notice="check">.*?</div>\s*<button', body, re.S)
    assert match, "黄色の知らせが無い"
    return match.group(0)


def _assert_confirm_form(response, message):
    """画面の形: 黄色の知らせの中に文があり、保存のボタンの字が「このまま保存」で、hidden の confirmed がある。"""
    body = response.content.decode()
    box = _check_box(body)
    first, rest = message.split("。", 1)
    assert first + "。" in box and rest in box and "R31" in box
    assert re.search(r'<button class="btn btn-warning" type="submit">.*?このまま保存</button>', body, re.S)
    assert '<input type="hidden" name="confirmed" value="1">' in body


# ---------------------------------------------------------------- 決まりの行と設定の鍵 (j)


def test_r31_row_in_rules_md_and_setting_key():
    assert "得意先の注文番号が同じ受注" in rules_md_text("R31")
    assert checks.setting_key_for("R31") == "same_customer_order_no"
    actions = {CREATE, UPDATE, EXTRACTED, INBOX, INBOX_CONDITIONS}
    found = {check.action for action in actions for check in checks.registered(action) if check.name == "R31"}
    assert found == actions


@pytest.mark.django_db
def test_r31_setting_on_settings_screen(logged_in):
    body = logged_in.get(reverse("core:settings")).content.decode()
    assert "same_customer_order_no" in body
    assert "同じ得意先の注文番号の受注 (R31)" in body


# ---------------------------------------------------------------- 照合の字 (d)


def test_r31_key_folds_width_case_and_spaces():
    key = services.customer_order_no_key
    assert key("ＰＯ－１００") == key(" po-100 ") == key("PO-100") == "po-100"
    assert key("PO-0100") != key("PO-100")
    assert key(None) == key("") == key("　 ") == ""


@pytest.mark.django_db
def test_r31_same_customer_order_no_reads_once(customer, other_customer, item, user, django_assert_num_queries):
    first = _order(customer, item, user, "PO-100")
    second = _order(customer, item, user, " po-100 ")
    _order(customer, item, user, "PO-0100")
    _order(other_customer, item, user, "PO-100")
    cancelled = _order(customer, item, user, "PO-100")
    services.cancel_order(cancelled, user)
    _order(customer, item, user, "")

    with django_assert_num_queries(1):
        assert services.same_customer_order_no(customer, "ＰＯ－１００") == [first.order_no, second.order_no]
    with django_assert_num_queries(1):
        assert services.same_customer_order_no(customer, "PO-100", exclude_pk=first.pk) == [second.order_no]
    with django_assert_num_queries(0):
        assert services.same_customer_order_no(customer, "  ") == []


# ---------------------------------------------------------------- (a) 受注の新規登録の画面で知らせる


@pytest.mark.django_db
def test_r31_warns_on_create_screen_then_saves(settings, logged_in, customer, item, existing):
    settings.SEIZOU = {}
    url = reverse("orders:order_create")
    first = logged_in.post(url, _create_post(customer, item, "PO-100"))

    assert first.status_code == 200
    message = _warn("PO-100", existing.order_no)
    assert _warning_messages(first) == [message]
    _assert_confirm_form(first, message)
    assert Order.objects.count() == 1

    second = logged_in.post(url, _create_post(customer, item, "PO-100", confirmed="1"))

    assert second.status_code == 302
    assert Order.objects.count() == 2


# ---------------------------------------------------------------- (b) 止める・通す


@pytest.mark.django_db
def test_r31_stop_refuses_on_create_screen(settings, logged_in, customer, item, existing):
    settings.SEIZOU = {"same_customer_order_no": "stop"}
    response = logged_in.post(reverse("orders:order_create"), _create_post(customer, item, "PO-100", confirmed="1"))

    assert response.status_code == 200
    notice = response.context["notice"]
    assert notice.code == "R31"
    assert notice.message == _stop("PO-100", existing.order_no)
    assert "このまま保存できます" not in notice.message
    assert Order.objects.count() == 1


@pytest.mark.django_db
def test_r31_off_saves_silently(settings, logged_in, customer, item, existing):
    settings.SEIZOU = {"same_customer_order_no": "off"}
    response = logged_in.post(reverse("orders:order_create"), _create_post(customer, item, "PO-100"))

    assert response.status_code == 302
    assert Order.objects.count() == 2


# ---------------------------------------------------------------- (c) 見ない物


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["warn", "stop"])
def test_r31_skips_other_customer_blank_and_cancelled(settings, user, customer, other_customer, item, existing, mode):
    """得意先 A の PO-100 を置いたまま、別の得意先・空の番号では黙る。取消の受注だけが持つ番号でも黙る。"""
    settings.SEIZOU = {"same_customer_order_no": mode}
    assert checks.run(CREATE, {"customer": other_customer, "customer_order_no": "PO-100"}, user) == []
    assert checks.run(CREATE, {"customer": customer, "customer_order_no": ""}, user) == []
    assert checks.run(CREATE, {"customer": customer, "customer_order_no": "   "}, user) == []

    only_cancelled = _order(other_customer, item, user, "PO-200")
    services.cancel_order(only_cancelled, user)
    assert checks.run(CREATE, {"customer": other_customer, "customer_order_no": "PO-200"}, user) == []
    # 取消でない受注を足すと見る (取消の受注を見ないことだけで黙っていた)
    live = _order(other_customer, item, user, "PO-200")
    if mode == "stop":
        with pytest.raises(CheckViolation):
            checks.run(CREATE, {"customer": other_customer, "customer_order_no": "PO-200"}, user)
    else:
        warnings = checks.run(CREATE, {"customer": other_customer, "customer_order_no": "PO-200"}, user)
        assert [warning.message for warning in warnings] == [_warn("PO-200", live.order_no)]


# ---------------------------------------------------------------- (d) 照合の字で知らせる


@pytest.mark.django_db
def test_r31_matches_by_folded_text(settings, user, customer, existing):
    settings.SEIZOU = {}
    wide = checks.run(CREATE, {"customer": customer, "customer_order_no": "ＰＯ－１００"}, user)
    assert [(w.code, w.message) for w in wide] == [("R31", _warn("ＰＯ－１００", existing.order_no))]
    spaced = checks.run(CREATE, {"customer": customer, "customer_order_no": " po-100 "}, user)
    assert [w.message for w in spaced] == [_warn("po-100", existing.order_no)]
    assert checks.run(CREATE, {"customer": customer, "customer_order_no": "PO-0100"}, user) == []


# ---------------------------------------------------------------- (e) 並び


@pytest.mark.django_db
def test_r31_lists_three_orders_and_counts_the_rest(settings, user, customer, item, existing):
    settings.SEIZOU = {}
    others = [_order(customer, item, user, "PO-100") for _ in range(3)]
    numbers = sorted([existing.order_no, *[order.order_no for order in others]])
    warnings = checks.run(CREATE, {"customer": customer, "customer_order_no": "PO-100"}, user)
    assert [w.message for w in warnings] == [_warn("PO-100", "、".join(numbers[:3]) + " ほか 1 件")]


# ---------------------------------------------------------------- (f) 編集


@pytest.mark.django_db
def test_r31_update_without_change_is_silent(settings, logged_in, customer, item, user, existing):
    """同じ番号の受注 (existing) を置いたまま、番号も得意先も変えない保存では出ない (備考だけ直す)。"""
    settings.SEIZOU = {}
    twin = _order(customer, item, user, "PO-100")
    response = logged_in.post(reverse("orders:order_update", args=[twin.pk]), _update_post(twin, note="作り物の備考"))

    assert response.status_code == 302
    twin.refresh_from_db()
    assert twin.note == "作り物の備考"


@pytest.mark.django_db
def test_r31_update_number_to_existing_warns(settings, logged_in, customer, item, user, existing):
    settings.SEIZOU = {}
    order = _order(customer, item, user, "PO-200")
    url = reverse("orders:order_update", args=[order.pk])
    first = logged_in.post(url, _update_post(order, customer_order_no="PO-100"))

    assert first.status_code == 200
    message = _warn("PO-100", existing.order_no)
    assert _warning_messages(first) == [message]
    _assert_confirm_form(first, message)
    order.refresh_from_db()
    assert order.customer_order_no == "PO-200"

    second = logged_in.post(url, _update_post(order, customer_order_no="PO-100", confirmed="1"))
    assert second.status_code == 302
    order.refresh_from_db()
    assert order.customer_order_no == "PO-100"


@pytest.mark.django_db
def test_r31_update_customer_to_one_with_same_number_warns(settings, logged_in, customer, other_customer, item, user, existing):
    settings.SEIZOU = {}
    order = _order(other_customer, item, user, "PO-100")
    response = logged_in.post(reverse("orders:order_update", args=[order.pk]), _update_post(order, customer=customer.pk))

    assert response.status_code == 200
    assert _warning_messages(response) == [_warn("PO-100", existing.order_no)]
    order.refresh_from_db()
    assert order.customer == other_customer


@pytest.mark.django_db
def test_r31_update_with_hidden_number_field_is_silent(settings, logged_in, customer, item, user, existing):
    """番号の欄を外した会社 (hidden_fields) では、番号の欄の無い編集で出ない (同じ番号の受注 existing を置いたまま)。"""
    settings.SEIZOU = {"hidden_fields": ["customer_order_no"]}
    twin = _order(customer, item, user, "PO-100")
    response = logged_in.post(
        reverse("orders:order_update", args=[twin.pk]), _update_post(twin, customer_order_no=None, note="作り物の備考")
    )

    assert response.status_code == 302
    twin.refresh_from_db()
    assert (twin.customer_order_no, twin.note) == ("PO-100", "作り物の備考")


@pytest.mark.django_db
def test_r31_update_cancelled_order_shows_only_r05(settings, logged_in, customer, item, user, existing):
    settings.SEIZOU = {}
    order = _order(customer, item, user, "PO-200")
    services.cancel_order(order, user)
    order.refresh_from_db()
    data = {"customer": customer, "customer_order_no": "PO-100", "object": order}
    assert checks.run(UPDATE, data, user) == []

    response = logged_in.post(reverse("orders:order_update", args=[order.pk]), _update_post(order, customer_order_no="PO-100"))
    assert response.status_code == 200
    assert response.context["warnings"] == []
    assert response.context["notice"].code == "R05"


# ---------------------------------------------------------------- (g) MCP の道具


@pytest.mark.django_db
def test_r31_mcp_create_order_needs_confirm(settings, user, customer, item, existing):
    settings.SEIZOU = {}
    create_order = {tool.__name__: tool for tool in write_tools(user)}["create_order"]
    lines = [{"part_no": item.part_no, "qty": "5", "unit_price": "100"}]

    first = create_order(customer.code, lines, ORDER_DATE.isoformat(), "PO-100")
    assert first["ok"] is False
    assert first["warnings"] == [{"code": "R31", "message": _warn("PO-100", existing.order_no)}]
    assert Order.objects.count() == 1

    second = create_order(customer.code, lines, ORDER_DATE.isoformat(), "PO-100", confirm=True)
    assert second["ok"] is True
    assert Order.objects.count() == 2


# ---------------------------------------------------------------- (h) 読み取りの受け入れ


@pytest.fixture
def extraction(monkeypatch, customer, user):
    """得意先 A の読み取り (読み取った中身の order_no が PO-100)。"""
    reply = reply_data()
    reply["order_no"] = "PO-100"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-for-r31-tests-not-real")
    monkeypatch.setattr(intake_services, "_ask_claude", lambda model, block: json.dumps(reply, ensure_ascii=False))
    record = intake_services.extract_order(upload(), customer, user)
    monkeypatch.setattr(intake_services, "_ask_claude", refuse_api)
    return record


def _mapping(user):
    shaft = masters_services.create_item({"part_no": "S-100", "name": "作り物のシャフト", "unit": "個"}, user)
    bracket = masters_services.create_item({"part_no": "B-200", "name": "作り物のブラケット", "unit": "個", "standard_price": "240"}, user)
    return {"item_1": shaft.pk, "item_2": bracket.pk}


@pytest.mark.django_db
def test_r31_warns_on_extraction_accept_then_saves(settings, logged_in, user, existing, extraction):
    settings.SEIZOU = {}
    url = reverse("intake:extraction_accept", args=[extraction.pk])
    data = _mapping(user)
    first = logged_in.post(url, data)

    assert first.status_code == 200
    message = _warn("PO-100", existing.order_no)
    assert _warning_messages(first) == [message]
    _assert_confirm_form(first, message)
    assert Order.objects.count() == 1

    second = logged_in.post(url, {**data, "confirmed": "1"})
    assert second.status_code == 302
    assert Order.objects.count() == 2
    extraction.refresh_from_db()
    assert extraction.status == extraction.STATUS_ORDERED
    # 受注にした読み取りでは R31 の文を返さない (services の断りだけが出る)
    assert checks.run(EXTRACTED, {"object": extraction}, user) == []


@pytest.mark.django_db
def test_r31_skips_extraction_without_customer(settings, user, existing, extraction):
    settings.SEIZOU = {}
    assert [w.code for w in checks.run(EXTRACTED, {"object": extraction}, user)] == ["R31"]
    extraction.customer = None
    assert checks.run(EXTRACTED, {"object": extraction}, user) == []


# ---------------------------------------------------------------- (i) 受信した注文の受け入れ


@pytest.fixture
def secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(ENV_NAME, value)
    return value


@pytest.fixture
def receive_order(customer, user, secret):
    """得意先 A から届いた受信した注文 (order_no は PO-100)。"""
    connect_services.register_connection(customer, "", ENV_NAME, "receive", user)

    def _receive():
        msg_id = str(uuid.uuid4())
        payload = _payload(msg_id, [_line(1, "A-100")])
        payload["order_no"] = "PO-100"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        result = connect_services.receive(body, signing.headers_for(secret, msg_id, body))
        assert isinstance(result, Inbox)
        return result

    return _receive


@pytest.fixture
def own_item(user):
    return masters_services.create_item({"part_no": "B-100", "name": "作り物の受ける側の軸", "unit": "個"}, user)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name, extra",
    [
        ("connect:inbox_accept", {}),
        ("connect:inbox_accept_conditional", {"line_1_unit_price": "250", "line_1_qty": "10", "line_1_due_date": "2026-11-20"}),
    ],
)
def test_r31_warns_on_inbox_accept_then_saves(settings, logged_in, existing, receive_order, own_item, name, extra):
    settings.SEIZOU = {}
    inbox = receive_order()
    url = reverse(name, args=[inbox.pk])
    data = {"item_1": own_item.pk, **extra}
    first = logged_in.post(url, data)

    assert first.status_code == 200
    message = _warn("PO-100", existing.order_no)
    assert _warning_messages(first) == [message]
    _assert_confirm_form(first, message)
    assert Order.objects.count() == 1

    second = logged_in.post(url, {**data, "confirmed": "1"})
    assert second.status_code == 302
    assert Order.objects.count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize("action", [INBOX, INBOX_CONDITIONS])
def test_r31_skips_inbox_rows_services_refuse(settings, user, existing, receive_order, own_item, action):
    """変更と取消の版の行・取引先の空の行・受け入れ済みの行では R31 の文を返さない (同じ番号の受注 existing を置いたまま)。"""
    settings.SEIZOU = {}
    inbox = receive_order()
    assert [w.code for w in checks.run(action, {"object": inbox}, user)] == ["R31"]

    revision = Inbox(partner=inbox.partner, payload=inbox.payload, status=Inbox.STATUS_RECEIVED, revises=inbox)
    assert checks.run(action, {"object": revision}, user) == []

    no_sender = Inbox(partner=None, payload=inbox.payload, status=Inbox.STATUS_RECEIVED)
    assert checks.run(action, {"object": no_sender}, user) == []

    connect_services.accept(inbox, {"A-100": own_item}, user, send=False)
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_ACCEPTED
    assert checks.run(action, {"object": inbox}, user) == []
