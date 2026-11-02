"""billing の公開する関数 (01 の 10.4)。画面とほかの部品は、この関数だけを通して請求書を書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は記録のために受ける。記録の列がまだ無いので、今は受けるだけで書かない。
- 中心の決まり (R09 = 請求書の明細は、出荷済みの物だけ) に当たったら RuleViolation("R09", message)。
  message は RULES.md の R09 の文と、外す出荷の番号と「外してください」。
  「出荷済み」は、発行の納品書に入っている出荷と読む。
- 決まりの番号の無い入力の誤り (出荷の一覧が空・取り消し済みの請求書を取り消す) は日本語の ValueError。
- ほかの部品の model は import しない。外部キーの先を related_model で引く (出荷・納品書・取引先)。
  出荷の受注の得意先と単価は、外部キーをたどって読む (orders を import しない。01 の 1 節)。
- 金額は DB で計算しない (R03)。明細の金額は masters.services.round_amount の 1 か所で丸め、合計は Python で足す。
- 消費税の計算は _tax の 1 か所、支払期日の計算は payment_due_date の 1 か所。
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from django.db import transaction

from seizou.core.exceptions import RuleViolation
from seizou.core.services import company, next_number
from seizou.masters.services import round_amount

from .models import Invoice, InvoiceLine

#: 消費税の率 (会社の設定に置くまでの定数)
TAX_RATE = Decimal("0.10")

_R09_RULE = "請求書の明細は、出荷済みの物だけ。"
_YEN = Decimal("1")
_ZERO = Decimal("0")

# ほかの部品の表は、外部キーの先として引く (shipping と masters の models を import しない)。
_Shipment = InvoiceLine._meta.get_field("shipment").related_model
_DeliveryNote = _Shipment._meta.get_field("delivery_note").related_model
_Partner = Invoice._meta.get_field("customer").related_model

#: 支払の月 → 締めの月に足す月の数
_MONTH_OFFSETS = {
    _Partner.PAYMENT_CURRENT: 0,
    _Partner.PAYMENT_NEXT: 1,
    _Partner.PAYMENT_AFTER_NEXT: 2,
}


def _refuse_unbillable(shipment, customer):
    """出荷が請求書に入れられないなら RuleViolation("R09")。ロックした出荷の行で見る。"""
    note = shipment.delivery_note
    if note is None or note.status != _DeliveryNote.STATUS_ISSUED:
        raise RuleViolation(
            "R09",
            f"{_R09_RULE}出荷 {shipment.shipment_no} は発行の納品書に入っていないので、外してください。",
        )
    invoiced = (
        InvoiceLine.objects.select_related("invoice")
        .filter(shipment=shipment, invoice__status=Invoice.STATUS_ISSUED)
        .first()
    )
    if invoiced is not None:
        raise RuleViolation(
            "R09",
            f"{_R09_RULE}出荷 {shipment.shipment_no} は請求書 {invoiced.invoice.invoice_no} に入っているので、"
            "外してください。",
        )
    if shipment.order_line.order.customer_id != customer.pk:
        raise RuleViolation(
            "R09",
            f"{_R09_RULE}請求書は得意先ごとに作るので、出荷 {shipment.shipment_no} を外してください。",
        )


def create_invoice(customer, shipments, user, *, issued_on):
    """得意先の出荷済みの出荷をまとめて請求書を作る。請求書番号は core の連番が付ける。

    明細は出荷ごとに 1 行 (並びは出荷日・出荷番号)。数量は出荷の数量、単価は受注の明細の単価の写し、
    金額は round_amount(customer, 数量 × 単価)。期間は明細の出荷日の最初と最後。
    支払期日は payment_due_date(customer, 期間の終わり) (支払の欄が空の取引先では空)。
    """
    shipments = list(shipments)
    if not shipments:
        raise ValueError("出荷がありません。請求書に入れる出荷を 1 つ以上選んでください。")
    for obj in shipments:
        if not isinstance(obj, _Shipment):
            raise RuleViolation(
                "R09", f"{_R09_RULE}出荷でない物 ({obj}) が入っているので、外してください。"
            )
    pks = sorted({shipment.pk for shipment in shipments})
    with transaction.atomic():
        locked = list(_Shipment.objects.select_for_update().filter(pk__in=pks).order_by("pk"))
        for shipment in locked:
            _refuse_unbillable(shipment, customer)
        locked.sort(key=lambda row: (row.shipped_at, row.shipment_no))

        lines = []
        for shipment in locked:
            unit_price = shipment.order_line.unit_price
            amount = round_amount(customer, shipment.qty * unit_price)
            lines.append((shipment, unit_price, amount))
        amounts = [amount for _shipment, _price, amount in lines]
        subtotal = sum(amounts, _ZERO)
        tax = _tax(customer, amounts)
        period_from = locked[0].shipped_at
        period_to = locked[-1].shipped_at

        invoice = Invoice.objects.create(
            invoice_no=next_number("invoice"),
            customer=customer,
            issued_on=issued_on,
            period_from=period_from,
            period_to=period_to,
            due_on=payment_due_date(customer, period_to),
            subtotal=subtotal,
            tax=tax,
            total=subtotal + tax,
        )
        for shipment, unit_price, amount in lines:
            InvoiceLine.objects.create(
                invoice=invoice,
                shipment=shipment,
                qty=shipment.qty,
                unit_price=unit_price,
                amount=amount,
            )
        return invoice


def cancel_invoice(invoice, user):
    """請求書を取消にする。明細は残す (入っていた出荷は、また請求できる)。"""
    with transaction.atomic():
        locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if locked.status == Invoice.STATUS_CANCELLED:
            raise ValueError("この請求書は取り消し済みです。")
        locked.status = Invoice.STATUS_CANCELLED
        locked.save(update_fields=["status"])
        return locked


def billable_shipments(customer=None):
    """請求できる出荷 (読むだけ)。発行の納品書に入っていて、取消でない請求書に入っていない出荷。

    create_invoice の断る条件の (1) 納品書と (2) 請求済みと同じ。customer を渡せば、その得意先の出荷だけ。
    """
    invoiced = InvoiceLine.objects.filter(invoice__status=Invoice.STATUS_ISSUED).values("shipment_id")
    queryset = _Shipment.objects.filter(delivery_note__status=_DeliveryNote.STATUS_ISSUED).exclude(
        pk__in=invoiced
    )
    if customer is not None:
        queryset = queryset.filter(order_line__order__customer=customer)
    return queryset


def payment_due_date(customer, base_date):
    """支払期日 (読むだけ)。締日・支払の月・支払日のどれかが空なら None (止めない)。

    (1) 締めの月: base_date の日が、その月の締日 (月の日数より大きければ月末。31 は月末) までなら
        base_date の月、過ぎていれば翌月。
    (2) 支払の月: 締めの月に 当月 = 0・翌月 = 1・翌々月 = 2 か月を足す (年をまたぐ)。
    (3) 支払日がその月の日数より大きければ月末 (31 は月末)。
    """
    closing_day = customer.closing_day
    payment_month = customer.payment_month
    payment_day = customer.payment_day
    if closing_day is None or not payment_month or payment_day is None:
        return None
    year, month = base_date.year, base_date.month
    if base_date.day > _day_in_month(year, month, closing_day):
        year, month = _add_months(year, month, 1)
    year, month = _add_months(year, month, _MONTH_OFFSETS[payment_month])
    return date(year, month, _day_in_month(year, month, payment_day))


def _add_months(year, month, count):
    """(年, 月) に count か月を足した (年, 月)。"""
    index = year * 12 + (month - 1) + count
    return index // 12, index % 12 + 1


def _day_in_month(year, month, day):
    """その月の日数より大きい日を月末に寄せる。"""
    return min(day, calendar.monthrange(year, month)[1])


def _tax(customer, amounts):
    """消費税。方法は取引先の消費税の計算方法 (空なら会社の値)。

    total (トータル) = 金額の合計 × 率を round_amount(customer, …) で丸める。
    line_round (各行四捨五入) = 行ごとに 金額 × 率 を 1 円に四捨五入して足す。
    line_floor (各行切捨) = 行ごとに 金額 × 率 を 1 円に切り捨てて足す。
    """
    mode = customer.tax_mode or company().tax_mode
    if mode == "line_round":
        return sum(((amount * TAX_RATE).quantize(_YEN, rounding=ROUND_HALF_UP) for amount in amounts), _ZERO)
    if mode == "line_floor":
        return sum(((amount * TAX_RATE).quantize(_YEN, rounding=ROUND_FLOOR) for amount in amounts), _ZERO)
    return round_amount(customer, sum(amounts, _ZERO) * TAX_RATE)
