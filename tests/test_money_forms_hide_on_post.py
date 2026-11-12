"""金額の閲覧の無い人が、金額に関わる操作の入力の画面へ直に送った時の描き直しに、金額と単価が出ないことの総当たり (依頼書 N436 の試験 1)。

背景 (本人の決め 2 つ):
- 11-12 07:2x: 編集の権限か金額の閲覧が無くて保存できない人には、入力の画面を開いた時点で 403 の画面「この画面は開けません」を出す
  (入力の土台 core.views.forms.BaseFormView.get の門)。直に送った時 (POST) は今のまま描き直す (欄が正しければ form_valid の頭の
  access.require_write が赤の知らせで断り、欄に誤りがあれば form_invalid が欄の誤りで描き直す)。
- 11-12 14:0x: 金額を隠して描く作り (form の hide_money と、テンプレートの seizou.hide_money) は消さずに残す。開く道からは通らなく
  なったが、直に送って断られた時と欄の誤りの描き直しで、金額を見せない守りとして働く。この試験は、その守りを入力ごとに確かめる。

見る物: 金額の判定だけが on (部署と編集は off) の会社で、金額の閲覧の無い人が、金額に関わる操作 (core.access.MONEY_ACTIONS) の
入力の画面の全部 (URL の一覧から並べる) に空の中身で直に送る。応答が 200 か 403 で、本文に、金額の項目の入力の欄
(<input>・<select>・<textarea> の name の末尾が core.access.MONEY_FIELDS の名前) と、作り物の記録の単価と金額の字が無い。
「残の全額を入れる」「残を消す金額を入れる」のボタン (data-fill-amount) も無い。
字の並びが正しいこと (試験が空振りしないこと) は、金額の閲覧のある人が同じ画面を開いた時に、同じ字のどれかが出ることで見る
(出ない画面は MONEY_NOT_ON_SCREEN に訳を書く)。

作り物の金額は、ほかの数と紛れない値にする: 単価 7,331・数量 3 → 金額 21,993。入金 4,987・調整 −1,129・提案の単価 7,129。
記録は services で作る (既にある試験の helper を import する。そのファイルは変えない)。会社・人・品番は作り物。
"""

import json
import re
import uuid
from datetime import date
from decimal import Decimal
from functools import cached_property

import pytest
from django.contrib.auth import get_user_model
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from seizou.billing import services as billing_services
from seizou.connect import services as connect_services
from seizou.connect.models import Inbox, ReceivedSelfInvoice
from seizou.core import access
from seizou.core.views import BaseFormView
from seizou.intake import services as intake_services
from seizou.masters import services as masters_services
from seizou.masters.models import ItemPrice
from seizou.orders import services as orders_services
from seizou.purchasing import services as purchasing_services
from seizou.shipping import services as shipping_services
from tests.billing_helpers import shipped
from tests.test_connect_reply import ORDER_ENV, REPLY_ENV, _connect, _into_receive
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret
from tests.test_core_access import account
from tests.test_intake_extract import FAKE_KEY, refuse_api, reply_data, upload
from tests.test_r10_inbox_accept import _line, _payload

User = get_user_model()

#: 金額の閲覧だけを効かせる (部署と編集の権限は見ない。tests/test_connect_answers.py の MONEY_ON と同じ)
MONEY_ON = {"department": "off", "write": "off", "money": "on"}

PRICE = Decimal("7331")
QTY = Decimal("3")
AMOUNT = PRICE * QTY  # 21,993
PAYMENT = Decimal("4987")
ADJUSTMENT = Decimal("-1129")
PROPOSED_PRICE = Decimal("7129")
ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
RECEIVED_ON = date(2026, 12, 25)
ADJUSTED_ON = date(2026, 12, 26)
DUE_DATE = date(2026, 11, 20)
#: 締めの一括の画面の今日 (2026-11-20 締めの後)
CLOSING_TODAY = date(2026, 11, 25)

