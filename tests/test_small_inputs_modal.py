"""詳細と一覧から開く小さい入力の小窓と、保存の後の取り直し (決め 11-05 11:4x。N158・N158b)。

開く link は href と 1 字も違わない data-modal-form を持つ (seizou-modal.js がその URL を小窓に読む)。入力の土台
(BaseFormView) は小窓の見出し (HX-Target = seizou-modal-box) の要求に小窓の中身を返し、保存したら本文の無い 204 に
HX-Trigger の seizou:saved を付ける。確かめの画面 (取消) の小窓は黄の帯に文を出す。詳細は seizou:refresh で
見出しの帯と中身を取り直す。全体の画面の URL は今のまま (直に開いたとき・JS の無いとき・Ctrl か Cmd の押下の逃げ道)。
作り物の取引先と部品で、画面ごとに要る物だけを作る (試験ごとに表は空から)。services が返す断りと知らせの字は見ない。
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Callable

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import resolve, reverse
from django.utils import timezone

from seizou.accounts import services as accounts_services
from seizou.accounts.models import Department
from seizou.billing import services as billing_services
from seizou.billing.models import Invoice, Payment
from seizou.core import checks
from seizou.masters import services as masters_services
from seizou.masters.models import Holiday, Process
from seizou.orders import services as orders_services
from seizou.orders.models import Order
from seizou.orders.views import OrderCancelView
from seizou.progress.models import Progress
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import PurchaseOrder, Receipt
from seizou.purchasing.views import PurchaseOrderCancelView
from seizou.shipping import services as shipping_services
from seizou.shipping.models import DeliveryNote
from seizou.workorders import services as workorders_services
from seizou.workorders.models import WorkOrder

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
PASSWORD = "pass-for-tests-only"
ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
RECEIVED_ON = date(2026, 12, 25)
ALL_ON = {"department": "on", "write": "on", "money": "on"}
REASON = "作り物の取消の理由"
TOO_LONG = "あ" * 501  # 取消の理由の欄は 500 字まで (受注・発注・指示書の取消の form の max_length)

#: (b) の画面の名前 (N158・N158b。開く所は依頼書の棚卸しの表。実績の記録は指示書の詳細の工程のタブの「実績を入力」= C16)。名前ごとに開く画面・開く URL・POST の値・確かめの画面か、を引く dict を同じ並びで持つ。
SCREENS = ("order_cancel", "deliverynote_create", "deliverynote_cancel", "purchaseorder_cancel", "receipt_create",
           "invoice_cancel", "payment_create", "payment_cancel", "workorder_cancel", "progress_create",
           "receipt_list_create", "holiday_create", "process_create", "department_create")
#: 受入の一覧の「新規登録」は受入の新規登録 (receipt_create) と同じ画面なので、小窓の中身と保存は 12 画面で見る
FORMS = tuple(name for name in SCREENS if name != "receipt_list_create")
#: 欄の誤りを作れる画面 (入金の金額・受入の数量・実績の数・休日の日付・工程のコード・部署の名前が空、取消の理由が 501 字)。欄の無い取消 3 つと納品書作成は外
ERRORS = ("order_cancel", "purchaseorder_cancel", "receipt_create", "payment_create", "workorder_cancel", "progress_create",
          "holiday_create", "process_create", "department_create")
#: 全体の画面のまま開ける入口 (確かめの画面 6 つと入金の記録と実績の記録)
WHOLE = ("order_cancel", "deliverynote_cancel", "purchaseorder_cancel", "invoice_cancel", "payment_cancel",
         "workorder_cancel", "payment_create", "progress_create")
#: 取り直しの要素を持つ詳細 (<名> と、その詳細を開く画面の名前)
DETAILS = (("order", "order_cancel"), ("shipment", "deliverynote_create"), ("deliverynote", "deliverynote_cancel"),
           ("invoice", "invoice_cancel"), ("workorder", "workorder_cancel"), ("purchaseorder", "purchaseorder_cancel"))


@dataclass
class Case:
    """1 つの画面の作り物。url は要求に使う字 (& は生のまま)。HTML の中の字は link (& が &amp;)。"""

    opener: str
    url: str
    post: dict
    saved: Callable[[], bool]
    bad: dict | None = None
    confirm: bool = False
    text: str = ""
    moved: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def link(self) -> str:
        return self.url.replace("&", "&amp;")

    @property
    def screen_name(self) -> str:
        return resolve(self.url.split("?")[0]).func.view_class.screen_name


@dataclass
class World:
    client: object
    user: object
    settings: object


class NoHttp:
    def __call__(self, *args, **kwargs):
        raise AssertionError("つながりの無い作り物の取引先なのに HTTP を呼んだ")


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    monkeypatch.setattr("requests.post", NoHttp())


@pytest.fixture
def world(client, user, settings):
    client.force_login(user)
    return World(client, user, settings)


def text(response) -> str:
    return response.content.decode()


# ---------------------------------------------------------------- 作り物


def _item(user, part_no="T-100"):
    return masters_services.create_item({"part_no": part_no, "name": "作り物の軸", "unit": "個"}, user)


def _order(user):
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer", "closing_day": 20, "payment_month": "next", "payment_day": 25},
        user,
    )
    return orders_services.create_order(
        customer, [{"item": _item(user), "qty": Decimal("10"), "unit_price": Decimal("1500")}], user, order_date=ORDER_DATE
    )


def _shipment(user, order):
    return shipping_services.ship(order.lines.get(), Decimal("4"), SHIPPED_AT, user)


def _note(user, order):
    return shipping_services.create_delivery_note(order.customer, [_shipment(user, order)], user)


def _invoice(user):
    order = _order(user)
    note = _note(user, order)
    return billing_services.create_invoice(order.customer, list(note.shipments.all()), user, issued_on=ISSUED_ON)


def _purchase_order(user, part_no="T-100"):
    partner = masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)
    return purchasing_services.create_purchase_order(
        partner, [{"item": _item(user, part_no), "qty": Decimal("10"), "unit_price": Decimal("25")}], user, order_date=timezone.localdate()
    )


def _workorder(user):
    cut = Process.objects.create(code="CUT", name="作り物の切断", sort_order=1, use_in_plan=True)
    order = _order(user)
    return workorders_services.issue_workorder(
        order.lines.get(), Decimal("4"), [{"process": cut, "planned_qty": Decimal("4")}], user
    )


def _receipt_post(line):
    return {"line": line.pk, "received_on": timezone.localdate().isoformat(), "qty": "4", "note": ""}


def _status_is(model, pk, status):
    return lambda: model.objects.get(pk=pk).status == status


def _one_more(model):
    before = model.objects.count()
    return lambda: model.objects.count() == before + 1


# ---------------------------------------------------------------- 画面ごとの作り物 (SCREENS と同じ並び)


def _order_cancel(w):
    order = _order(w.user)
    return Case(
        opener=reverse("orders:order_detail", args=[order.pk]),
        url=reverse("orders:order_cancel", args=[order.pk]),
        post={"reason": REASON},
        bad={"reason": TOO_LONG},
        saved=_status_is(Order, order.pk, Order.STATUS_CANCELLED),
        confirm=True,
        text=OrderCancelView.screen_help,
    )


def _deliverynote_create(w):
    shipment = _shipment(w.user, _order(w.user))
    detail = reverse("shipping:shipment_detail", args=[shipment.pk])
    return Case(
        opener=detail,
        url=f'{reverse("shipping:deliverynote_create")}?shipments={shipment.pk}&next={detail}',
        post={"shipments": [shipment.pk]},
        saved=_one_more(DeliveryNote),
    )


def _deliverynote_cancel(w):
    note = _note(w.user, _order(w.user))
    return Case(
        opener=reverse("shipping:deliverynote_detail", args=[note.pk]),
        url=reverse("shipping:deliverynote_cancel", args=[note.pk]),
        post={},
        saved=_status_is(DeliveryNote, note.pk, DeliveryNote.STATUS_CANCELLED),
        confirm=True,
        text="この納品書を取り消します。この納品書の出荷は請求できなくなり、出荷を直してから新しい納品書にまとめ直せます。",
        moved=True,
    )


def _purchaseorder_cancel(w):
    po = _purchase_order(w.user)
    return Case(
        opener=reverse("purchasing:purchaseorder_detail", args=[po.pk]),
        url=reverse("purchasing:purchaseorder_cancel", args=[po.pk]),
        post={"reason": REASON},
        bad={"reason": TOO_LONG},
        saved=_status_is(PurchaseOrder, po.pk, PurchaseOrder.STATUS_CANCELLED),
        confirm=True,
        text=PurchaseOrderCancelView.screen_help,
    )


def _receipt_create(w):
    line = _purchase_order(w.user).lines.get()
    detail = reverse("purchasing:purchaseorder_detail", args=[line.purchase_order_id])
    return Case(
        opener=detail,
        url=f'{reverse("purchasing:receipt_create")}?line={line.pk}&next={detail}',
        post=_receipt_post(line),
        bad={**_receipt_post(line), "qty": ""},
        saved=_one_more(Receipt),
    )


def _invoice_cancel(w):
    invoice = _invoice(w.user)
    return Case(
        opener=reverse("billing:invoice_detail", args=[invoice.pk]),
        url=reverse("billing:invoice_cancel", args=[invoice.pk]),
        post={},
        saved=_status_is(Invoice, invoice.pk, Invoice.STATUS_CANCELLED),
        confirm=True,
        text="この請求書を取り消します。入っていた出荷は、また請求できます。請求書は消えずに状態が「取消」で残り、元には戻せません。",
        moved=True,
        extra={"invoice": invoice},
    )


def _payment_create(w):
    invoice = _invoice(w.user)
    detail = reverse("billing:invoice_detail", args=[invoice.pk])
    post = {"received_on": "2026-12-25", "amount": "6500", "method": "transfer", "note": "作り物の入金"}
    return Case(
        opener=detail,
        url=f'{reverse("billing:payment_create", args=[invoice.pk])}?next={detail}',
        post=post,
        bad={**post, "amount": ""},
        saved=_one_more(Payment),
        text=f'請求書 <span class="font-mono">{invoice.invoice_no}</span> (作り物の得意先)。',
        moved=True,
    )


def _payment_cancel(w):
    invoice = _invoice(w.user)
    payment = billing_services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), w.user)
    return Case(
        opener=reverse("billing:invoice_detail", args=[invoice.pk]),
        url=reverse("billing:payment_cancel", args=[payment.pk]),
        post={},
        saved=_status_is(Payment, payment.pk, Payment.STATUS_CANCELLED),
        confirm=True,
        text="を取り消します。取消の記録は履歴に残り、請求書の残の金額に戻ります。",
        moved=True,
    )


def _workorder_cancel(w):
    workorder = _workorder(w.user)
    return Case(
        opener=reverse("workorders:workorder_detail", args=[workorder.pk]),
        url=reverse("workorders:workorder_cancel", args=[workorder.pk]),
        post={"reason": REASON},
        bad={"reason": TOO_LONG},
        saved=_status_is(WorkOrder, workorder.pk, WorkOrder.STATUS_CANCELLED),
        confirm=True,
        text="この指示書を取り消します。実績の入った指示書は取り消せません。取消の指示書は生産計画と進捗の盤から外れます。",
        moved=True,
    )


def _progress_create(w):
    """指示書の詳細の工程のタブの「実績を入力」(C16)。工程の pk と戻る道を付けて、実績の新規登録を小窓で開く。"""
    step = _workorder(w.user).steps.get()
    detail = reverse("workorders:workorder_detail", args=[step.workorder_id])
    post = {"workorder_step": step.pk, "qty": "4", "recorded_at": "2026-11-06 10:00", "note": ""}
    return Case(
        opener=detail,
        url=f'{reverse("progress:progress_create")}?workorder_step={step.pk}&next={detail}',
        post=post,
        bad={**post, "qty": ""},
        saved=_one_more(Progress),
    )


def _receipt_list_create(w):
    line = _purchase_order(w.user).lines.get()
    return Case(
        opener=reverse("purchasing:receipt_list"),
        url=reverse("purchasing:receipt_create"),
        post=_receipt_post(line),
        saved=_one_more(Receipt),
    )


def _holiday_create(w):
    post = {"date": "2026-12-29", "name": "作り物の会社の休み", "kind": "company"}
    return Case(
        opener=reverse("masters:holiday_list"),
        url=reverse("masters:holiday_create"),
        post=post,
        bad={**post, "date": ""},
        saved=_one_more(Holiday),
    )


def _process_create(w):
    post = {"code": "MILL", "name": "作り物の工程", "sort_order": "20"}
    return Case(
        opener=reverse("masters:process_list"),
        url=reverse("masters:process_create"),
        post=post,
        bad={**post, "code": ""},
        saved=_one_more(Process),
    )


def _login_manager(w):
    """部署の画面は権限の判定が on の会社の、アカウント管理の担当者だけが開ける。"""
    w.settings.SEIZOU = {"access_control": dict(ALL_ON)}
    accounts_services.issue_account("soumu", PASSWORD, [], [], {"manage_accounts": True}, None)
    w.client.force_login(get_user_model().objects.get(username="soumu"))


def _department_create(w):
    _login_manager(w)
    post = {"name": "作り物の部署", "apps": [], "default_permissions": [], "sort_order": "0"}
    return Case(
        opener=reverse("accounts:department_list"),
        url=reverse("accounts:department_create"),
        post=post,
        bad={**post, "name": ""},
        saved=_one_more(Department),
    )


BUILD = {
    "order_cancel": _order_cancel,
    "deliverynote_create": _deliverynote_create,
    "deliverynote_cancel": _deliverynote_cancel,
    "purchaseorder_cancel": _purchaseorder_cancel,
    "receipt_create": _receipt_create,
    "invoice_cancel": _invoice_cancel,
    "payment_create": _payment_create,
    "payment_cancel": _payment_cancel,
    "workorder_cancel": _workorder_cancel,
    "progress_create": _progress_create,
    "receipt_list_create": _receipt_list_create,
    "holiday_create": _holiday_create,
    "process_create": _process_create,
    "department_create": _department_create,
}


def _tags(body, href):
    """href が href の字の a の開きの tag の並び。"""
    return re.findall(r'<a [^>]*href="' + re.escape(href) + r'"[^>]*>', body)


# ---------------------------------------------------------------- 試験


@pytest.mark.parametrize("name", SCREENS)
def test_openers_carry_modal_mark(world, name):
    assert tuple(BUILD) == SCREENS
    case = BUILD[name](world)
    body = text(world.client.get(case.opener))
    assert f'href="{case.link}" data-modal-form="{case.link}"' in body, name


@pytest.mark.parametrize("name", FORMS)
def test_modal_get_is_fragment(world, name):
    case = BUILD[name](world)
    response = world.client.get(case.url, **MODAL)
    body = text(response)
    assert response.status_code == 200
    assert "<html" not in body
    assert "hx-post=" in body
    assert case.screen_name in body
    if case.confirm:
        alert = body[body.index("alert-warning") :]
        assert case.text in alert[: alert.index("</div>")]
        assert "キャンセル" not in body


@pytest.mark.parametrize("name", FORMS)
def test_modal_post_saves_with_trigger(world, name):
    case = BUILD[name](world)
    response = world.client.post(case.url, case.post, **MODAL)
    assert response.status_code == 204, text(response)
    saved = json.loads(response["HX-Trigger"])["seizou:saved"]
    assert saved["level"] in ("success", "warning")
    assert case.saved()
    assert list(get_messages(response.wsgi_request)) == []


@pytest.mark.parametrize("name", ERRORS)
def test_modal_post_error_stays_in_modal(world, name):
    case = BUILD[name](world)
    response = world.client.post(case.url, case.bad, **MODAL)
    body = text(response)
    assert response.status_code == 200
    assert "<html" not in body
    assert "hx-post=" in body
    assert "text-error" in body
    assert not case.saved()


def test_progress_modal_preselects_step(world):
    """指示書の詳細から開いた実績の新規登録の小窓は、その工程を選んだ形 (QR の着地と同じ GET の値の道)。"""
    case = _progress_create(world)
    step = WorkOrder.objects.get().steps.get()
    match = re.search(r'<select name="workorder_step".*?</select>', text(world.client.get(case.url, **MODAL)), re.S)
    assert match, "指示書の工程の選ぶ欄が無い"
    assert f'<option value="{step.pk}" selected>' in match.group(0)
    assert match.group(0).count(" selected>") == 1


def test_invoice_cancel_refusal_in_modal(world):
    case = _invoice_cancel(world)
    invoice = case.extra["invoice"]
    billing_services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), world.user)
    back = f'href="{reverse("billing:invoice_detail", args=[invoice.pk])}#payments"'

    modal = text(world.client.get(case.url, **MODAL))
    assert "<html" not in modal
    assert "alert-warning" in modal
    assert '<form method="post"' not in modal
    assert back not in modal

    # 全体の画面は今のまま、詳細の入金の枠へ戻る link を出す
    whole = text(world.client.get(case.url))
    assert "<html" in whole
    assert back in whole


def test_deliverynote_cancel_refusal_in_modal(world):
    """請求済みの納品書の取消の小窓は、押す前に黄の断りと出荷・請求書の link を出し、form と取り消すボタンを出さない。

    全体の画面だけ「納品書へ戻る」を出す (小窓の下は納品書の詳細。初見テスト R5 の候補 C03。依頼書 N196)。
    """
    invoice = _invoice(world.user)
    note = DeliveryNote.objects.get()
    shipment = note.shipments.get()
    url = reverse("shipping:deliverynote_cancel", args=[note.pk])
    back = f'href="{reverse("shipping:deliverynote_detail", args=[note.pk])}"'

    modal = text(world.client.get(url, **MODAL))
    assert "<html" not in modal
    alert = modal[modal.index("alert-warning") :]
    assert shipping_services.cancel_refusal(note).partition("。")[2].strip() in alert[: alert.index("</div>")]
    assert '<form method="post"' not in modal
    assert f"{note.note_no} を取り消す</button>" not in modal
    assert "この納品書を取り消します。" not in modal
    # 請求書の詳細の href は閉じの " まで見ない (入金の枠への行き先が後で付いても通るように)
    assert f'href="{reverse("billing:invoice_detail", args=[invoice.pk])}' in modal
    assert f'href="{reverse("shipping:shipment_detail", args=[shipment.pk])}"' in modal
    assert back not in modal

    whole = text(world.client.get(url))
    assert "<html" in whole
    assert back in whole
    assert "納品書へ戻る" in whole
    assert DeliveryNote.objects.get().status == DeliveryNote.STATUS_ISSUED


@pytest.mark.parametrize(("name", "screen"), DETAILS)
def test_details_refresh_after_save(world, name, screen):
    case = BUILD[screen](world)
    body = text(world.client.get(case.opener))
    if name == "purchaseorder":
        # 発注の詳細は 2 秒ごとの取り直しの要素に seizou:refresh を足しただけ (取りに行く要素は 1 つ)
        assert re.search(r'id="purchaseorder-detail-poll"[^>]*hx-trigger="every 2s[^"]*, seizou:refresh from:body"', body)
        assert body.count('hx-select-oob="#po-heading,#po-alert,#detail-aside"') == 1
        return
    assert body.count(f'id="{name}-heading"') == 1
    assert body.count(f'id="{name}-live"') == 1
    refresh = re.search(rf'<div id="{name}-detail-refresh"[^>]*>', body)
    assert refresh, name
    tag = refresh.group(0)
    assert 'hx-trigger="seizou:refresh from:body"' in tag
    assert f'hx-select="#{name}-live"' in tag
    assert f'hx-select-oob="#{name}-heading"' in tag
    # 取り直しの要素は中身の外 (後ろ)。中に置くと 1 回目の取り直しで要素が消える
    live = body.index(f'id="{name}-live"')
    assert live < refresh.start()
    assert body[: refresh.start()].rstrip().endswith("</div>")
    if name == "order":
        for inner in ('id="order-stages"', 'id="detail-aside"', 'id="order-lines-title"'):
            assert live < body.index(inner) < refresh.start(), inner


@pytest.mark.parametrize("name", WHOLE)
def test_whole_pages_keep_working(world, name):
    case = BUILD[name](world)
    page = text(world.client.get(case.url))
    assert "<html" in page
    if name != "payment_cancel":
        # 入金の取消は入金の一覧の URL が無く、入金の枠の link は ?next= を付けないので、前から「キャンセル」を出さない (変えない)
        assert "キャンセル" in page
    if case.moved:
        assert case.text in page
    response = world.client.post(case.url, case.post)
    assert response.status_code == 302
    assert case.saved()


def test_large_inputs_stay_pages(world):
    order = _order(world.user)
    _purchase_order(world.user, "T-200")
    holiday = masters_services.create_holiday({"date": date(2026, 12, 29), "name": "作り物の会社の休み", "kind": "company"}, world.user)
    client = world.client

    for list_name, create_name in (
        ("orders:order_list", "orders:order_create"),
        ("purchasing:purchaseorder_list", "purchasing:purchaseorder_create"),
        ("masters:item_list", "masters:item_create"),
    ):
        tags = _tags(text(client.get(reverse(list_name))), reverse(create_name))
        assert tags, create_name
        assert all("data-modal-form" not in tag for tag in tags), create_name

    edit = _tags(
        text(client.get(reverse("orders:order_detail", args=[order.pk]))),
        f'{reverse("orders:order_update", args=[order.pk])}?next={reverse("orders:order_detail", args=[order.pk])}',
    )
    assert edit and all("data-modal-form" not in tag for tag in edit)
    delete = _tags(
        text(client.get(reverse("masters:holiday_detail", args=[holiday.pk]))), reverse("masters:holiday_delete", args=[holiday.pk])
    )
    assert delete and all("data-modal-form" not in tag for tag in delete)

    # 工程の一覧の「新規登録」は小窓で開く (core の一覧に create_in_modal を渡す形の見張り)
    create = reverse("masters:process_create")
    assert any(f'data-modal-form="{create}"' in tag for tag in _tags(text(client.get(reverse("masters:process_list"))), create))

    _login_manager(world)
    access = _tags(text(client.get(reverse("accounts:access_list"))), reverse("accounts:access_create"))
    assert access and all("data-modal-form" not in tag for tag in access)


# ---------------------------------------------------------------- 取消の確かめのカードの形 (初見テスト R5 の候補 C24。依頼書 N258)

#: 欄の格子の字 (格子の行の頭の mt-6 は必須の欄がある form だけに付く形に変わる見込み = 候補 C54 なので、見ない)
GRID = "grid gap-x-6"
#: 小窓の下の行 (欄がある form)・小窓の下の行 (見える欄が 0 個の form)・画面の全体の下の行 (C21)
MODAL_ROW = '<div class="mt-6 flex flex-wrap items-center justify-end gap-2 border-t border-base-300 pt-6">'
NO_FIELD_ROW = '<div class="flex flex-wrap items-center justify-end gap-2">'
SAVE_ROW = '<div class="seizou-save-row flex flex-wrap items-center justify-end gap-2">'
#: 見える欄が 0 個の 4 つの form (納品書の取消・請求書の取消・入金の取消・注文を送り直す)
NO_FIELDS = ("deliverynote_cancel", "invoice_cancel", "payment_cancel", "purchaseorder_send")


def _card(body):
    """入力のカードの form (data-enter-guard の付いた <form から </form> まで)。

    全体の画面では <main より前の上の帯にログアウトの <form method="post" があるので、最初の form を取らない。
    """
    mark = body.index("novalidate data-enter-guard>")
    start = body.rfind("<form", 0, mark)
    assert start >= 0, "カードの form が無い"
    assert "</form>" not in body[start:mark], "カードの form が無い"
    return body[start : body.index("</form>", mark) + len("</form>")]


def _url(w, name):
    """name の画面の URL (注文を送り直すは BUILD に無いので、ここで「作成」の発注を作る)。"""
    if name == "purchaseorder_send":
        return reverse("purchasing:purchaseorder_send", args=[_purchase_order(w.user).pk])
    return BUILD[name](w).url


@pytest.mark.parametrize("name", NO_FIELDS)
def test_no_field_cards_have_no_empty_band(world, name):
    """見える欄が 0 個の確かめは、白い枠に欄の格子を描かない。小窓では下の行の上の空きと線も描かない (U69・a-blind-15。C24 の 1)。

    画面の全体の下の行 (seizou-save-row) の上の空きと線は、外し方が決まっていないので見ない (候補の「決めを待つ所」)。
    """
    url = _url(world, name)
    modal = _card(text(world.client.get(url, **MODAL)))
    assert GRID not in modal
    assert "border-t" not in modal
    assert MODAL_ROW not in modal
    assert NO_FIELD_ROW in modal
    whole = _card(text(world.client.get(url)))
    assert GRID not in whole
    assert SAVE_ROW in whole


@pytest.mark.parametrize("name", ("order_cancel", "payment_create"))
def test_field_cards_keep_grid_and_rows(world, name):
    """欄がある form (受注の取消・入金の記録) は、格子と、小窓と画面の全体の下の行が今の形のまま (C24 の 1)。"""
    case = BUILD[name](world)
    modal = _card(text(world.client.get(case.url, **MODAL)))
    assert GRID in modal
    assert MODAL_ROW in modal
    assert NO_FIELD_ROW not in modal
    whole = _card(text(world.client.get(case.url)))
    assert GRID in whole
    assert SAVE_ROW in whole


#: 6 つの取消の確かめ (受注・指示書・発注・納品書・請求書・入金。どれも取り消せる記録で作る)
CANCELS = ("order_cancel", "workorder_cancel", "purchaseorder_cancel", "deliverynote_cancel", "invoice_cancel", "payment_cancel")
DANGER_BUTTON = '<button class="btn btn-error" type="submit"'
PRIMARY_BUTTON = '<button class="btn btn-primary" type="submit"'
WARNING_BUTTON = '<button class="btn btn-warning" type="submit">'


class _CancelNote(checks.Check):
    """作り物の知らせる検査 (受注の取消で、いつも黄の知らせ)。形は tests/test_core_save_bar.py の _HolidayNote を写した。"""

    name = "T93"
    action = "orders.cancel_order"
    mode = "warn"

    def run(self, data, user):
        return "作り物の知らせです。確かめてから取り消してください。"


@pytest.fixture
def cancel_checks():
    before = checks.registered(_CancelNote.action)
    yield
    checks.clear(_CancelNote.action)
    for check in before:
        checks.register(check)


@pytest.mark.parametrize("name", CANCELS)
def test_cancel_buttons_are_red(world, name):
    """取消の確かめの実行のボタンは、削除の確かめと同じ赤 (btn btn-error)。全体の画面と小窓の両方 (U125。C24 の 2)。"""
    url = BUILD[name](world).url
    for card in (_card(text(world.client.get(url))), _card(text(world.client.get(url, **MODAL)))):
        assert card.count(DANGER_BUTTON) == 1
        assert "取り消す</button>" in card
        assert not re.search(r'<button class="[^"]*\bbtn-primary\b[^"]*" type="submit"', card)


@pytest.mark.parametrize("name", ("payment_create", "purchaseorder_send"))
def test_other_buttons_keep_primary(world, name):
    """取消でない入力 (入金の記録・注文を送り直す) の保存のボタンは、主の色のまま (C24 の 2)。"""
    url = _url(world, name)
    for card in (_card(text(world.client.get(url))), _card(text(world.client.get(url, **MODAL)))):
        assert card.count(PRIMARY_BUTTON) == 1
        assert DANGER_BUTTON not in card


def test_warned_cancel_keeps_yellow_button(world, cancel_checks):
    """黄の知らせが出た後は、取消の確かめでも「このまま保存」の黄のボタン (赤にしない。C24 の 2)。"""
    checks.register(_CancelNote())
    case = BUILD["order_cancel"](world)
    for response in (world.client.post(case.url, case.post), world.client.post(case.url, case.post, **MODAL)):
        assert response.status_code == 200
        card = _card(text(response))
        assert card.count(WARNING_BUTTON) == 1
        assert "このまま保存</button>" in card
        assert DANGER_BUTTON not in card
        assert PRIMARY_BUTTON not in card
    assert not case.saved()
