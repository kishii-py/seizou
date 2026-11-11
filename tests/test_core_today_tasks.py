"""ダッシュボードの数字のカードの下の段「今日やること」の枠 (core.dashboard.today_tasks と home.html。本人の決め 11-05 09:4x で数字のカードの直下)。件数は作り物のデータを services で作って数える。
その人が進められない行 (書く action を編集の権限・金額の閲覧・部署で行えない) は出さない (GR-04。個人のタスクの枠 = 本人の決め 11-05 09:4x)。

Django の test client で HTML を見る (本物のブラウザは使わない)。発注はつながりの無い協力企業で作り、相手に送らない。
名前は作り物だけ。
"""

import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.accounts import demo_seed as accounts_demo_seed
from seizou.accounts import services as accounts_services
from seizou.accounts.models import Department
from seizou.billing import services as billing_services
from seizou.connect import services as connect_services
from seizou.connect.models import Inbox
from seizou.core import access, dashboard
from seizou.core.screens import all_screens
from seizou.core.tours import TOURS
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import PurchaseOrder
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services

ROOT = Path(__file__).resolve().parents[1]
FRAGMENTS = (
    ROOT / "src" / "seizou" / "core" / "manual_default" / "core_home.html",
    ROOT / "examples" / "companies" / "a_machining" / "manual" / "core_home.html",
    ROOT / "examples" / "companies" / "b_plating" / "manual" / "core_home.html",
)
# 流れの順 (受注から入金まで → 発注から検収まで) の行の key
ROW_ORDER = ["order", "workorder", "shipment", "delivery_note", "invoice", "payment", "purchase_draft", "purchase_sent", "receipt"]
# 行の key → 移る先の URL の名前と絞りの GET (納品書と請求書は、その出荷だけが並ぶ作る画面。決め 11-07 20:5x の 2。flow-02。
# 指示書の無い明細は、生産管理ボードを数える関数と同じ絞りで。nav-01。受入の残は合う絞りが無く素の一覧)
TARGETS = {
    "order": ("connect:inbox_list", "?f_status=received"),
    "workorder": ("orders:orderline_list", "?f_open=1&f_no_workorder=1"),
    "shipment": ("orders:order_list", ""),
    "delivery_note": ("shipping:deliverynote_create", ""),
    "invoice": ("billing:invoice_create", ""),
    "payment": ("billing:invoice_list", "?f_unpaid=1"),
    "purchase_draft": ("purchasing:purchaseorder_list", "?f_status=draft"),
    "purchase_sent": ("purchasing:purchaseorder_list", "?f_status=sent"),
    "receipt": ("purchasing:receipt_list", ""),
}
# 行の key → その行を進めるのに要る書く action (_task_targets の 4 つ目。core.access の ACTION_KINDS と同じ名前 = 保存のときの判定と同じ物。
# 下書きの発注は詳細の「再送」の画面の service。返事を待つ発注は数えるだけで操作が無い = None)
ACTIONS = {
    "order": "connect.accept",
    "workorder": "workorders.issue_workorder",
    "shipment": "shipping.ship",
    "delivery_note": "shipping.create_delivery_note",
    "invoice": "billing.create_invoice",
    "payment": "billing.record_payment",
    "purchase_draft": "purchasing.resend_purchase_order",
    "purchase_sent": None,
    "receipt": "purchasing.receive",
}
#: 部署・編集・金額の判定を全部 on にした会社
ALL_ON = {"department": "on", "write": "on", "money": "on"}
SECTION = re.compile(r'<section[^>]*aria-labelledby="flow-guide-title".*?</section>', re.S)
PASSWORD = "kumo-tests-2611"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {
            "code": "C001",
            "name": "作り物の得意先",
            "role": "customer",
            "closing_day": 20,
            "payment_month": "next",
            "payment_day": 25,
        },
        user,
    )


@pytest.fixture
def partner(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor", "rounding": "round"}, user
    )


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def make_order(user, customer, item, *, unit_price="100"):
    """今日の受注 (数量 10 の明細 1 行。納期は 3 日後)。"""
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal(unit_price), "due_date": timezone.localdate() + timedelta(days=3)}],
        user,
        order_date=timezone.localdate(),
    )


