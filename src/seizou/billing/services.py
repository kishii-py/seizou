"""billing の公開する関数 (01 の 10.4)。画面とほかの部品は、この関数だけを通して請求書を書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は記録のために受ける。記録の列がまだ無いので、今は受けるだけで書かない。
- 中心の決まり (R09 = 請求書の明細は、出荷済みの物だけ) に当たったら RuleViolation("R09", message)。
  message は RULES.md の R09 の文と、外す出荷の番号と「外してください」。
  「出荷済み」は、発行の納品書に入っている出荷と読む。
- 決まりの番号の無い入力の誤り (出荷の一覧が空・取り消し済みの請求書を取り消す・取消でない入金の残る請求書を取り消す) は日本語の ValueError。
- ほかの部品の model は import しない。外部キーの先を related_model で引く (出荷・納品書・取引先)。
  出荷の受注の得意先と単価は、外部キーをたどって読む (orders を import しない。01 の 1 節)。
- 金額は DB で計算しない (R03)。明細の金額は masters.services.round_amount の 1 か所で丸め、合計は Python で足す。
  出荷 1 つの金額の式は shipment_amount の 1 か所 (create_invoice と、請求書の新規登録の画面の行の金額の見込みが同じ関数を使う)。
- 消費税の計算は _tax の 1 か所、請求の期間 (得意先の締め期間) は closing_period の 1 か所、支払期日の計算は payment_due_date の 1 か所。
- 入金は record_payment・cancel_payment の 2 つだけが書く。残は Invoice の method (取消でない入金を Python で足す)。
  取消した請求書への入金は R21 で断る。多い入金・未来の入金日は止めない (現物優先)。
  取消でない入金の残る請求書は cancel_invoice が断る (先に cancel_payment。文は cancel_refusal の 1 か所。決め 11-04 06:4x)。
- create_invoice は、返す請求書に画面の知らせの字 saved_note (同じ締めの期間に残った出荷 = unbilled_in_period) を付け、残りがあれば saved_level = "warning" (黄) も付ける (表に書かない。止めない)。
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction

from seizou.core import access
from seizou.core.exceptions import RuleViolation
from seizou.core.services import company, next_number
from seizou.masters.services import round_amount

from .models import Invoice, InvoiceLine, Payment

#: 消費税の率 (会社の設定に置くまでの定数)
TAX_RATE = Decimal("0.10")
#: 入金の備考の上限 (表の列と同じ)
PAYMENT_NOTE_MAX = 500

_R09_RULE = "請求書の明細は、出荷済みの物だけ。"
_R21_RULE = "取消した請求書には、入金を記録できない。"
_YEN = Decimal("1")
_ZERO = Decimal("0")

# ほかの部品の表は、外部キーの先として引く (shipping と masters の models を import しない)。
_Shipment = InvoiceLine._meta.get_field("shipment").related_model
_DeliveryNote = _Shipment._meta.get_field("delivery_note").related_model
_Partner = Invoice._meta.get_field("customer").related_model

#: 締日の 31 は月末 (masters の締日と支払日と同じ約束)。締日が空の取引先は、請求の期間を月末で締める
_MONTH_END = 31

#: 支払の月 → 締めの月に足す月の数
_MONTH_OFFSETS = {
    _Partner.PAYMENT_CURRENT: 0,
    _Partner.PAYMENT_NEXT: 1,
    _Partner.PAYMENT_AFTER_NEXT: 2,
}


def _refuse_unbillable(shipment, customer):
    """出荷が請求書に入れられないなら RuleViolation("R09")。ロックした出荷の行で見る。

    訳: 請求書の明細を出荷済みの物 (発行の納品書に入った出荷) だけにするため。出所 = RULES.md R09。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    note = shipment.delivery_note
    if note is None or note.status != _DeliveryNote.STATUS_ISSUED:
        raise RuleViolation(
            "R09",
            f"発行できません。{_R09_RULE}出荷 {shipment.shipment_no} は発行の納品書に入っていないので、外してください。",
        )
    invoiced = (
        InvoiceLine.objects.select_related("invoice")
        .filter(shipment=shipment, invoice__status=Invoice.STATUS_ISSUED)
        .first()
    )
    if invoiced is not None:
        raise RuleViolation(
            "R09",
            f"発行できません。{_R09_RULE}出荷 {shipment.shipment_no} は請求書 {invoiced.invoice.invoice_no} に入っているので、"
            "外してください。",
        )
    if shipment.order_line.order.customer_id != customer.pk:
        raise RuleViolation(
            "R09",
            f"発行できません。{_R09_RULE}請求書は得意先ごとに作るので、出荷 {shipment.shipment_no} を外してください。",
        )