#: 金額の閲覧の無い人の描き直しにも出てよい、金額の項目の欄 (URL の名前 → 欄の名前)。
#: 訳: 入金の記録・請求書の外で受け取った入金の記録・調整の記録の金額 (amount) は、使う人が自分で入れる字なので隠さない
#: (billing.forms.PaymentForm の docstring の決め。調整も同じ形)。この 3 つの画面で見るのは、頭の 1 行の 合計・入金済み・調整・残 の字と、
#: 「残の全額を入れる」「残を消す金額を入れる」のボタンが出ないこと。出所 = 依頼書 N436 の追記 (本体。11-12 15:4x)。
#: 発注の新規登録の明細の単価 (unit_price。formset の lines-0-unit_price と、行を足す雛形の lines-__prefix__-unit_price) も同じ扱いにする。
#: 訳: 協力企業への単価は利用者が打つ欄で (purchasing.forms の頭の docstring の作者の決め)、金額の閲覧の無い人の描き直しでも
#: 記録の単価は出ない (単価表から選ぶボタンは purchasing/_purchaseorder_card.html の seizou.hide_money で隠れる)。依頼書の追記の 3 つの外なので、
#: 隠すかは本体に渡す (記録 docs/nights/20261112/N436.md の迷った事 1)。外すときは、この行を消すと試験 1 と 2 が落ちる。
TYPED_MONEY_FIELDS = {
    "billing:payment_create": {"amount"},
    "billing:shipment_payment_create": {"amount"},
    "billing:adjustment_create": {"amount"},
    "purchasing:purchaseorder_create": {"unit_price"},
}

#: 金額の閲覧のある人が開いても、作り物の単価と金額の字が出ない画面 (URL の名前 → 訳)。この画面では、字の無いことは空振りでないと示せない
#: (欄の名前は見る)。数を固定する (下の test_lists_are_fixed)。
MONEY_NOT_ON_SCREEN: dict[str, str] = {
    "billing:invoice_cancel": "取消の確かめ。請求書の番号と黄の 1 行だけで、金額を出さない。",
    "billing:invoice_reissue": "作り直しの確かめ。出荷の表は出荷番号・出荷日・品番・数量だけで、金額を出さない (billing/_invoice_reissue_card.html の注)。",
    "masters:itemprice_add": "空の単価の行の入力。今ある単価の行を出さない。",
    "masters:itemprice_delete": "消す確かめ。単価の行の名前 (品番・共通・売り) だけで、単価を出さない。",
    "orders:order_cancel": "取消の確かめ。受注の番号と取消の理由の欄だけ。",
    "orders:order_create": "空の受注の入力。記録の単価と金額を出す所が無い (明細の単価は人が入れる)。",
    "orders:orderline_no_bill": "印を付ける確かめ。明細の番号だけ。",
    "orders:orderline_no_bill_clear": "印を外す確かめ。明細の番号だけ。",
    "purchasing:purchaseorder_cancel": "取消の確かめ。発注の番号と取消の理由の欄だけ。",
    "purchasing:purchaseorder_create": "空の発注の入力。注残の盤から来た行 (?from_line=) も単価は空で開く (協力企業への単価は利用者が打つ。purchasing.forms の注)。",
    "purchasing:selfinvoice_cancel": "取消の確かめ。仕入明細の番号だけ。",
    "shipping:deliverynote_cancel": "取消の確かめ。納品書の番号だけ。",
    "shipping:deliverynote_create": "出荷の表は出荷番号・出荷日・得意先・受注番号・部品・数量だけで、金額を出さない。",
}

#: 記録を作れなかった画面・送る形が特別な画面 (URL の名前 → 訳)。数を固定する。
NOT_COVERED: dict[str, str] = {}