def make_work(user, customer, partner, item):
    """手を動かす物を作る: 受注 1 件 (指示書の無い明細・出荷の残り)、出荷して請求書まで出した受注 1 件 (入金の残り)、受信した注文 1 件、送っていない発注 1 件と送った発注 1 件。"""
    make_order(user, customer, item)
    billed = make_order(user, customer, item, unit_price="1500")
    shipment = shipping_services.ship(billed.lines.get(), Decimal("10"), timezone.localdate(), user)
    shipping_services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    billing_services.create_invoice(customer, [shipment], user, issued_on=timezone.localdate())
    Inbox.objects.create(message_id=uuid.uuid4(), partner=customer, kind="order", payload={})
    for send in (False, True):
        purchase_order = purchasing_services.create_purchase_order(
            partner,
            [{"item": item, "qty": Decimal("100"), "unit_price": Decimal("45")}],
            user,
            order_date=date(2026, 10, 1),
            send=False,
        )
        if send:
            purchasing_services.mark_sent(purchase_order, user)


def make_all_work(user, customer, partner, item):
    """make_work に、納品書に入っていない出荷・納品書に入って請求書に入っていない出荷・受入の残っている出荷の知らせ (送った発注の明細) を足し、9 行が全部出る形にする。"""
    make_work(user, customer, partner, item)
    shipping_services.ship(make_order(user, customer, item).lines.get(), Decimal("10"), timezone.localdate(), user)
    noted = shipping_services.ship(make_order(user, customer, item).lines.get(), Decimal("10"), timezone.localdate(), user)
    shipping_services.create_delivery_note(customer, [noted], user)
    sent = PurchaseOrder.objects.get(status=PurchaseOrder.STATUS_SENT)
    connect_services.record_shipping_notice(
        sent.lines.get(), Decimal("40"), timezone.localdate(), user, message_id=uuid.uuid4(), shipment_no="SH-0001"
    )


def keys_of(login_id):
    """ログイン ID の人の「今日やること」の行の key の並び (枠ごと出さないときは None)。利用者の物は控えを持たないよう取り直す。"""
    today = dashboard.today_tasks(get_user_model().objects.get(username=login_id))
    return None if today is None else [row["key"] for row in today["rows"]]


def flows_step(row_key):
    """行の key の FLOWS の段 (段の key か、数える物の名前が同じ段)。"""
    return next(step for flow in dashboard.FLOWS for step in flow["steps"] if row_key in (step["key"], step["count"]))


def home_section(client):
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    body = response.content.decode()
    return body, SECTION.search(body).group(0)


class _NoParts:
    """部品が 1 つも入っていない会社の apps の代わり。"""

    def get_model(self, app_label, name):
        raise LookupError(f"{app_label}.{name}")


@pytest.mark.django_db
def test_today_tasks_rows_follow_flow_order_and_targets(user, customer, partner, shaft):
    make_work(user, customer, partner, shaft)
    rows = dashboard.today_tasks()["rows"]
    keys = [row["key"] for row in rows]
    assert keys == ["order", "workorder", "shipment", "payment", "purchase_draft", "purchase_sent"]
    assert keys == [key for key in ROW_ORDER if key in keys]
    for row in rows:
        url_name, query = TARGETS[row["key"]]
        assert row["url_name"] == url_name, row["key"]
        assert row["url"] == reverse(url_name) + query, row["key"]
        step = flows_step(row["key"])
        assert row["label"] == step["count_label"], row["key"]
        assert row["unit"] == step["unit"], row["key"]
        assert row["count"] == 1, row["key"]


@pytest.mark.django_db
def test_today_tasks_buttons_are_screen_names(user, customer, partner, shaft):
    make_work(user, customer, partner, shaft)
    names = {screen.url_name: screen.name for screen in all_screens()}
    rows = dashboard.today_tasks()["rows"]
    assert rows
    for row in rows:
        assert row["button"] == names[row["url_name"]], row["key"]
    # 行にならなかった段 (納品書・請求書・受入) の移る先も、画面の名前のボタン
    for key, (button, url_name, _params, _action) in dashboard._task_targets().items():
        assert button == names[url_name], key
        assert url_name == TARGETS[key][0], key