def create_invoice(customer, shipments, user, *, issued_on):
    """得意先の出荷済みの出荷をまとめて請求書を作る。請求書番号は core の連番が付ける。

    明細は出荷ごとに 1 行 (並びは出荷日・出荷番号)。数量は出荷の数量、単価は受注の明細の単価の写し、
    金額は round_amount(customer, 数量 × 単価)。期間は得意先の締め期間 = closing_period(customer, 最後の出荷日)
    (最後の出荷日以降で最初の締日まで。初めはその 1 つ前の締日の翌日)。作るときに決めて表に残す。
    支払期日は payment_due_date(customer, 期間の終わり) (支払の欄が空の取引先では空)。
    返す請求書に saved_note (同じ期間に残った出荷の知らせの字。_period_note) と、残りがあれば saved_level = "warning" を付ける。
    訳: 請求書の明細を出荷済みの物だけにするため (出荷でない物を混ぜたら断る。出荷ごとの見方は _refuse_unbillable)。出所 = RULES.md R09。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    shipments = list(shipments)
    if not shipments:
        raise ValueError("発行できません。出荷がありません。請求書に入れる出荷を 1 つ以上選んでください。")
    for obj in shipments:
        if not isinstance(obj, _Shipment):
            raise RuleViolation(
                "R09", f"発行できません。{_R09_RULE}出荷でない物 ({obj}) が入っているので、外してください。"
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
            amount = shipment_amount(customer, shipment)
            lines.append((shipment, unit_price, amount))
        amounts = [amount for _shipment, _price, amount in lines]
        subtotal = sum(amounts, _ZERO)
        tax = _tax(customer, amounts)
        period_from, period_to = closing_period(customer, locked[-1].shipped_at)

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
        # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の後ろに付ける)。表に書かない。
        # 今作った明細は取消でない請求書に入っているので、残りに入らない。
        invoice.saved_note, level = _period_note(customer, period_from, period_to)
        if level:
            invoice.saved_level = level
        return invoice


def _period_note(customer, period_from, period_to):
    """請求書を作った後の知らせの字と色 (読むだけ)。(saved_note の字, "warning" か空の字)。

    同じ得意先の同じ締めの期間に、まだ請求書に入っていない出荷 (unbilled_in_period) があれば、件数と出荷番号を並べて黄。
    発行の納品書に入っていない出荷 (納品書が無いか取消。_refuse_unbillable と同じ分け方) には「(発行の納品書なし)」を付ける。
    残りが無ければ「全部請求書に入っています」の字で、色は付けない (緑)。金額は書かない。
    """
    rows = list(unbilled_in_period(customer, period_from, period_to))
    if not rows:
        return f"この期間の{customer.name}の出荷は、全部請求書に入っています。", ""
    numbers = "・".join(
        row.shipment_no if _in_issued_note(row) else f"{row.shipment_no} (発行の納品書なし)" for row in rows
    )
    return (
        f"この期間 ({period_from:%Y-%m-%d}〜{period_to:%Y-%m-%d}) の{customer.name}の出荷で、"
        f"まだ請求書に入っていない物が {len(rows)} 件あります: {numbers}。",
        "warning",
    )


def _in_issued_note(shipment):
    """出荷が発行の納品書に入っているか (R09 の「出荷済み」の読み)。"""
    note = shipment.delivery_note
    return note is not None and note.status == _DeliveryNote.STATUS_ISSUED


def _yen(value):
    """金額の字。core の |comma と同じ形 (3 桁ごとの区切り。小数の末尾の 0 を落とす。7500.00 → 7,500、1234.50 → 1,234.5)。

    頭の ¥ は呼ぶ側の字に書く (「合計 ¥{_yen(…)}」。保存のボタンの字の型「¥{amount}」と同じ置き方)。
    """
    return f"{value.normalize():,f}"


def cancel_refusal(invoice, *, hide_money=False) -> str:
    """請求書を取り消せない理由の文 (読むだけ)。取り消せるなら空の字。

    取消でない入金が 1 件でも残っていれば、先に入金を取り消す文 (残りの件数と合計) を返す。
    hide_money が真なら合計を書かず件数だけ (金額の閲覧の無い人)。
    cancel_invoice と取消の確かめの画面 (billing_tags の invoice_cancel_refusal) が同じ文を使う。請求書の状態は見ない。
    訳: 取消でない入金が残ったまま請求書を取り消さないため (先に入金を取り消す)。出所 = 未確認 (この module と cancel_invoice の docstring に「決め 11-04 06:4x」とあるが、本人の言葉は記録に無い。作った晩の記録は docs/nights/20261104/N89.md)。
    変えてよい条件 = Issue「決まりの提案」でこの訳に対して議論してから。
    """
    rows = [row for row in invoice.payments.all() if row.status == Payment.STATUS_RECORDED]
    if not rows:
        return ""
    left = f"残り {len(rows)} 件"
    if not hide_money:
        left += f"・合計 ¥{_yen(sum((row.amount for row in rows), _ZERO))}"
    return f"取り消せません。入金が残っています。先に入金を取り消してください ({left})。"


def cancel_invoice(invoice, user):
    """請求書を取消にする。明細は残す (入っていた出荷は、また請求できる)。

    取消でない入金が残っていれば、何も変えずに ValueError (先に cancel_payment で入金を取り消す。決め 11-04 06:4x)。
    文は cancel_refusal の 1 か所 (金額の閲覧の無い人には件数だけ = access.hides_money)。
    入金の行はロックしない (record_payment は請求書の行をロックしてから入金を足すので、請求書の行のロックで足りる)。
    """
    with transaction.atomic():
        locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if locked.status == Invoice.STATUS_CANCELLED:
            raise ValueError("取り消せません。この請求書は取り消し済みです。請求書の詳細を開き直してください。")
        refusal = cancel_refusal(locked, hide_money=access.hides_money(user))
        if refusal:
            raise ValueError(refusal)
        locked.status = Invoice.STATUS_CANCELLED
        locked.save(update_fields=["status"])
        # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
        locked.saved_message = "取り消しました。"
        return locked


def record_payment(invoice, received_on, amount, user, *, method=Payment.METHOD_TRANSFER, note=""):
    """請求書に入金を 1 件記録する。記録した人は user (pk が無ければ空)。

    金額は 0 より大きい数。入金の方法は 振込 / 現金 / 手形 / その他。備考は 500 字まで (番号の無い入力の誤り = ValueError)。
    取消した請求書には記録できない (R21)。多い入金 (残を超える額)・未来の入金日・入金済みの請求書へのさらなる入金は止めない (現物優先)。
    訳: 取消した請求書に入金を記録させないため (なぜ要るかは未確認。原則の「記録どうしの矛盾」に当たるかは本人が決める)。出所 = RULES.md R21 (決め 11-03 14:1x。理由の字は仕様 01 の表 (2026-11-01) に無い)。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    try:
        amount = Decimal(str(amount))
        readable = amount.is_finite()
    except (InvalidOperation, ValueError, TypeError):
        readable = False
    if not readable:
        raise ValueError(f"記録できません。入金の金額を数として読めません (入れた値: {amount!r})。数を入れてください。")
    if not amount > 0:
        raise ValueError(f"記録できません。入金の金額が 0 以下です。入金の金額は 0 より大きい数にしてください (入れた値: {amount})。")
    if method not in dict(Payment.METHOD_CHOICES):
        raise ValueError(f"記録できません。知らない入金の方法です。入金の方法は 振込 / 現金 / 手形 / その他 のどれかにしてください (入れた値: {method!r})。")
    if len(note or "") > PAYMENT_NOTE_MAX:
        raise ValueError(f"記録できません。備考が {PAYMENT_NOTE_MAX} 字を超えています。備考は {PAYMENT_NOTE_MAX} 字までにしてください。")
    with transaction.atomic():
        locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if locked.status == Invoice.STATUS_CANCELLED:
            raise RuleViolation("R21", f"記録できません。{_R21_RULE}請求書 {locked.invoice_no} は取消なので、発行の請求書を選び直してください。")
        return Payment.objects.create(
            invoice=locked,
            received_on=received_on,
            amount=amount,
            method=method,
            note=note or "",
            recorded_by=user if getattr(user, "pk", None) is not None else None,
        )


