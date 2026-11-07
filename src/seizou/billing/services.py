"""billing の公開する関数 (01 の 10.4)。画面とほかの部品は、この関数だけを通して請求書を書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は記録のために受ける。記録の列がまだ無いので、今は受けるだけで書かない。
- 中心の決まり (R09 = 請求書の明細は、出荷済みの物だけ) に当たったら RuleViolation("R09", message)。
  message は RULES.md の R09 の文と、外す出荷の番号と「外してください」。
  「出荷済み」は、発行の納品書に入っている出荷と読む。
- 中心の決まり (R23 = 同じ得意先の同じ締め期間の請求書は 1 枚だけ。請求の仕方が都度の得意先は除く) に当たったら
  RuleViolation("R23", message, link=(作り直しの画面の URL の名前, 1 枚目の pk, ボタンの字))。見るのは R09 の後、期間を決めた後
  (同じ出荷を 2 回入れたときは R09 で断る)。同時に 2 人が作っても 2 枚にならないよう、transaction の頭で得意先の行をロックする。
- 決まりの番号の無い入力の誤り (出荷の一覧が空・取り消し済みの請求書を取り消す・取消でない入金の残る請求書を取り消す) は日本語の ValueError。
- ほかの部品の model は import しない。外部キーの先を related_model で引く (出荷・納品書・取引先)。
  出荷の受注の得意先と単価は、外部キーをたどって読む (orders を import しない。01 の 1 節)。
- 金額は DB で計算しない (R03)。明細の金額は masters.services.round_amount の 1 か所で丸め、合計は Python で足す。
  請求書の明細は出荷の行と一式の行 (出荷に結び付かない行。決め PRICE-1d) の 2 つで、どちらも受注の明細 (order_line) を指す。
  出荷 1 つの金額の式は shipping.services.amount_of_shipment の 1 か所 (shipment_amount が呼ぶ。create_invoice と、請求書の新規登録の画面の
  行の金額の見込みが同じ関数を使う。一式の明細は明細を出し終えた出荷で一式の全額、ほかは 0 = 決め PRICE-1c)。
- 消費税の計算は _tax の 1 か所 (請求書の税率ごとの合計に 1 回。インボイス制度。丸める向きは masters.services.round_tax)、請求書の期間は invoice_period の 1 か所 (締日でまとめる得意先は closing_period の締め期間・
  都度の得意先は出荷の日)、支払期日の計算は payment_due_date の 1 か所。
  支払期日: 支払の月が当日なら期間の終わりの日 (支払日は見ない)。締日が空の得意先と、請求の仕方が都度の得意先は月末で締めて数える。
  支払の月か支払日が空 (当日でない) なら空。今ある請求書の支払期日は作った時の値のまま (計算し直さない)。
- 入金は record_payment・cancel_payment の 2 つだけが書く。残は Invoice の method (取消でない入金を Python で足す)。
  取消した請求書への入金は R21 で断る。多い入金・未来の入金日は止めない (現物優先)。
  取消でない入金の残る請求書は cancel_invoice が断る (先に cancel_payment。文は cancel_refusal の 1 か所。決め 11-04 06:4x)。
- 作り直しは reissue_invoice (同じ得意先・同じ期間の取消でない請求書を全部取り消し、入っていた出荷と期間の中で残っていた出荷で 1 枚に作る)。
  中身を決めるのは読むだけの reissue_plan の 1 か所 (作り直しの画面も同じ物を読む)。請求書を書く中身は create_invoice と同じ _issue_invoice。
  入金の残る請求書があれば何も変えずに ValueError (文は cancel_refusal)。
- create_invoice は、返す請求書に画面の知らせの字 saved_note (同じ締めの期間に残った出荷 = unbilled_in_period) を付け、残りがあれば saved_level = "warning" (黄) も付ける (表に書かない。止めない)。
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q

from seizou.core import access
from seizou.core.exceptions import RuleViolation
from seizou.core.services import next_number
from seizou.masters.services import round_tax
from seizou.shipping.services import amount_of_shipment

from .models import Invoice, InvoiceLine, Payment

#: 消費税の率 (会社の設定に置くまでの定数)
TAX_RATE = Decimal("0.10")
#: 入金の備考の上限 (表の列と同じ)
PAYMENT_NOTE_MAX = 500

_R09_RULE = "請求書の明細は、出荷済みの物だけ。"
_R21_RULE = "取消した請求書には、入金を記録できない。"
_R23_RULE = "同じ得意先の同じ締め期間の請求書は 1 枚だけ。"
_ZERO = Decimal("0")

# ほかの部品の表は、外部キーの先として引く (shipping と masters の models を import しない)。
_Shipment = InvoiceLine._meta.get_field("shipment").related_model
_DeliveryNote = _Shipment._meta.get_field("delivery_note").related_model
_Partner = Invoice._meta.get_field("customer").related_model

#: 締日の 31 は月末 (masters の締日と支払日と同じ約束)。締日が空の取引先は、請求の期間と支払期日を月末で締める
_MONTH_END = 31

#: 支払の月 → 締めの月に足す月の数 (当日は表を引く前に返すので入れない = payment_due_date の (0))
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
    受注の明細と単価の単位も写す。金額は shipment_amount (一式の明細は全部を出し終えた出荷で一式の全額、ほかは 0)。期間は invoice_period(customer, 出荷) で、作るときに決めて表に残す。
    締日でまとめる得意先は締め期間 = closing_period(customer, 最後の出荷日) (最後の出荷日以降で最初の締日まで。
    初めはその 1 つ前の締日の翌日)。都度の得意先は、最初の出荷日から最後の出荷日まで (納品書 1 枚なら、その出荷の日)。
    期間は、中小企業共通 EDI の請求の鑑ヘッダ取引期間 (取引開始日・取引終了日) を元にした項目で、EDI の付表では
    法的必須の印が付いている (どの法令の印かは未確認)。都度の請求書でも期間を空にしない。
    支払期日は payment_due_date(customer, 期間の終わり) (支払の月か支払日が空の取引先では空)。
    同じ得意先の同じ締め期間に取消でない請求書があれば、R09 の後・期間を決めた後に R23 で止める (_refuse_second_in_period)。
    返す請求書に saved_note (同じ期間に残った出荷の知らせの字。_period_note) と、残りがあれば saved_level = "warning" を付ける。
    消費税は _tax (明細の金額の合計に 1 回)。
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
    with transaction.atomic():
        # 得意先の行 → 出荷の行の順にロックする (R23。違う出荷を選んだ 2 人が同じ得意先の請求書を同時に作っても 2 枚にしない)。
        _Partner.objects.select_for_update().get(pk=customer.pk)
        return _issue_invoice(customer, shipments, issued_on)


def _issue_invoice(customer, shipments, issued_on):
    """請求書と明細を書く中身。create_invoice と reissue_invoice が、得意先の行をロックした transaction の中から呼ぶ。

    出荷の行をロックして R09 (_refuse_unbillable) を見て、金額 (shipment_amount)・消費税 (_tax)・期間 (invoice_period)・
    1 期間 1 枚の決まり (R23 = _refuse_second_in_period)・支払期日 (payment_due_date) を決めて書く (2 つの入口で同じ決まりを通す)。
    返す請求書に saved_note と、残りがあれば saved_level = "warning" を付ける。
    """
    pks = sorted({shipment.pk for shipment in shipments})
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
    period_from, period_to = invoice_period(customer, locked)
    _refuse_second_in_period(customer, period_from, period_to)

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
            order_line=shipment.order_line,
            qty=shipment.qty,
            unit_price=unit_price,
            price_basis=shipment.order_line.price_basis,
            amount=amount,
        )
    # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の後ろに付ける)。表に書かない。
    # 今作った明細は取消でない請求書に入っているので、残りに入らない。
    invoice.saved_note, level = _period_note(customer, period_from, period_to)
    if level:
        invoice.saved_level = level
    return invoice


def _refuse_second_in_period(customer, period_from, period_to):
    """同じ得意先の同じ締め期間に取消でない請求書があれば RuleViolation("R23")。請求の仕方が都度の得意先は見ない。

    同じ締め期間 = 期間の初めと終わりが同じ (期間の重なりでは見ない。締日を変えた得意先の前の請求書は別の期間)。
    止めの字は 期間・得意先の名前・請求書の番号 (2 枚以上なら「・」で並べる) で、金額は書かない。
    link は作り直しの画面 (billing:invoice_reissue) の 1 枚目 (pk の小さい方) とボタンの字。作り直しは同じ期間の全部を集めるので、
    どの請求書から入っても結果は同じ。
    訳: 同じ得意先の同じ締め期間に請求書が 2 枚できる事故を防ぐため (2 枚目は止めて、1 枚目の作り直しへ案内する)。出所 = RULES.md R23 (本人の決め 11-07 00:1x「同じ期間は何かしらの方法で1枚じゃないと事故の元でしょう」と 00:2x「bでいいんだ」)。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    if customer.billing_method == _Partner.BILLING_PER_DELIVERY:
        return
    existing = list(
        Invoice.objects.filter(
            customer=customer, status=Invoice.STATUS_ISSUED, period_from=period_from, period_to=period_to
        ).order_by("pk")
    )
    if not existing:
        return
    numbers = "・".join(row.invoice_no for row in existing)
    first = existing[0]
    raise RuleViolation(
        "R23",
        f"発行できません。{_R23_RULE}この期間 ({period_from:%Y-%m-%d}〜{period_to:%Y-%m-%d}) の{customer.name}の請求書 {numbers} "
        f"があるので、{numbers} を取り消して、この期間の全部の出荷で作り直してください。",
        link=("billing:invoice_reissue", first.pk, f"{first.invoice_no} を取り消して、この期間の全部の出荷で作り直す"),
    )


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

    取消でない入金が 1 件でも残っていれば、先に入金を取り消す文 (入金の記録の件数と合計) を返す。
    hide_money が真なら合計を書かず件数だけ (金額の閲覧の無い人)。
    cancel_invoice と取消の確かめの画面 (billing_tags の invoice_cancel_refusal) が同じ文を使う。請求書の状態は見ない。
    訳: 取消でない入金が残ったまま請求書を取り消さないため (先に入金を取り消す)。出所 = 未確認 (この module と cancel_invoice の docstring に「決め 11-04 06:4x」とあるが、本人の言葉は記録に無い。作った晩の記録は docs/nights/20261104/N89.md)。
    変えてよい条件 = Issue「決まりの提案」でこの訳に対して議論してから。
    """
    rows = [row for row in invoice.payments.all() if row.status == Payment.STATUS_RECORDED]
    if not rows:
        return ""
    money = "" if hide_money else f" (合計 ¥{_yen(sum((row.amount for row in rows), _ZERO))})"
    return f"取り消せません。この請求書には入金の記録が {len(rows)} 件あります{money}。先に入金を取り消してください。"


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


def _same_period_invoices(invoice):
    """請求書と同じ得意先・同じ期間 (初めと終わり) の取消でない請求書の queryset (pk の順)。渡した請求書が取消なら、それは入らない。"""
    return Invoice.objects.filter(
        customer_id=invoice.customer_id,
        status=Invoice.STATUS_ISSUED,
        period_from=invoice.period_from,
        period_to=invoice.period_to,
    ).order_by("pk")


def reissue_plan(invoice, *, hide_money=False) -> dict:
    """請求書の作り直しの中身 (読むだけ)。reissue_invoice と作り直しの画面 (billing_tags の invoice_reissue_plan) が同じ物を使う。

    返す辞書 = {"invoices": 取り消す請求書 (同じ得意先・同じ期間の取消でない請求書の全部。pk の順),
    "shipments": 新しい請求書に入る出荷 (出荷日・出荷番号の順), "refusal": 作り直せない理由の文 (作り直せるなら空の字),
    "refused": 理由の文の請求書 (無ければ None), "period_from", "period_to": 渡した請求書の期間}。
    入る出荷 = 取り消す請求書に入っていた出荷の全部 + その得意先の請求できる出荷 (billable_shipments) のうち出荷日が期間の中 (両端を含む) の物。
    理由の文は、取り消す請求書のどれかに取消でない入金が残っていれば、その請求書の cancel_refusal の文 (hide_money なら件数だけ)。
    """
    invoices = list(_same_period_invoices(invoice).prefetch_related("payments", "lines"))
    refusal, refused = "", None
    for row in invoices:
        text = cancel_refusal(row, hide_money=hide_money)
        if text:
            refusal, refused = text, row
            break
    taken = {line.shipment_id for row in invoices for line in row.lines.all()}
    in_period = billable_shipments(invoice.customer_id).filter(
        shipped_at__gte=invoice.period_from, shipped_at__lte=invoice.period_to
    )
    shipments = list(
        _Shipment.objects.filter(Q(pk__in=taken) | Q(pk__in=in_period.values("pk")))
        .select_related("order_line__item", "delivery_note")
        .order_by("shipped_at", "shipment_no")
    )
    return {
        "invoices": invoices,
        "shipments": shipments,
        "refusal": refusal,
        "refused": refused,
        "period_from": invoice.period_from,
        "period_to": invoice.period_to,
    }


def reissue_invoice(invoice, user, *, issued_on):
    """同じ得意先・同じ期間の取消でない請求書を全部取り消し、それらに入っていた出荷と期間の中で残っていた出荷で 1 枚に作り直す。

    1 つの transaction で、得意先の行 → 同じ期間の請求書の行の順にロックし (create_invoice と同じ順)、reissue_plan で中身を決める。
    どれかの請求書に取消でない入金が残っていれば、何も変えずに ValueError (文は cancel_refusal。金額の閲覧の無い人には件数だけ)。
    請求書は行を消さずに取消にし、新しい請求書は create_invoice と同じ中身 (_issue_invoice) で書く (R09・期間・R23・支払期日を通す)。
    期間は invoice_period で決まり、最後の出荷は期間の中にあるので、取り消した請求書と同じ期間になる。
    返す請求書に saved_message (「IV-000001 を取り消し、この期間の出荷 9 件で IV-000003 を作りました。」) と、
    _period_note の saved_note・saved_level を付ける (表に書かない)。
    訳: 同じ得意先の同じ締め期間の請求書を、前の請求書を取り消して 1 枚に作り直すため (入金の残る請求書は取り消さない = cancel_refusal と同じ決まり。取り消した請求書に入っていた出荷は全部入れ、期間の中で残っていた出荷を足すので、作り直しで請求から落ちる出荷は無い)。出所 = RULES.md R23・本人の決め 11-07 00:2x (本人が選んだ案「同じ期間の 2 枚目は赤の知らせで止め、1 枚目を取り消して期間の全部の出荷で作り直すボタンへ案内する」) と 11-07 06:11 (入れる出荷の決め)。入金を先に取り消す順は cancel_refusal の訳のとおり未確認。
    変えてよい条件 = Issue「決まりの提案」でこの訳に対して議論してから。
    """
    with transaction.atomic():
        customer = _Partner.objects.select_for_update().get(pk=invoice.customer_id)
        list(_same_period_invoices(invoice).select_for_update())
        plan = reissue_plan(invoice, hide_money=access.hides_money(user))
        refusal = plan["refusal"]
        if refusal:
            raise ValueError(refusal)
        if not plan["shipments"]:
            raise ValueError("発行できません。出荷がありません。請求書に入れる出荷を 1 つ以上選んでください。")
        for row in plan["invoices"]:
            row.status = Invoice.STATUS_CANCELLED
            row.save(update_fields=["status"])
        created = _issue_invoice(customer, plan["shipments"], issued_on)
        made = f"この期間の出荷 {len(plan['shipments'])} 件で {created.invoice_no} を作りました。"
        cancelled = "・".join(row.invoice_no for row in plan["invoices"])
        # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出し、saved_note を後ろに付ける)。表に書かない。
        created.saved_message = f"{cancelled} を取り消し、{made}" if cancelled else made
        return created


def record_payment(invoice, received_on, amount, user, *, method=Payment.METHOD_TRANSFER, note=""):
    """請求書に入金を 1 件記録する。記録した人は user (pk が無ければ空)。

    金額は 0 より大きい数。入金の方法は 振込 / 現金 / 手形 / その他。備考は 500 字まで (番号の無い入力の誤り = ValueError)。
    取消した請求書には記録できない (R21)。多い入金 (残を超える額)・未来の入金日・入金済みの請求書へのさらなる入金は止めない (現物優先)。
    返す入金に、画面の知らせの字 saved_message (「請求書 <番号> に ¥<金額> を入金として記録しました。」) を付ける (表に書かない)。
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
        payment = Payment.objects.create(
            invoice=locked,
            received_on=received_on,
            amount=amount,
            method=method,
            note=note or "",
            recorded_by=user if getattr(user, "pk", None) is not None else None,
        )
        # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
        payment.saved_message = f"請求書 {locked.invoice_no} に ¥{_yen(amount)} を入金として記録しました。"
        return payment


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
    # 一式の行 (出荷が空) を外す。空の値が NOT IN に入ると、どの出荷も外れるため。
    invoiced = InvoiceLine.objects.filter(invoice__status=Invoice.STATUS_ISSUED, shipment__isnull=False).values("shipment_id")
    queryset = _Shipment.objects.filter(delivery_note__status=_DeliveryNote.STATUS_ISSUED).exclude(
        pk__in=invoiced
    )
    if customer is not None:
        queryset = queryset.filter(order_line__order__customer=customer)
    return queryset