@pytest.mark.django_db
def test_note_and_invoice_rows_open_the_screens_that_list_those_shipments(logged_in, user, customer, shaft):
    # 納品書に入っていない出荷の行は「納品書を作る」へ、請求書に入っていない出荷の行は「請求書の新規登録」へ移り、
    # どちらの画面にも数えた出荷だけが並ぶ (決め 11-07 20:5x の 2。flow-02。一覧へ移ると数えた物と違う物が並んでいた)。
    # 請求書に入っていない出荷は、納品書の無い出荷も数える (本人の決め 11-11 10:1x)。
    unnoted = shipping_services.ship(make_order(user, customer, shaft).lines.get(), Decimal("10"), timezone.localdate(), user)
    noted = shipping_services.ship(make_order(user, customer, shaft).lines.get(), Decimal("10"), timezone.localdate(), user)
    shipping_services.create_delivery_note(customer, [noted], user)

    rows = {row["key"]: row for row in dashboard.today_tasks(user)["rows"]}
    assert (rows["delivery_note"]["count"], rows["delivery_note"]["button"]) == (1, "納品書を作る")
    # 請求書の新規登録は納品書の無い出荷も数える (本人の決め 11-11 10:1x)。納品書を作るの件数は変えない。
    assert (rows["invoice"]["count"], rows["invoice"]["button"]) == (2, "請求書の新規登録")
    assert rows["delivery_note"]["url"] == reverse("shipping:deliverynote_create")
    assert rows["invoice"]["url"] == reverse("billing:invoice_create")

    note_screen = logged_in.get(rows["delivery_note"]["url"])
    assert note_screen.status_code == 200
    assert list(note_screen.context["form"].fields["shipments"].queryset) == [unnoted]
    invoice_screen = logged_in.get(rows["invoice"]["url"])
    assert invoice_screen.status_code == 200
    assert list(invoice_screen.context["form"].fields["shipments"].queryset) == [unnoted, noted]

    body, section = home_section(logged_in)
    assert f'href="{rows["delivery_note"]["url"]}">納品書を作る<' in section
    assert f'href="{rows["invoice"]["url"]}">請求書の新規登録<' in section
    assert "その物に絞った一覧が開きます" not in body  # 枠の中の説明の文は出さない (決め 11-03 14:3x)


@pytest.mark.django_db
def test_workorder_row_opens_the_board_narrowed_to_the_counted_lines(logged_in, user, customer, partner, shaft):
    # 「指示書の無い明細 n 行」の行は、生産管理ボードを数える関数と同じ絞り「指示書の無い明細だけ」で開き、同じ行が並ぶ (nav-01 の (b))
    make_work(user, customer, partner, shaft)
    issued = make_order(user, customer, shaft).lines.get()
    cut = masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)
    workorders_services.issue_workorder(issued, Decimal("10"), [{"process": cut, "planned_qty": Decimal("10")}], user)

    row = next(row for row in dashboard.today_tasks(user)["rows"] if row["key"] == "workorder")
    assert row["count"] == 1 == dashboard._lines_without_workorder().count()
    response = logged_in.get(row["url"])
    assert response.status_code == 200
    listed = [item["obj"] for item in response.context["rows"]]
    assert listed == list(dashboard._lines_without_workorder())
    assert issued not in listed
    assert 'name="f_no_workorder" value="1" class="checkbox checkbox-sm" checked data-list-filter>指示書の無い明細だけ</label>' in response.content.decode()


@pytest.mark.django_db
def test_today_tasks_hides_zero_and_none(monkeypatch):
    today = dashboard.today_tasks()
    assert today["title"] == "今日やること"
    assert today["rows"] == []
    assert today["guide"] == dashboard.flow_guide()
    monkeypatch.setattr(dashboard, "apps", _NoParts())
    assert dashboard.today_tasks() is None