def cancel_payment(payment, user):
    """記録した入金を取消にする。行は消さない (履歴)。請求書の状態は見ない (取消の請求書に残った入金も取り消せる)。"""
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status == Payment.STATUS_CANCELLED:
            raise ValueError("取り消せません。この入金は取り消し済みです。請求書の詳細を開き直してください。")
        locked.status = Payment.STATUS_CANCELLED
        locked.save(update_fields=["status"])
        # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
        locked.saved_message = "取り消しました。"
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


def shipment_amount(customer, shipment):
    """出荷 1 つの請求の金額 (読むだけ)。round_amount(customer, 出荷の数量 × 受注の明細の単価)。

    create_invoice が明細の金額に使い、請求書の新規登録の画面 (billing.forms.InvoiceForm の rows) が行の金額の見込みに使う。
    訳: 画面に出す見込みと保存する明細の金額を 1 つの式にするため (頭の注記の「明細の金額は round_amount の 1 か所で丸め」と同じ考え)。出所 = RULES.md R03・AI の案 (依頼書 N248 = 候補 C74 の 1-1)。
    変えてよい条件 = 金額の式を変えるときは、この関数の 1 か所を変える (画面の見込みと保存が同じ関数を通る)。丸めを round_amount に任せること (R03) は変えない。
    """
    return round_amount(customer, shipment.qty * shipment.order_line.unit_price)