def shipment_amount(customer, shipment):
    """出荷 1 つの請求の金額 (読むだけ)。shipping.services.amount_of_shipment(出荷, partner=得意先) (部品の単位は数量 × 受注の明細の単価を丸めた値。一式の明細は明細を出し終えた出荷で一式の全額、ほかは 0 = 決め PRICE-1c)。

    create_invoice が明細の金額に使い、請求書の新規登録の画面 (billing.forms.InvoiceForm の rows) が行の金額の見込みに使う。
    訳: 画面に出す見込みと保存する明細の金額を 1 つの式にし、一式の明細は全部を出し終えた出荷で全額を請求に載せるため。出所 = RULES.md R03・作者の決め PRICE-1c。
    変えてよい条件 = 金額の式を変えるときは、この関数の 1 か所を変える (画面の見込みと保存が同じ関数を通る)。丸めを round_amount に任せること (R03) は変えない。
    """
    return amount_of_shipment(shipment, partner=customer)


def unbilled_in_period(customer, period_from, period_to):
    """得意先の出荷のうち、出荷日が period_from〜period_to (両端を含む) にあり、取消でない請求書に入っていない物 (読むだけ)。

    並びは出荷日・出荷番号。billable_shipments と違い、発行の納品書に入っていない出荷 (納品書が無いか取消) も返す。
    出荷には状態の列が無いので、出荷の取消は見ない。create_invoice が作った後の知らせ (_period_note) に使う。
    """
    # 一式の行 (出荷が空) を外す。空の値が NOT IN に入ると、どの出荷も外れるため。
    invoiced = InvoiceLine.objects.filter(invoice__status=Invoice.STATUS_ISSUED, shipment__isnull=False).values("shipment_id")
    return (
        _Shipment.objects.filter(
            order_line__order__customer=customer, shipped_at__gte=period_from, shipped_at__lte=period_to
        )
        .exclude(pk__in=invoiced)
        .select_related("delivery_note")
        .order_by("shipped_at", "shipment_no")
    )