@pytest.mark.django_db
def test_today_tasks_hides_rows_of_closed_departments(settings, user, customer, partner, shaft):
    settings.SEIZOU = {"access_control": {"department": "on"}}
    make_work(user, customer, partner, shaft)
    department = accounts_services.create_department("作り物の受注と出荷の部署", ["orders", "shipping"], ["orders"], None)
    accounts_services.issue_account("juchu", PASSWORD, [department], [], {}, None)
    clerk = get_user_model().objects.get(username="juchu")

    rows = dashboard.today_tasks(clerk)["rows"]
    # 受信した注文・請求書・発注と受入の行は移る先の部品を部署で開けない。指示書の行は、移る先の盤 (受注の部品) は開けても、
    # 発行の入口のある指示書の部品を部署で開けないので出ない (GR-04)。出荷の行 (出荷の部品を開ける) だけが残る
    assert [row["key"] for row in rows] == ["shipment"]
    assert {row["url_name"].split(":")[0] for row in rows} == {"orders"}

    everyone = {row["url_name"].split(":")[0] for row in dashboard.today_tasks()["rows"]}
    assert {"connect", "billing", "purchasing", "orders"} <= everyone


def test_task_targets_hold_the_action_of_each_row():
    # 行ごとの書く action は core.access の表にある名前 (保存のときの判定と同じ名前)。返事を待つ発注は数えるだけで操作が無い
    actions = {key: action for key, (_button, _url_name, _params, action) in dashboard._task_targets().items()}
    assert actions == ACTIONS
    assert all(action in access.ACTION_KINDS for action in actions.values() if action is not None)
    # 金額に関わる操作の行 (決め 4 = 11-07 21:0x。受け入れ・納品書・請求書・入金) = 金額の閲覧の無い人には出ない行
    assert {key for key, action in actions.items() if action in access.MONEY_ACTIONS} == {"order", "delivery_note", "invoice", "payment"}


@pytest.mark.django_db
def test_today_tasks_hides_rows_the_person_cannot_proceed(settings, user, customer, partner, shaft):
    # GR-04: 押して移っても操作のボタンが出ない行を出さない (個人のタスクの枠 = 本人の決め 11-05 09:4x)。
    # 人は作り物の会社の種 (accounts の demo_seed) の 3 人 = 現場 genba (製造。権限なし・金額なし)・事務 jimu (受注と出荷。伝票編集・金額なし)・
    # 経理 keiri (経理。請求書・金額あり) と、フルコントロールの人、jimu と同じ部署と権限で金額を見られる人
    settings.SEIZOU = {"access_control": dict(ALL_ON)}
    make_all_work(user, customer, partner, shaft)
    assert [row["key"] for row in dashboard.today_tasks()["rows"]] == ROW_ORDER
    accounts_demo_seed.load(user, PASSWORD)
    accounts_services.issue_account("kanri", PASSWORD, [], [], {"full_control": True}, None)
    sales = Department.objects.get(name="受注と出荷")
    accounts_services.issue_account("hanbai-kin", PASSWORD, [sales], ["slip_edit"], {"see_money": True}, None)

    assert keys_of("kanri") == ROW_ORDER
    # 事務 (金額なし): 受信した注文の受け入れと納品書を作るは金額に関わる操作 (入口が出ない)、指示書の発行は部署で開けない部品 (伝票編集の権限はある)、
    # 請求書と入金は部署の外。返事を待つ発注は数えるだけの行なので残る
    assert keys_of("jimu") == ["shipment", "purchase_draft", "purchase_sent", "receipt"]
    # 同じ部署と権限で金額を見られる人には、金額に関わる操作の 2 行が出る (指示書は部署のまま出ない)
    assert keys_of("hanbai-kin") == ["order", "shipment", "delivery_note", "purchase_draft", "purchase_sent", "receipt"]
    # 経理: 移る先の盤と受注の一覧は開けるが、指示書と出荷の編集の権限が無い。進められるのは請求書と入金だけ
    assert keys_of("keiri") == ["invoice", "payment"]
    # 現場: 製造の部署は行の移る先をどれも開けない (枠ごと出さない = tests/test_core_home_closed_links と同じ)
    assert keys_of("genba") is None