def unbilled_in_period(customer, period_from, period_to):
    """得意先の出荷のうち、出荷日が period_from〜period_to (両端を含む) にあり、取消でない請求書に入っていない物 (読むだけ)。

    並びは出荷日・出荷番号。billable_shipments と違い、発行の納品書に入っていない出荷 (納品書が無いか取消) も返す。
    出荷には状態の列が無いので、出荷の取消は見ない。create_invoice が作った後の知らせ (_period_note) に使う。
    """
    invoiced = InvoiceLine.objects.filter(invoice__status=Invoice.STATUS_ISSUED).values("shipment_id")
    return (
        _Shipment.objects.filter(
            order_line__order__customer=customer, shipped_at__gte=period_from, shipped_at__lte=period_to
        )
        .exclude(pk__in=invoiced)
        .select_related("delivery_note")
        .order_by("shipped_at", "shipment_no")
    )


def closing_period(customer, day):
    """day を含む得意先の締め期間 (読むだけ)。(期間の初め, 期間の終わり) の日付の組。

    締日は取引先の closing_day (31 は月末。月の日数より大きければ月末)。空なら月末で締める。
    (1) 終わり: day の日が、その月の締日までなら day の月の締日、過ぎていれば翌月の締日 (年をまたぐ)。
        締めの月の決め方は payment_due_date の (1) と同じ。
    (2) 初め: 終わりの 1 つ前の月の締日の翌日。
    例: 20 日締めで day が 10/29 → 10/21〜11/20。月末締めで 10/29 → 10/1〜10/31。
    """
    closing_day = _MONTH_END if customer.closing_day is None else customer.closing_day
    year, month = day.year, day.month
    if day.day > _day_in_month(year, month, closing_day):
        year, month = _add_months(year, month, 1)
    period_to = date(year, month, _day_in_month(year, month, closing_day))
    prev_year, prev_month = _add_months(year, month, -1)
    period_from = date(prev_year, prev_month, _day_in_month(prev_year, prev_month, closing_day)) + timedelta(days=1)
    return period_from, period_to


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
    訳: 消費税の端数を、取引先 (空なら会社) の消費税の計算方法 (トータル・各行四捨五入・各行切捨) どおりに 1 円に丸めるため。出所 = 未確認 (3 つの計算方法と率の根拠は、法令か商慣習の見込み。本人が書く)。
    変えてよい条件 = Issue「決まりの提案」でこの訳に対して議論してから。
    """
    mode = customer.tax_mode or company().tax_mode
    if mode == "line_round":
        return sum(((amount * TAX_RATE).quantize(_YEN, rounding=ROUND_HALF_UP) for amount in amounts), _ZERO)
    if mode == "line_floor":
        return sum(((amount * TAX_RATE).quantize(_YEN, rounding=ROUND_FLOOR) for amount in amounts), _ZERO)
    return round_amount(customer, sum(amounts, _ZERO) * TAX_RATE)