def _url_patterns(patterns, namespace=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            inner = f"{namespace}{pattern.namespace}:" if pattern.namespace else namespace
            yield from _url_patterns(pattern.url_patterns, inner)
        elif isinstance(pattern, URLPattern):
            yield namespace, pattern


def money_form_screens() -> dict[str, str]:
    """金額に関わる操作の入力の画面 (BaseFormView の子で、get_action() が MONEY_ACTIONS にある物) の {URL の名前: action}。"""
    screens = {}
    for namespace, pattern in _url_patterns(get_resolver().url_patterns):
        view_class = getattr(pattern.callback, "view_class", None)
        if view_class is None or not issubclass(view_class, BaseFormView) or getattr(view_class, "service", None) is None:
            continue
        action = view_class().get_action()
        if action in access.MONEY_ACTIONS:
            screens[f"{namespace}{pattern.name}"] = action
    return screens


# ---------------------------------------------------------------- 本文の見方

_FIELD = re.compile(r"<(?:input|select|textarea)\b[^>]*?\bname=\"([^\"]+)\"", re.S)


def is_money_field_name(name: str) -> bool:
    """欄の名前の末尾が金額の項目か。formset の lines-0-unit_price・受信した注文の line_1_unit_price・一式の lump-5-amount の形も見る。"""
    last = re.split(r"__|-", name)[-1]
    return access.is_money_name(last) or any(last.endswith(f"_{field}") for field in access.MONEY_FIELDS)


def money_field_names(body: str, allowed=frozenset()) -> list[str]:
    """本文の入力の欄のうち、金額の項目の名前 (名前の末尾が allowed の名前の欄は除く)。"""
    return [
        name
        for name in _FIELD.findall(body)
        if is_money_field_name(name) and re.split(r"__|-", name)[-1] not in allowed
    ]


def money_texts(values) -> set[str]:
    """金額の値から、画面に出うる字 (区切りあり・なし。絶対値) を作る。1,000 より小さい値は、ほかの数と紛れるので入れない。"""
    texts = set()
    for value in values:
        if value is None:
            continue
        number = abs(Decimal(value))
        if number < 1000:
            continue
        if number == number.to_integral_value():
            whole = int(number)
            texts.update({f"{whole:,}", str(whole)})
        else:
            texts.update({f"{number:,.2f}", f"{number:.2f}", f"{number.normalize():f}"})
    return texts


def found_texts(body: str, texts) -> list[str]:
    """本文に出た字 (前後が英数字でない所だけ。CSRF の字や静的ファイルの印の中の数字は数えない)。"""
    return sorted(text for text in texts if re.search(rf"(?<![0-9A-Za-z]){re.escape(text)}(?![0-9A-Za-z])", body))


def record_money(*records) -> list:
    """記録と、その明細 (lines) の金額の項目の値を集める。"""
    values = []
    for record in records:
        rows = [record]
        lines = getattr(record, "lines", None)
        if lines is not None and hasattr(lines, "all"):
            rows += list(lines.all())
        for row in rows:
            for name in access.MONEY_FIELDS:
                value = getattr(row, name, None)
                if isinstance(value, Decimal):
                    values.append(value)
    return values


# ---------------------------------------------------------------- 作り物の記録


class World:
    """画面ごとの記録を、要る物だけ services で作る (cached_property。1 つの試験の中で 1 回)。"""

    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch
        self.owner = User.objects.create_superuser("owner", password="pass-for-tests-only")

    def freeze_today(self):
        """締めの一括の「年月」の選択肢 (今月とその前の 11 か月) に 2026-11 が入るよう、今日を CLOSING_TODAY にする
        (tests/test_billing_closing_views.py の today と同じ差し替え)。"""
        self.monkeypatch.setattr("seizou.billing.forms.timezone.localdate", lambda *args, **kwargs: CLOSING_TODAY)
        self.monkeypatch.setattr("seizou.billing.services.timezone.localdate", lambda *args, **kwargs: CLOSING_TODAY)

    # --- マスター ---
    @cached_property
    def customer(self):
        return masters_services.create_partner(
            {"code": "C701", "name": "作り物の得意先", "role": "customer", "closing_day": 20, "payment_month": "next", "payment_day": 25},
            self.owner,
        )

    @cached_property
    def item(self):
        return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, self.owner)

    @cached_property
    def item_price(self):
        """部品の売りの単価の行 (単価 7,331)。"""
        return masters_services.add_item_price({"item": self.item, "kind": ItemPrice.KIND_SALE, "unit_price": PRICE}, self.owner)

    # --- 受注と出荷 ---
    def new_order(self):
        return orders_services.create_order(
            self.customer, [{"item": self.item, "qty": QTY, "unit_price": PRICE}], self.owner, order_date=ORDER_DATE
        )

    @cached_property
    def order(self):
        self.item_price  # 受注の画面の単価表の候補にも 7,331 を置く
        return self.new_order()

    @cached_property
    def no_bill_line(self):
        line = self.new_order().lines.get()
        return orders_services.mark_no_bill(line, self.owner)

    @cached_property
    def noted_shipment(self):
        """納品書の出た出荷 (まだ請求書に入っていない)。"""
        return shipped(self.owner, self.customer, self.new_order().lines.get(), str(QTY), SHIPPED_AT)

    @cached_property
    def bare_shipment(self):
        """納品書の無い出荷。"""
        return shipping_services.ship(self.new_order().lines.get(), QTY, SHIPPED_AT, self.owner)

    # --- 請求 ---
    @cached_property
    def invoice(self):
        return billing_services.create_invoice(self.customer, [self.noted_shipment], self.owner, issued_on=ISSUED_ON)

    @cached_property
    def payment(self):
        return billing_services.record_payment(self.invoice, RECEIVED_ON, PAYMENT, self.owner)

    @cached_property
    def adjustment(self):
        self.payment
        return billing_services.record_adjustment(self.invoice, ADJUSTED_ON, ADJUSTMENT, self.owner)

    # --- 受信した注文と読み取り ---
    @cached_property
    def inbox(self):
        payload = _payload(str(uuid.uuid4()), [_line(1, "X-100", qty="3.00", unit_price="7331.00")])
        return connect_services.record_inbox(self.customer, payload, self.owner)

    @cached_property
    def extraction(self):
        data = reply_data()
        data["lines"][0].update({"qty": "3", "unit_price": "7331"})
        text = json.dumps(data, ensure_ascii=False)
        self.monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
        self.monkeypatch.setattr(intake_services, "_ask_claude", lambda model, block: text)
        try:
            return intake_services.extract_order(upload(), self.customer, self.owner)
        finally:
            self.monkeypatch.setattr(intake_services, "_ask_claude", refuse_api)

    # --- 2 社の役 (発注と仕入明細。tests/test_connect_self_invoice_reply.py の組み方) ---
    @cached_property
    def connected(self):
        """2 社の役のつながりを置き、相手への HTTP を受け口に回す。返す物は (協力企業, 発注元の役の得意先)。"""
        self.monkeypatch.setenv(ORDER_ENV, _new_secret())
        self.monkeypatch.setenv(REPLY_ENV, _new_secret())
        subcontractor = masters_services.create_partner(
            {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, self.owner
        )
        buyer = masters_services.create_partner({"code": "C001", "name": "作り物の発注元", "role": "customer"}, self.owner)
        _connect(self.owner, subcontractor, buyer)
        self.monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
        return subcontractor, buyer

    @property
    def subcontractor(self):
        return self.connected[0]

    @cached_property
    def purchase_order(self):
        """相手へ送った発注 (単価 7,331・数量 3)。相手の役の受信した注文もできる。"""
        return purchasing_services.create_purchase_order(
            self.subcontractor,
            [{"item": self.item, "qty": QTY, "unit_price": PRICE, "due_date": DUE_DATE}],
            self.owner,
            order_date=ORDER_DATE,
        )

    @cached_property
    def receipt(self):
        """相手の役が受け入れた発注の受入 (数量 3 → 金額 21,993)。"""
        po = self.purchase_order
        own = masters_services.create_item({"part_no": "B-200", "name": "作り物の受ける側の部品", "unit": "個"}, self.owner)
        connect_services.accept(Inbox.objects.get(source=Inbox.SOURCE_DIRECT, partner__code="C001"), {"T-100": own}, self.owner)
        return purchasing_services.receive(po.lines.get(), QTY, SHIPPED_AT, self.owner)

    @cached_property
    def self_invoice(self):
        return purchasing_services.create_self_invoice(self.subcontractor, [self.receipt], self.owner, issued_on=ISSUED_ON)

    @cached_property
    def received_self_invoice(self):
        self.self_invoice
        return ReceivedSelfInvoice.objects.get()

    @cached_property
    def proposed_self_invoice(self):
        """相手の役が明細 1 の単価を 7,129 に直す提案で答えた仕入明細。"""
        connect_services.answer_self_invoice(
            self.received_self_invoice, {1: {"answer": "propose", "qty": QTY, "unit_price": PROPOSED_PRICE}}, self.owner
        )
        self.self_invoice.refresh_from_db()
        return self.self_invoice


def _base():
    return [PRICE, AMOUNT]


#: 画面 (URL の名前) → 記録を作る helper。返す物は (URL, 本文に出てはいけない金額の値の並び)。
MAKERS = {
    "masters:itemprice_add": lambda w: (reverse("masters:itemprice_add") + f"?item={w.item_price.item_id}", _base()),
    "masters:itemprice_update": lambda w: (reverse("masters:itemprice_update", args=[w.item_price.pk]), _base()),
    "masters:itemprice_delete": lambda w: (reverse("masters:itemprice_delete", args=[w.item_price.pk]), _base()),
    "orders:order_create": lambda w: (reverse("orders:order_create") + f"?item={w.order.lines.get().item_id}", _base()),
    "orders:order_update": lambda w: (reverse("orders:order_update", args=[w.order.pk]), _base() + record_money(w.order)),
    "orders:order_cancel": lambda w: (reverse("orders:order_cancel", args=[w.order.pk]), _base() + record_money(w.order)),
    "orders:orderline_no_bill": lambda w: (reverse("orders:orderline_no_bill", args=[w.order.lines.get().pk]), _base()),
    "orders:orderline_no_bill_clear": lambda w: (reverse("orders:orderline_no_bill_clear", args=[w.no_bill_line.pk]), _base()),
    "connect:inbox_accept": lambda w: (reverse("connect:inbox_accept", args=[w.inbox.pk]), _base()),
    "connect:inbox_accept_conditional": lambda w: (reverse("connect:inbox_accept_conditional", args=[w.inbox.pk]), _base()),
    "connect:receivedselfinvoice_answer": lambda w: (
        reverse("connect:receivedselfinvoice_answer", args=[w.received_self_invoice.pk]),
        _base() + record_money(w.received_self_invoice),
    ),
    "shipping:deliverynote_create": lambda w: (reverse("shipping:deliverynote_create") + f"?customer={w.bare_shipment.order_line.order.customer_id}", _base()),
    "shipping:deliverynote_cancel": lambda w: (reverse("shipping:deliverynote_cancel", args=[w.noted_shipment.delivery_note_id]), _base()),
    "purchasing:purchaseorder_create": lambda w: (reverse("purchasing:purchaseorder_create") + f"?from_line={w.order.lines.get().pk}", _base()),
    "purchasing:purchaseorder_cancel": lambda w: (
        reverse("purchasing:purchaseorder_cancel", args=[w.purchase_order.pk]),
        _base() + record_money(w.purchase_order),
    ),
    "purchasing:purchaseorder_change": lambda w: (
        reverse("purchasing:purchaseorder_change", args=[w.purchase_order.pk]),
        _base() + record_money(w.purchase_order),
    ),
    "purchasing:selfinvoice_create": lambda w: (
        reverse("purchasing:selfinvoice_create") + f"?partner={w.receipt.line.purchase_order.partner_id}",
        _base() + record_money(w.receipt),
    ),
    "purchasing:selfinvoice_cancel": lambda w: (
        reverse("purchasing:selfinvoice_cancel", args=[w.self_invoice.pk]),
        _base() + record_money(w.self_invoice),
    ),
    "purchasing:selfinvoice_accept": lambda w: (
        reverse("purchasing:selfinvoice_accept", args=[w.proposed_self_invoice.pk]),
        _base() + [PROPOSED_PRICE, PROPOSED_PRICE * QTY] + record_money(w.proposed_self_invoice),
    ),
    "billing:invoice_create": lambda w: (_made(w.noted_shipment) and reverse("billing:invoice_create"), _base()),
    "billing:invoice_closing": lambda w: (
        _made(w.noted_shipment, w.freeze_today()) and reverse("billing:invoice_closing") + "?month=2026-11&closing_day=20",
        _base(),
    ),
    "billing:invoice_cancel": lambda w: (reverse("billing:invoice_cancel", args=[w.invoice.pk]), _base() + record_money(w.invoice)),
    "billing:invoice_reissue": lambda w: (reverse("billing:invoice_reissue", args=[w.invoice.pk]), _base() + record_money(w.invoice)),
    "billing:payment_create": lambda w: (reverse("billing:payment_create", args=[w.invoice.pk]), _base() + record_money(w.invoice)),
    "billing:shipment_payment_create": lambda w: (
        reverse("billing:shipment_payment_create", args=[w.noted_shipment.pk]),
        _base() + [AMOUNT + billing_services.consumption_tax(w.customer, [AMOUNT])],
    ),
    "billing:payment_cancel": lambda w: (
        reverse("billing:payment_cancel", args=[w.payment.pk]),
        _base() + [PAYMENT] + record_money(_fresh(w.invoice)),
    ),
    "billing:adjustment_create": lambda w: (
        reverse("billing:adjustment_create", args=[w.adjustment.invoice_id]),
        _base() + [PAYMENT, ADJUSTMENT] + record_money(_fresh(w.invoice)),
    ),
    "billing:adjustment_cancel": lambda w: (
        reverse("billing:adjustment_cancel", args=[w.adjustment.pk]),
        _base() + [PAYMENT, ADJUSTMENT] + record_money(_fresh(w.invoice)),
    ),
    "intake:extraction_accept": lambda w: (reverse("intake:extraction_accept", args=[w.extraction.pk]), _base()),
}


def _fresh(record):
    record.refresh_from_db()
    return record


def _made(*records) -> bool:
    """記録を作らせるだけの印 (MAKERS の lambda の中で、URL の前に記録を作る)。いつも真。"""
    return True


@pytest.fixture
def money_on(settings):
    settings.SEIZOU = {"access_control": dict(MONEY_ON)}


@pytest.fixture
def world(db, monkeypatch):
    return World(monkeypatch)


def no_money_person():
    """金額の閲覧の無い人 (編集の権限は全部ある。編集の判定は off)。"""
    return account("jimu", permissions=["master_edit", "slip_edit"])


def money_person():
    return account("keiri", permissions=["master_edit", "slip_edit"], see_money=True)


# ---------------------------------------------------------------- 試験


def test_lists_are_fixed():
    """金額に関わる操作の入力の画面の数と、試験の一覧 (MAKERS・外した画面・字の出ない画面) を固定する (黙って減らさない)。"""
    screens = money_form_screens()
    assert len(screens) == 29
    assert set(MAKERS) | set(NOT_COVERED) == set(screens)
    assert not set(MAKERS) & set(NOT_COVERED)
    assert set(TYPED_MONEY_FIELDS) <= set(MAKERS)
    assert set(MONEY_NOT_ON_SCREEN) <= set(MAKERS)
    assert len(NOT_COVERED) == 0
    assert len(MONEY_NOT_ON_SCREEN) == 13
    assert len(TYPED_MONEY_FIELDS) == 4


@pytest.mark.django_db
@pytest.mark.parametrize("name", sorted(MAKERS))
def test_empty_post_redraw_hides_money(client, money_on, world, name):
    """金額の閲覧の無い人が、空の中身で直に送る。描き直しに金額の項目の欄と、作り物の単価と金額の字が無い。"""
    url, values = MAKERS[name](world)
    texts = money_texts(values)

    client.force_login(no_money_person())
    response = client.post(url, {})
    assert response.status_code in (200, 403), (name, response.status_code)
    body = response.content.decode()
    assert money_field_names(body, TYPED_MONEY_FIELDS.get(name, frozenset())) == [], name
    assert found_texts(body, texts) == [], name
    assert "data-fill-amount" not in body, name

    # 空振りでないこと: 金額の閲覧のある人が同じ画面を開くと、同じ字のどれかが出る (出ない画面は MONEY_NOT_ON_SCREEN)。
    client.force_login(money_person())
    opened = client.get(url)
    assert opened.status_code == 200, (name, opened.status_code)
    shown = found_texts(opened.content.decode(), texts)
    if name in MONEY_NOT_ON_SCREEN:
        assert shown == [], f"{name} は金額の閲覧のある人に字が出る (MONEY_NOT_ON_SCREEN から外す)"
    else:
        assert shown, f"{name} は金額の閲覧のある人にも字が出ない (試験の字の誤りか、MONEY_NOT_ON_SCREEN に訳を書く)"