@pytest.mark.django_db
def test_today_tasks_keeps_rows_when_judgements_only_log(user, customer, partner, shaft):
    # 判定が log (既定) の会社では行を外さない (write_allowed と can_open は on のときだけ偽になる)
    make_all_work(user, customer, partner, shaft)
    accounts_demo_seed.load(user, PASSWORD)
    for login_id in ("genba", "jimu", "keiri"):
        assert keys_of(login_id) == ROW_ORDER, login_id


@pytest.mark.django_db
def test_today_tasks_adds_no_query(monkeypatch):
    calls = []
    real = dashboard.flow_guide

    def counted():
        calls.append(1)
        return real()

    monkeypatch.setattr(dashboard, "flow_guide", counted)
    with CaptureQueriesContext(connection) as captured:
        today = dashboard.today_tasks()
    counted_steps = [step for flow in today["guide"]["flows"] for step in flow["steps"] if step["count"] is not None]
    assert len(counted_steps) == 9
    assert len(captured.captured_queries) == len(counted_steps)
    assert calls == [1]


@pytest.mark.django_db
def test_home_shows_today_after_cards_with_noun_buttons(logged_in, user, customer, shaft):
    make_order(user, customer, shaft)
    body, section = home_section(logged_in)
    # 並びは 数字のカード (会社の KPI) → 今日やること (個人のタスク) → 出荷と生産の図 (本人の決め 11-05 09:4x)。
    # 今日やることの右に納期アラート (同じ段。本人の決め 11-05 11:0x)
    stats = body.index('data-tour="stats"')
    frame = body.index('aria-labelledby="flow-guide-title"')
    alerts = body.index('aria-labelledby="due-alerts-title"')
    shipping = body.index('aria-labelledby="shipping-title"')
    assert stats < frame < alerts < shipping
    # 題は h2 の 1 回 (行の並びの aria-label にも同じ字が入るので、見える字で数える)
    assert section.count('<h2 id="flow-guide-title" class="text-lg font-bold">今日やること</h2>') == 1
    assert "はじめての方へ" not in section
    assert "btn-primary" not in section
    assert "次にすること:" not in section
    board_url = reverse("orders:orderline_list")
    assert f'href="{board_url}?f_open=1&amp;f_no_workorder=1">生産管理ボード<' in section


@pytest.mark.django_db
def test_home_shows_flow_picture_when_nothing_to_do(logged_in):
    body, section = home_section(logged_in)
    assert section.count("はじめての方へ") == 1
    assert "今日やること" not in section
    # 部品も取引先も無い会社: 「最初にすること」の段 (ONB-02) の下に、2 つの流れの段 (tests/test_core_dashboard_first_steps.py)
    assert re.findall(r'<ol class="mt-2 flex flex-wrap items-center gap-2" aria-label="([^"]+)"', section) == [
        "最初にすることの段",
        "受注から入金までの段",
        "発注から検収までの段",
    ]
    assert "badge" not in section


def test_tour_second_step_is_today():
    steps = TOURS["core:home"]
    assert steps[0]["selector"] == '[data-tour="stats"]'
    assert steps[1]["selector"] == '[data-tour="flow-guide"]'
    assert steps[1]["title"] == dashboard.TODAY_TITLE
    # 3 段目は今日やることの右の納期アラート、4 段目はその下の出荷と生産の図 (画面の並びの順。本人の決め 11-05 11:0x)
    assert steps[2]["selector"] == 'section[aria-labelledby="due-alerts-title"]'
    assert steps[3]["selector"] == 'section[aria-labelledby="shipping-title"]'
    assert len(steps) == 5


def test_manual_fragments_and_readme_say_today():
    texts = [path.read_text(encoding="utf-8") for path in FRAGMENTS]
    assert texts[0] == texts[1] == texts[2]
    assert "今日やること" in texts[0]
    # README の「業務の流れ」は docs/screens.md に分けた (2026-11-10)
    readme = (ROOT / "docs" / "screens.md").read_text(encoding="utf-8")
    assert "ダッシュボードの数字のカードの下の枠「今日やること」には" in readme
    assert "ダッシュボードの最初の枠「今日やること」には" not in readme
    assert "数字のカードの下の「今日やること」に" in texts[0]
    assert "一番上の「今日やること」に" not in texts[0]
    assert "ダッシュボードの「はじめての方へ」の枠には" not in readme