def invoice_period(customer, shipments):
    """請求書の期間 (読むだけ)。(期間の初め, 期間の終わり) の日付の組。shipments は請求書に入れる出荷 (1 つ以上)。

    締日でまとめる得意先: 最後の出荷日を含む締め期間 = closing_period(customer, 最後の出荷日)。
    都度の得意先: 最初の出荷日〜最後の出荷日 (都度は締めないので、期間は出荷の日)。
    create_invoice と作り直し (reissue_invoice) が同じ関数を通る (期間を決める所を 2 か所に持たない)。
    """
    days = [shipment.shipped_at for shipment in shipments]
    if customer.billing_method == _Partner.BILLING_PER_DELIVERY:
        return min(days), max(days)
    return closing_period(customer, max(days))


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
    """支払期日 (読むだけ)。base_date は期間の終わり (締日でまとめる得意先は締日、都度の得意先は納品の日)。

    (0) 支払の月が当日なら base_date をそのまま返す (支払日は見ない。現金払いなど)。
        支払の月か支払日が空なら None (止めない。当日のときは支払日が空でも (0) で返る)。
    (1) 締めの月: base_date の日が、その月の締日 (月の日数より大きければ月末。31 は月末) までなら
        base_date の月、過ぎていれば翌月。締日が空の得意先は月末で締める (closing_period と同じ)。
        請求の仕方が都度の得意先は締日を見ず、月末で締める (締めの月 = 納品の日の月)。
    (2) 支払の月: 締めの月に 当月 = 0・翌月 = 1・翌々月 = 2 か月を足す (年をまたぐ)。
    (3) 支払日がその月の日数より大きければ月末 (31 は月末)。
    今ある請求書の支払期日 (Invoice.due_on) は作った時に決めて表に残した値で、ここを変えても変わらない。
    """
    payment_month = customer.payment_month
    payment_day = customer.payment_day
    if payment_month == _Partner.PAYMENT_SAME_DAY:
        return base_date
    if not payment_month or payment_day is None:
        return None
    if customer.billing_method == _Partner.BILLING_PER_DELIVERY or customer.closing_day is None:
        closing_day = _MONTH_END
    else:
        closing_day = customer.closing_day
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
    """消費税。請求書 1 枚の明細の金額を税率ごとに合計し、合計に 1 回だけ率をかけて 1 円に丸める。

    インボイス制度 (消費税法の適格請求書等保存方式) では、適格請求書の消費税額は、税率ごとに合計した金額に
    1 回だけ計算して端数を処理する (明細ごとに丸めて足さない)。今の率は TAX_RATE の 1 つだけなので、
    合計 × TAX_RATE を masters.services.round_tax(customer, …) で丸める。丸める向きは取引先の消費税の端数の向き
    (空なら会社の値。会社の既定は切捨。決め C95-1b)。金額の端数の扱い (rounding) は読まない。
    取引先と会社の消費税の端数の扱い (tax_mode) も読まない (選べる値はトータルだけ。決め LAW-2)。
    確かめた資料: 中小企業共通 EDI ガイドブック 1.1。国税庁の資料と条文は未読。
    訳: 適格請求書の消費税額を、税率ごとの合計に 1 回だけ計算する法令の決まりに合わせるため。出所 = 法令 (インボイス制度。決め LAW-2。確かめた資料は上の 1 つ)。
    変えてよい条件 = 率が増えたら、明細の率で分けた合計ごとに同じ計算を足す。明細ごとに丸めて足す形には、法令が変わったときだけ戻す。
    """
    return round_tax(customer, sum(amounts, _ZERO) * TAX_RATE)
