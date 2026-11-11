"""billing の公開する関数 (部品の公開する関数の設計の決め)。画面とほかの部品は、この関数だけを通して請求書を書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (services の書き方の設計の決め)。
- user は記録のために受け、変更の履歴 (core.services.record_history。作者の決めの伝票の「請求書」「入金」) に書く。書く前に core.history.saved_state で前を取り、
  書いた後に同じ transaction.atomic の中で残す (止まって戻ったら履歴も残らない)。明細と入金の行は請求書の詳細の「履歴」に出る
  (InvoiceLine.history_owner・Payment.history_owner)。請求書の外で受け取った入金は出荷の詳細の「履歴」に出る。
- 中心の決まり (R09 = 請求書の明細は、出荷済みの物だけ) に当たったら RuleViolation("R09", message)。
  message は RULES.md の R09 の文と、外す出荷の番号と「外してください」。
  「出荷済み」は、出荷の記録がある出荷と読む (納品書を発行したかどうかは見ない。本人の決め 11-11 10:1x)。
- 中心の決まり (R23 = 同じ得意先の同じ締め期間の請求書は 1 枚だけ。請求の仕方が都度の得意先は除く) に当たったら
  RuleViolation("R23", message, link=(作り直しの画面の URL の名前, 1 枚目の pk, ボタンの字))。見るのは R09 の後、期間を決めた後
  (同じ出荷を 2 回入れたときは R09 で断る)。同時に 2 人が作っても 2 枚にならないよう、transaction の頭で得意先の行をロックする。
- 決まりの番号の無い入力の誤り (出荷の一覧が空・取り消し済みの請求書を取り消す・取消でない入金の残る請求書を取り消す・
  請求の候補でない出荷に請求書の外の入金を記録する) は日本語の ValueError。
- ほかの部品の model は import しない。外部キーの先を related_model で引く (出荷・納品書・取引先)。
  出荷の受注の得意先と単価は、外部キーをたどって読む (orders を import しない。部品どうしの呼ぶ向きの設計の決め)。
- 金額は DB で計算しない (R03)。明細の金額は masters.services.round_amount の 1 か所で丸め、合計は Python で足す。
  請求書の明細は出荷の行と一式の行 (出荷に結び付かない行。作者の決め) の 2 つで、どちらも受注の明細 (order_line) を指す。
  出荷 1 つの金額の式は shipping.services.amount_of_shipment の 1 か所 (shipment_amount が呼ぶ。create_invoice と、請求書の新規登録の画面の
  行の金額の見込みが同じ関数を使う。一式の明細は明細を出し終えた出荷で一式の全額、ほかは 0 = 作者の決め)。
- 消費税の計算は _tax の 1 か所 (請求書の税率ごとの合計に 1 回。インボイス制度。丸める向きは masters.services.round_tax)、請求書の期間は invoice_period の 1 か所 (締日でまとめる得意先は closing_period の締め期間・
  都度の得意先は出荷の日)、支払期日の計算は payment_due_date の 1 か所。仕入明細の期間は closing_period を直に呼ぶ (purchasing の create_self_invoice。
  消費税と率は consumption_tax・consumption_tax_rate の口を通す。計算を写さない)。
  支払期日: 支払の月が当日なら期間の終わりの日 (支払日は見ない)。締日が空の得意先と、請求の仕方が都度の得意先は月末で締めて数える。
  支払の月か支払日が空 (当日でない) なら空。今ある請求書の支払期日は作った時の値のまま (計算し直さない)。
- 入金は record_payment・record_outside_payment・cancel_payment の 3 つだけが書く。残は Invoice の method (取消でない入金を Python で足す)。
  請求書の外で受け取った入金は record_outside_payment が出荷に書く (請求書は空。作者の決め)。今月の入金に入り、その出荷は請求の候補に出ない。
  取消した請求書への入金は R21 で断る。多い入金・未来の入金日は止めない (現物優先)。
  取消でない入金の残る請求書は cancel_invoice が断る (先に cancel_payment。文は cancel_refusal の 1 か所。決め 11-04 06:4x)。
- 作り直しは reissue_invoice (同じ得意先・同じ期間の取消でない請求書を全部取り消し、入っていた出荷と期間の中で残っていた出荷で 1 枚に作る)。
  中身を決めるのは読むだけの reissue_plan の 1 か所 (作り直しの画面も同じ物を読む)。請求書を書く中身は create_invoice と同じ _issue_invoice。
  入金の残る請求書があれば何も変えずに ValueError (文は cancel_refusal)。
- create_invoice は、返す請求書に画面の知らせの字 saved_note (同じ締めの期間に残った出荷 = unbilled_in_period) を付け、残りがあれば saved_level = "warning" (黄) も付ける (表に書かない。止めない)。
- 出し切らずに終えた一式の受注の明細 (請求書の新規登録の黄の 1 行が出す物。作者の決め) は unbilled_lumps、その明細の請求の材料 (出荷の合計・最後の出荷日・一式の全額・出した分) は lump_facts (どちらも読むだけ)。
  人が金額を決めた一式の行は create_invoice の lumps = (受注の明細, 金額) で載せる (R09 の見方は _refuse_unbillable_lump。同じ一式を 2 枚に入れない = 作者の決め。
  金額は 0 より大きい数 = 作者の決め)。一式の行だけの請求書の期間は発行日の締め期間 (都度の得意先は発行日の 1 日。作者の決め)。
  作り直し (reissue_invoice) は取り消した請求書の一式の行を同じ金額で写し、作り直しの画面で足した一式の行も受ける。
- 受注の明細に「請求しない」の印がある出荷は、請求の候補 (billable_shipments・unbilled_in_period・unbilled_lumps) に出さない。
  出荷の金額の式 (shipment_amount) は変えない (作者の決め)。
- 請求済みの出荷を後で直すか消しても止めない (検査の層の R27 が直す前に知らせる。本人の決め 11-11 12:5x〜13:0x)。
  請求した時の数量と今の出荷の数量が違う明細は changed_lines (読むだけ) が返す。
- 締めの一括 (締日を選ぶと、その締日の得意先の請求書をまとめて作る):
  closing_date(年, 月, 締日) は締める日 (その月の日数より大きい締日は月末。読むだけ)。
  closing_plan(締める日) は得意先ごとの行 (作る・もう在る・一式の金額を待つ・締日が空。読むだけ。2 段目の画面と create_closing_invoices が同じ中身を読む)。
  create_closing_invoices(締める日, user, issued_on=…) は作る行の得意先ごとに _issue_invoice を通し、どれかの得意先で断られたら 1 枚も作らない。
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Max, Q, Sum
from django.utils import timezone

from seizou.core import access, history
from seizou.core.exceptions import RuleViolation
from seizou.core.services import company, next_number, record_history
from seizou.masters.services import PRICE_BASIS_LUMP, line_amount, round_amount, round_tax
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
_OrderLine = _Shipment._meta.get_field("order_line").related_model
_Order = _OrderLine._meta.get_field("order").related_model
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

    見るのは、発行の請求書に入っている・得意先が違う、の 2 つ (納品書を発行したかどうかは見ない)。
    訳: 請求書の明細を出荷済みの物 (出荷の記録がある出荷) だけにし、1 つの出荷を発行の請求書 2 枚に入れず、請求書を得意先ごとに作るため。納品書は出荷の記録を刷った物で、発行したかどうかを請求の条件にしない。出所 = RULES.md R09・本人の決め 11-11 10:1x (「納品書は出荷の記録を写したものであるべきでは？」「OK」)。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
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


def _line_ref(order_line):
    """断りの字の受注の明細の呼び方 (「受注 O-000001 の 1 行目」)。"""
    return f"受注 {order_line.order.order_no} の {order_line.line_no} 行目"


def _refuse_unbillable_lump(order_line, customer, *, copying=False):
    """一式の行 (出荷に結び付かない請求書の明細) にできない受注の明細なら断る。ロックした受注の明細の行で見る。

    得意先が違う・出荷が無い・発行の請求書にこの明細の一式の行がある、の 3 つは
    RuleViolation("R09") (今の _refuse_unbillable の R09 の見方を一式の行に広げる。新しい決まりの番号は足さない)。
    受注の明細に「請求しない」の印 (no_bill) がある明細は、ValueError「…には「請求しない」の印が付いているので、外してください。」
    (下の ValueError の字を分けるだけ。画面は印の明細を出さないので、開いた後に印を付けた時と画面の外から呼んだ時だけ)。
    copying が真 (作り直しで取り消した請求書の一式の行を写す時) は印を見ない (請求書に一度入った物は、印に依らず作り直しで落とさない)。
    そのほかで出し切らずに終えた一式の明細 (unbilled_lumps) でない物 (単価の単位が式でない・明細を出し終えた出荷がある・
    まだ終わっていない) は ValueError (画面は unbilled_lumps の明細しか出さないので、画面の外から呼んだ時と、開いた後に出荷が進んだ時だけ)。
    明細を出し終えた出荷のある明細に一式の行を足すと、出荷の行の全額と二重になる。
    訳: 請求書の明細を出荷済みの物 (出荷の記録がある出荷) に結び付いた物だけにし (一式の行も出荷のある受注の明細だけ)、同じ一式を 2 枚の請求書に入れないため。納品書は出荷の記録を刷った物で、発行したかどうかを請求の条件にしない。出所 = RULES.md R09 と作者の決め・本人の決め 11-11 10:1x (「納品書は出荷の記録を写したものであるべきでは？」「OK」)。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    ref = _line_ref(order_line)
    if order_line.order.customer_id != customer.pk:
        raise RuleViolation("R09", f"発行できません。{_R09_RULE}請求書は得意先ごとに作るので、{ref}を外してください。")
    if not _Shipment.objects.filter(order_line=order_line).exists():
        raise RuleViolation("R09", f"発行できません。{_R09_RULE}{ref}の出荷が無いので、外してください。")
    billed = (
        InvoiceLine.objects.select_related("invoice")
        .filter(order_line=order_line, shipment__isnull=True, invoice__status=Invoice.STATUS_ISSUED)
        .order_by("pk")
        .first()
    )
    if billed is not None:
        raise RuleViolation(
            "R09", f"発行できません。{_R09_RULE}{ref}の一式は請求書 {billed.invoice.invoice_no} に入っているので、外してください。"
        )
    if order_line.no_bill and not copying:
        raise ValueError(f"発行できません。{ref}には「請求しない」の印が付いているので、外してください。")
    if order_line not in unbilled_lumps(customer, include_no_bill=copying):
        raise ValueError(f"発行できません。{ref}は、出し切らずに終えた一式の明細ではありません。出荷の行で請求してください。")


def _lump_amount(customer, order_line, amount):
    """一式の行に載せる金額 (round_amount(得意先, 金額) で 1 円に丸めた Decimal)。丸めた後が 0 以下か、数として読めなければ ValueError
    (あり得ない入力 = RULES.md の原則。form でも欄の下で止まる)。

    0 も止めるのは、一式の行に載せる金額を 0 より大きい数にするため (0 円の行は請求書に出さず、請求しない一式は「請求しない」の印にする)。
    訳: 0 円や負の一式の行を請求書に載せないため。出所 = 作者の決め。
    変えてよい条件 = 作者が一式の行の金額の決まりを決め直したとき。
    """
    try:
        value = Decimal(str(amount))
        readable = value.is_finite()
    except (InvalidOperation, ValueError, TypeError):
        readable = False
    if readable:
        value = round_amount(customer, value)
    if not readable or not value > 0:
        raise ValueError(
            f"発行できません。一式の金額が 0 以下か、数として読めません ({_line_ref(order_line)})。0 より大きい数を入れてください。"
        )
    return value


def create_invoice(customer, shipments, user, *, issued_on, lumps=()):
    """得意先の出荷済みの出荷 (と、出し切らずに終えた一式の受注の明細に人が決めた金額) をまとめて請求書を作る。請求書番号は core の連番が付ける。

    明細は出荷ごとに 1 行 (並びは出荷日・出荷番号)。数量は出荷の数量、単価は受注の明細の単価の写し、
    受注の明細と単価の単位も写す。金額は shipment_amount (一式の明細は全部を出し終えた出荷で一式の全額、ほかは 0)。期間は invoice_period(customer, 出荷, issued_on) で、作るときに決めて表に残す。
    lumps = (受注の明細, 金額) の組の並び (作者の決めの「人が金額を決めて載せる」)。組ごとに出荷に結び付かない一式の行を 1 行足す
    (出荷の行の後に受注番号・行番号の順)。一式の行の数量はその明細の出荷の合計、単価と金額は round_amount(得意先, 金額)、
    単価の単位は式、最後の出荷日はその出荷の最後の出荷日 (lump_facts)。明細ごとに _refuse_unbillable_lump で見てから、金額を見る
    (0 以下か数として読めなければ ValueError = _lump_amount)。
    締日でまとめる得意先は締め期間 = closing_period(customer, 最後の出荷日) (最後の出荷日以降で最初の締日まで。
    初めはその 1 つ前の締日の翌日)。都度の得意先は、最初の出荷日から最後の出荷日まで (納品書 1 枚なら、その出荷の日)。
    出荷の行があれば期間は出荷で決まり、一式の行は期間を動かさない。出荷の行が無く一式の行だけなら、締日でまとめる得意先は
    発行日の締め期間 (closing_period(customer, 発行日))、都度の得意先は発行日の 1 日 (作者の決め)。
    期間は、中小企業共通 EDI の請求の鑑ヘッダ取引期間 (取引開始日・取引終了日) を元にした項目で、EDI は取引期間を「法的必須」
    (インボイス制度で適格請求書等に記載を求められる情報項目) としている。一式の行だけの請求書でも空にしない (作者の決め)。
    一式の行の最後の出荷日は明細行取引終了日に当たり、適格請求書に載せる取引の日を行に残す。
    確かめた資料は中小企業共通 EDI の付表 3 のマッピング表とインボイス仕様編の表 5-3 で、インボイス制度の法令の本文と国税庁の資料は読んでいない (未確認)。
    支払期日は payment_due_date(customer, 期間の終わり) (支払の月か支払日が空の取引先では空)。
    同じ得意先の同じ締め期間に取消でない請求書があれば、R09 の後・期間を決めた後に R23 で止める (_refuse_second_in_period)。
    返す請求書に saved_note (同じ期間に残った出荷の知らせの字。_period_note) と、残りがあれば saved_level = "warning" を付ける。
    消費税は _tax (出荷の行と一式の行の金額の合計に 1 回)。
    出荷も一式の行も無ければ ValueError。
    訳: 請求書の明細を出荷済みの物 (出荷の記録がある出荷) だけにするため (出荷でない物を混ぜたら断る。出荷ごとの見方は _refuse_unbillable、出荷の記録が消えた物は _refuse_missing_shipments、一式の行の見方は _refuse_unbillable_lump)。出所 = RULES.md R09・本人の決め 11-11 10:1x (「納品書は出荷の記録を写したものであるべきでは？」「OK」)。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    shipments = list(shipments)
    lumps = list(lumps)
    if not shipments and not lumps:
        raise ValueError("発行できません。出荷がありません。請求書に入れる出荷を 1 つ以上選んでください。")
    for obj in shipments:
        if not isinstance(obj, _Shipment):
            raise RuleViolation(
                "R09", f"発行できません。{_R09_RULE}出荷でない物 ({obj}) が入っているので、外してください。"
            )
    with transaction.atomic():
        # 得意先の行 → 出荷の行 → 一式の受注の明細の行の順にロックする (R23。違う出荷を選んだ 2 人が同じ得意先の請求書を同時に作っても 2 枚にしない)。
        _Partner.objects.select_for_update().get(pk=customer.pk)
        return _issue_invoice(customer, shipments, issued_on, user, lumps=lumps)


def _issue_invoice(customer, shipments, issued_on, user, *, lumps=(), copies=(), period=None):
    """請求書と明細を書く中身。create_invoice と reissue_invoice が、得意先の行をロックした transaction の中から呼ぶ。

    出荷の行をロックして、出荷の記録が消えた物 (_refuse_missing_shipments) と R09 (_refuse_unbillable) を見て、金額 (shipment_amount)・消費税 (_tax)・期間 (invoice_period)・
    1 期間 1 枚の決まり (R23 = _refuse_second_in_period)・支払期日 (payment_due_date) を決めて書く (2 つの入口で同じ決まりを通す)。
    lumps = (受注の明細, 金額) の組の並び (人が金額を決めた一式の行)。copies = 作り直しで取り消した請求書から写す一式の行 (InvoiceLine。
    数量・金額・最後の出荷日をそのまま写す)。どちらも、出荷の行の後に一式の受注の明細の行と、その明細の出荷の行をロックしてから
    _refuse_unbillable_lump で見る (2 人が同じ一式を同時に載せても 1 枚にしか入らない。一式の行を作る間は、同じ明細の出荷の直しと削除が待つ)。
    period = 期間を渡すときの (初め, 終わり) (作り直しで出荷の行が無いとき、取り消した請求書と同じ期間にするため)。空なら invoice_period で決める。
    返す請求書に saved_note と、残りがあれば saved_level = "warning" を付ける。
    書いた請求書と明細は、変更の履歴に「作った」で残す (人は user)。
    発行した時の会社の登録番号・住所・電話・振込先と得意先の名前と税率、明細の品番・品名・単位 (受注の明細から)・得意先の注文番号を写す
    (刷り直しても渡した時と同じ字。決め 11-07 23:4x・11-08 00:0x。訳は billing.models の Invoice と InvoiceLine)。
    """
    pks = sorted({shipment.pk for shipment in shipments})
    locked = list(_Shipment.objects.select_for_update().filter(pk__in=pks).order_by("pk"))
    _refuse_missing_shipments(shipments, locked)
    for shipment in locked:
        _refuse_unbillable(shipment, customer)
    locked.sort(key=lambda row: (row.shipped_at, row.shipment_no))

    lines = []
    for shipment in locked:
        unit_price = shipment.order_line.unit_price
        amount = shipment_amount(customer, shipment)
        lines.append((shipment, unit_price, amount))
    lump_lines = _lump_values(customer, lumps, copies)
    amounts = [amount for _shipment, _price, amount in lines] + [row["amount"] for row in lump_lines]
    subtotal = sum(amounts, _ZERO)
    tax = _tax(customer, amounts)
    if period is not None and not locked:
        period_from, period_to = period
    else:
        period_from, period_to = invoice_period(customer, locked, issued_on)
    _refuse_second_in_period(customer, period_from, period_to)

    # 発行した時の会社の情報 (1 回だけ読む)。帳票の刷り直しは、この写しだけを読む (billing.reports.invoice_context)。
    issuer = company()
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
        issuer_registration_no=issuer.registration_no,
        issuer_address=issuer.address,
        issuer_phone=issuer.phone,
        issuer_bank_account=issuer.bank_account,
        customer_name=customer.name,
        tax_rate=consumption_tax_rate(),
    )
    record_history(invoice, user)
    created = []
    for shipment, unit_price, amount in lines:
        created.append(InvoiceLine.objects.create(
            invoice=invoice,
            shipment=shipment,
            order_line=shipment.order_line,
            qty=shipment.qty,
            unit_price=unit_price,
            price_basis=shipment.order_line.price_basis,
            amount=amount,
            **_line_snapshot(shipment.order_line),
        ))
    for row in lump_lines:
        created.append(InvoiceLine.objects.create(
            invoice=invoice,
            shipment=None,
            order_line=row["order_line"],
            qty=row["qty"],
            unit_price=row["amount"],
            price_basis=PRICE_BASIS_LUMP,
            amount=row["amount"],
            last_shipped_at=row["last_shipped_at"],
            **_line_snapshot(row["order_line"]),
        ))
    for invoice_line in created:
        record_history(invoice_line, user)
    # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の後ろに付ける)。表に書かない。
    # 今作った明細は取消でない請求書に入っているので、残りに入らない。
    invoice.saved_note, level = _period_note(customer, period_from, period_to)
    if level:
        invoice.saved_level = level
    return invoice


def _refuse_missing_shipments(shipments, locked):
    """渡した出荷のうち、ロックした行に無い物 (出荷の記録が消えた物) があれば RuleViolation("R09")。

    比べるのは渡した出荷の pk の集まりとロックした行の pk の集まり (同じ出荷を 2 回渡しても断らない)。
    番号は渡した物から読む (行はもう無い)。番号は出荷番号の順に「・」で並べる。
    訳: 出荷の記録が無い物を請求書に入れないため (新しい決まりでなく、R09 の「出荷済みの物だけ」の今の見方を外さないための止め。行が無いのを黙って落とすと、人が選んだ出荷が請求書から欠ける)。出所 = RULES.md R09・本人の決め 11-11 10:1x を受けた作者の決め。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    found = {row.pk for row in locked}
    missing = sorted({shipment.shipment_no for shipment in shipments if shipment.pk not in found})
    if missing:
        raise RuleViolation(
            "R09",
            f"発行できません。{_R09_RULE}出荷の記録が無い物 ({'・'.join(missing)}) が入っているので、外してください。",
        )


def _line_snapshot(order_line):
    """明細に写す字 (_issue_invoice の出荷の行と一式の行が使う)。部品の品番・品名、受注の明細の単位 (注文で合意した字。空なら空のまま)、
    受注の得意先の注文番号。"""
    return {
        "part_no": order_line.item.part_no,
        "item_name": order_line.item.name,
        "unit": order_line.unit,
        "customer_order_no": order_line.order.customer_order_no,
    }


def _lump_values(customer, lumps, copies):
    """一式の行の値の並び (受注番号・行番号の順)。1 つ = {order_line, qty, amount, last_shipped_at}。_issue_invoice が呼ぶ。

    lumps と copies の受注の明細の行と、その明細の出荷の行をロックしてから、明細ごとに _refuse_unbillable_lump で見る。
    lumps (受注の明細, 金額) は、金額を _lump_amount で見て、数量と最後の出荷日を lump_facts から決める。
    copies (取り消した請求書の一式の行) は、数量・金額・最後の出荷日をそのまま写す (同じ金額で写る)。
    """
    entries = [(line, amount, None) for line, amount in lumps] + [(row.order_line, row.amount, row) for row in copies]
    if not entries:
        return []
    pks = sorted({line.pk for line, _amount, _copy in entries})
    locked = {row.pk: row for row in _OrderLine.objects.select_for_update().filter(pk__in=pks).order_by("pk")}
    # 一式の明細の出荷の行もロックする (一式の行を作る間は、同じ明細の出荷の直しと削除が待つ)。
    list(_Shipment.objects.select_for_update().filter(order_line_id__in=pks).order_by("pk"))
    rows = []
    for line, amount, copy in entries:
        current = locked[line.pk]
        _refuse_unbillable_lump(current, customer, copying=copy is not None)
        if copy is None:
            facts = lump_facts(current)
            value = _lump_amount(customer, current, amount)
            rows.append(
                {"order_line": current, "qty": facts["shipped_qty"], "amount": value, "last_shipped_at": facts["last_shipped_at"]}
            )
        else:
            rows.append(
                {"order_line": current, "qty": copy.qty, "amount": copy.amount, "last_shipped_at": copy.last_shipped_at}
            )
    rows.sort(key=lambda row: (row["order_line"].order.order_no, row["order_line"].line_no))
    return rows


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

    同じ得意先の同じ締めの期間に、まだ請求書に入っていない出荷 (unbilled_in_period) があれば、件数と出荷番号を並べて黄
    (納品書を発行したかどうかでは分けない。どの出荷も請求書に入れられるため)。
    残りが無ければ「全部請求書に入っています」の字で、色は付けない (緑)。金額は書かない。
    """
    rows = list(unbilled_in_period(customer, period_from, period_to))
    if not rows:
        return f"この期間の{customer.name}の出荷は、全部請求書に入っています。", ""
    numbers = "・".join(row.shipment_no for row in rows)
    return (
        f"この期間 ({period_from:%Y-%m-%d}〜{period_to:%Y-%m-%d}) の{customer.name}の出荷で、"
        f"まだ請求書に入っていない物が {len(rows)} 件あります: {numbers}。",
        "warning",
    )


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
        before = history.saved_state(locked)
        locked.status = Invoice.STATUS_CANCELLED
        locked.save(update_fields=["status"])
        record_history(locked, user, before=before)
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
    "refused": 理由の文の請求書 (無ければ None), "period_from", "period_to": 渡した請求書の期間,
    "lumps": 取り消す請求書に入っていた一式の行 (InvoiceLine。受注の明細・数量・金額・最後の出荷日を同じ値で新しい請求書に写す。受注番号・行番号の順),
    "outside_paid": 取り消す請求書に入っていた出荷のうち、請求書の外で受け取った入金 (取消でない) のある物の (出荷, 入金) の並び (出荷日・出荷番号の順。
    1 つの出荷に 2 件あれば入金の pk の小さい方)}。
    請求書の外で受け取った入金 (取消でない) のある出荷は写さず、outside_paid に並べる (作者の決め。代金を受け取った出荷をもう一度請求しない)。
    入る出荷 = 取り消す請求書に入っていた出荷の全部 + その得意先の請求できる出荷 (billable_shipments) のうち出荷日が期間の中 (両端を含む) の物。
    一式の行 (出荷が空) は出荷の集まりに入れず、lumps に並べる (空の値を出荷の pk__in に渡さない。作り直しで一式の行も落とさない)。
    理由の文は、取り消す請求書のどれかに取消でない入金が残っていれば、その請求書の cancel_refusal の文 (hide_money なら件数だけ)。
    """
    invoices = list(_same_period_invoices(invoice).prefetch_related("payments", "lines"))
    refusal, refused = "", None
    for row in invoices:
        text = cancel_refusal(row, hide_money=hide_money)
        if text:
            refusal, refused = text, row
            break
    taken = {line.shipment_id for row in invoices for line in row.lines.all() if line.shipment_id is not None}
    lump_pks = [line.pk for row in invoices for line in row.lines.all() if line.shipment_id is None]
    lumps = list(
        InvoiceLine.objects.filter(pk__in=lump_pks)
        .select_related("order_line__order", "order_line__item")
        .order_by("order_line__order__order_no", "order_line__line_no")
    ) if lump_pks else []
    in_period = billable_shipments(invoice.customer_id).filter(
        shipped_at__gte=invoice.period_from, shipped_at__lte=invoice.period_to
    )
    shipments = list(
        _Shipment.objects.filter(Q(pk__in=taken) | Q(pk__in=in_period.values("pk")))
        .select_related("order_line__item", "delivery_note")
        .order_by("shipped_at", "shipment_no")
    )
    # 取り消す請求書に入っていた出荷のうち、請求書の外で受け取った入金 (取消でない) のある物は写さない (作者の決め)。
    paid = {}
    if taken:
        for payment in Payment.objects.filter(shipment_id__in=taken, status=Payment.STATUS_RECORDED).order_by("pk"):
            paid.setdefault(payment.shipment_id, payment)
    outside_paid = [(shipment, paid[shipment.pk]) for shipment in shipments if shipment.pk in paid]
    shipments = [shipment for shipment in shipments if shipment.pk not in paid]
    return {
        "invoices": invoices,
        "shipments": shipments,
        "refusal": refusal,
        "refused": refused,
        "period_from": invoice.period_from,
        "period_to": invoice.period_to,
        "lumps": lumps,
        "outside_paid": outside_paid,
    }


def reissue_invoice(invoice, user, *, issued_on, lumps=()):
    """同じ得意先・同じ期間の取消でない請求書を全部取り消し、それらに入っていた出荷と期間の中で残っていた出荷で 1 枚に作り直す。

    1 つの transaction で、得意先の行 → 同じ期間の請求書の行の順にロックし (create_invoice と同じ順)、reissue_plan で中身を決める。
    どれかの請求書に取消でない入金が残っていれば、何も変えずに ValueError (文は cancel_refusal。金額の閲覧の無い人には件数だけ)。
    請求書は行を消さずに取消にし、新しい請求書は create_invoice と同じ中身 (_issue_invoice) で書く (R09・期間・R23・支払期日を通す)。
    取り消した請求書に入っていた一式の行 (reissue_plan の lumps) は同じ数量・金額・最後の出荷日で写し、lumps = (受注の明細, 金額) の組の並び
    (作り直しの画面の一式の表で人が金額を決めて足した一式の行。create_invoice の lumps と同じ) も足す。写す一式の行と同じ受注の明細は足さない
    (写す方を残す。画面は写す一式の行の明細を一式の表に出さない)。
    期間は invoice_period で決まり、最後の出荷は期間の中にあるので、取り消した請求書と同じ期間になる。出荷の行が無く一式の行だけのときは、
    取り消した請求書と同じ期間にする (発行日で決めると、発行日の月によって期間が変わるため)。
    返す請求書に saved_message (「IV-000001 を取り消し、この期間の出荷 9 件で IV-000003 を作りました。」。一式の行があれば「出荷 9 件と一式の行 1 件」) と、
    _period_note の saved_note・saved_level を付ける (表に書かない)。
    請求書の外で受け取った入金 (取消でない) のある出荷は写さず (reissue_plan の outside_paid。作者の決め)、saved_message の末に
    「別に受け取った入金があるので入れない出荷: S-…。」を足して saved_level を "warning" にする (閉じるまで残す黄。黙って落とさない)。
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
        copied = {row.order_line_id for row in plan["lumps"]}
        extra = [(line, amount) for line, amount in lumps if line.pk not in copied]
        if not plan["shipments"] and not plan["lumps"] and not extra:
            raise ValueError("発行できません。出荷がありません。請求書に入れる出荷を 1 つ以上選んでください。")
        for row in plan["invoices"]:
            before = history.saved_state(row)
            row.status = Invoice.STATUS_CANCELLED
            row.save(update_fields=["status"])
            record_history(row, user, before=before)
        created = _issue_invoice(
            customer,
            plan["shipments"],
            issued_on,
            user,
            lumps=extra,
            copies=plan["lumps"],
            period=(plan["period_from"], plan["period_to"]),
        )
        lump_count = len(plan["lumps"]) + len(extra)
        parts = f"出荷 {len(plan['shipments'])} 件" + (f"と一式の行 {lump_count} 件" if lump_count else "")
        made = f"この期間の{parts}で {created.invoice_no} を作りました。"
        if plan["outside_paid"]:
            # 写さなかった出荷を、保存の後の知らせに並べて人の目に入れる (黙って落とさない。閉じるまで残す黄)。
            numbers = "・".join(shipment.shipment_no for shipment, _payment in plan["outside_paid"])
            made += f"別に受け取った入金があるので入れない出荷: {numbers}。"
            created.saved_level = "warning"
        cancelled = "・".join(row.invoice_no for row in plan["invoices"])
        # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出し、saved_note を後ろに付ける)。表に書かない。
        created.saved_message = f"{cancelled} を取り消し、{made}" if cancelled else made
        return created


# --- 締めの一括 (締日を選ぶと、その締日の得意先の請求書をまとめて作る) ---

#: 作らなかった得意先の名前を知らせの字に並べる数 (6 社目からは「ほか n 社」)
_CLOSING_NAMES_SHOWN = 5


def closing_date(year, month, closing_day):
    """締める日 (読むだけ)。year 年 month 月の締日 closing_day の日付。closing_day は 1〜31 の整数で、その月の日数より大きければ月末 (31 は月末)。

    締めの一括の 2 段目の form が年・月・締日を選び、この関数で締める日 (date) にする。closing_plan と create_closing_invoices は締める日だけを受ける。
    月の日数への寄せ方は closing_period と同じ (_day_in_month)。例: 2027 年 2 月の 31 → 2027-02-28、2028 年 2 月の 30 → 2028-02-29。
    1〜31 の外か整数でなければ ValueError (2 段目の form が先に止めるので、画面の外から呼んだ時だけ)。
    """
    if isinstance(closing_day, bool) or not isinstance(closing_day, int) or not 1 <= closing_day <= _MONTH_END:
        raise ValueError(
            f"発行できません。締日が 1〜31 の整数ではありません (入れた値: {closing_day!r})。締日は 1〜31 (31 は月末) で選んでください。"
        )
    return date(year, month, _day_in_month(year, month, closing_day))


def _refuse_early_closing(closing_on):
    """締める日が今日 (timezone.localdate()) より後なら、何も読まずに ValueError。締める日が今日なら通す。

    訳: 締日の前に締めると、締めた後の同じ期間の出荷が R23 で 2 枚目にできず、作り直しが要るため。1 社だけ先に締める道は請求書の新規登録に残す。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M12) と本人の決め 11-11 09:5x。
    変えてよい条件 = 本人が決め直したとき。
    """
    if closing_on > timezone.localdate():
        raise ValueError(
            f"発行できません。締める日 ({closing_on:%Y-%m-%d}) がまだ来ていません。締日の後に作ってください。"
            "1 社だけ先に締めるときは、請求書の新規登録から作ってください。"
        )


def _closing_customers(closing_on):
    """締める日が締日の得意先の queryset (読むだけ。並びは取引先コード)。

    請求の仕方が「締日でまとめる」で、締日が空でなく、closing_period(得意先, 締める日) の終わりが締める日と同じ得意先。
    問い合わせは締日の値で絞る: 締日が締める日の日と同じ得意先。締める日が月末なら、締日が締める日の日以上の得意先も入れる
    (closing_period は月の日数より大きい締日を月末に寄せるので、2026-02-28 の回には締日 28〜31 の得意先が入り、期間の終わりはどれも 2 月末)。
    締日が空の得意先は入れない (本人の決め 11-11 09:5x。closing_plan が "no_closing_day" の行で挙げる)。
    取引先の種類と使用中の印は見ない (closing_plan が行にするのは、請求の材料がある得意先だけ。使用停止の得意先でも請求できる出荷があれば作る)。
    """
    same_day = Q(closing_day=closing_on.day)
    if closing_on.day == _day_in_month(closing_on.year, closing_on.month, _MONTH_END):
        same_day |= Q(closing_day__gte=closing_on.day)
    return _Partner.objects.filter(
        same_day, billing_method=_Partner.BILLING_CLOSING, closing_day__isnull=False
    ).order_by("code")


def closing_plan(closing_on, *, hide_money=False):
    """締めの一括の中身 (読むだけ)。2 段目の画面の表と create_closing_invoices が同じ物を読む (作り直しの reissue_plan と reissue_invoice と同じ形)。

    返す物は得意先ごとの行の list (並びは得意先のコード)。1 行 = 辞書
    {"customer": 得意先, "period_from", "period_to": 期間 (締日が空の行は None),
    "state": 下の 4 つのどれか, "shipments": 出荷の list (出荷日・出荷番号の順), "amount": shipments の shipment_amount の合計 (税抜。hide_money なら None),
    "invoices": 同じ得意先の同じ期間 (初めと終わりが同じ) の取消でない請求書 (pk の順。_refuse_second_in_period と同じ見方),
    "waiting_lumps": 金額を決めていない一式の受注の明細 (unbilled_lumps のうち、最後の出荷日が締める日以前の物), "reason": 作らない行の訳の文 (金額を書かない。作る行は空の字)}。
    締める日が締日の得意先 (_closing_customers) の行: 期間 = closing_period(得意先, 締める日)。shipments = 請求できる出荷 (billable_shipments) のうち、
    出荷日が期間の中 (両端を含む) の物 (取消でない請求書に入った出荷・「請求しない」の印の明細の出荷・請求書の外で受け取った入金のある出荷は
    billable_shipments が除く。納品書を作っていない出荷も入る。期間より前に残った出荷と、締める日より後の出荷は入れない)。
    state の決め方 (上から順に見る):
    - "exists" (作らない): invoices がある。shipments があれば作り直しが要る (訳の文に出荷番号を並べる)。
    - "waiting" (作らない): waiting_lumps がある (一式の金額を人が決める。締める日より後に終わった一式は、この締めを止めない)。
    - "make" (作る): shipments がある。
    - どれも無い得意先は行にしない。
    締日が空の得意先の行: 請求の仕方が「締日でまとめる」で締日が空の得意先のうち、請求できる出荷に出荷日が締める日以前の物がある得意先を
    "no_closing_day" (作らない) の行にする (本人の決め 11-11 09:5x。月末で出すとミスの元なので入れず、作り忘れに気づけるよう挙げる)。
    期間は None、shipments はその出荷、invoices と waiting_lumps は空。
    """
    return _closing_rows(closing_on, _closing_customers(closing_on), hide_money=hide_money)


def _closing_rows(closing_on, customers, *, hide_money):
    """closing_plan の中身 (読むだけ)。customers = 締める日が締日の得意先 (create_closing_invoices はロックした行を渡す)。

    問い合わせは得意先の数に依らない: 請求できる出荷・同じ期間の請求書・一式の明細・締日が空の得意先の出荷を 1 回ずつ引き、得意先ごとに分ける。
    """
    customers = list(customers)
    periods = {customer.pk: closing_period(customer, closing_on) for customer in customers}
    rows = []
    if customers:
        start = min(period_from for period_from, _period_to in periods.values())
        found = _shipments_by_customer(
            billable_shipments().filter(
                order_line__order__customer__in=list(periods), shipped_at__gte=start, shipped_at__lte=closing_on
            )
        )
        invoices = {}
        for invoice in Invoice.objects.filter(
            customer_id__in=list(periods), status=Invoice.STATUS_ISSUED, period_to=closing_on
        ).order_by("pk"):
            if invoice.period_from == periods[invoice.customer_id][0]:
                invoices.setdefault(invoice.customer_id, []).append(invoice)
        lumps = {}
        for line in unbilled_lumps():
            if line.order.customer_id in periods and line.last_shipped_at <= closing_on:
                lumps.setdefault(line.order.customer_id, []).append(line)
        for customer in customers:
            period_from, period_to = periods[customer.pk]
            shipments = [shipment for shipment in found.get(customer.pk, []) if shipment.shipped_at >= period_from]
            same_period = invoices.get(customer.pk, [])
            waiting = lumps.get(customer.pk, [])
            if same_period:
                state = "exists"
            elif waiting:
                state = "waiting"
            elif shipments:
                state = "make"
            else:
                continue
            rows.append(
                _closing_row(customer, (period_from, period_to), state, shipments, same_period, waiting, hide_money=hide_money)
            )
    loose = billable_shipments().filter(
        order_line__order__customer__billing_method=_Partner.BILLING_CLOSING,
        order_line__order__customer__closing_day__isnull=True,
        shipped_at__lte=closing_on,
    )
    for shipments in _shipments_by_customer(loose.select_related("order_line__order__customer")).values():
        customer = shipments[0].order_line.order.customer
        rows.append(_closing_row(customer, (None, None), "no_closing_day", shipments, [], [], hide_money=hide_money))
    rows.sort(key=lambda row: row["customer"].code)
    return rows


def _shipments_by_customer(queryset):
    """出荷の queryset を {得意先の pk: 出荷の list (出荷日・出荷番号の順)} にする (問い合わせ 1 回)。"""
    found = {}
    for shipment in queryset.select_related("order_line__order", "order_line__item").order_by("shipped_at", "shipment_no"):
        found.setdefault(shipment.order_line.order.customer_id, []).append(shipment)
    return found


def _closing_row(customer, period, state, shipments, invoices, waiting, *, hide_money):
    """closing_plan の 1 行の辞書。金額は shipment_amount の合計 (hide_money なら None)。"""
    amount = None if hide_money else sum((shipment_amount(customer, shipment) for shipment in shipments), _ZERO)
    return {
        "customer": customer,
        "period_from": period[0],
        "period_to": period[1],
        "state": state,
        "shipments": shipments,
        "amount": amount,
        "invoices": invoices,
        "waiting_lumps": waiting,
        "reason": _closing_reason(state, shipments, invoices, waiting),
    }


def _closing_reason(state, shipments, invoices, waiting):
    """作らない行の訳の文 (金額を書かない)。作る行は空の字。"""
    numbers = "・".join(invoice.invoice_no for invoice in invoices)
    if state == "exists" and shipments:
        left = "・".join(shipment.shipment_no for shipment in shipments)
        return f"請求書 {numbers} の後に、この期間の出荷が {len(shipments)} 件あります ({left})。作り直しで入れてください。"
    if state == "exists":
        return f"請求書 {numbers} がもう在ります。"
    if state == "waiting":
        refs = "・".join(_line_ref(line) for line in waiting)
        return f"金額を決めていない一式の明細があります ({refs})。請求書の新規登録で金額を決めて作ってください。"
    if state == "no_closing_day":
        return (
            f"締日が空なので入れませんでした (請求していない出荷 {len(shipments)} 件)。"
            "取引先に締日を入れるか、請求書の新規登録で作ってください。"
        )
    return ""


def _closing_label(row):
    """作らなかった行の短い訳 (知らせの字に名前と並べる)。"""
    if row["state"] == "exists":
        return "作り直しが要る" if row["shipments"] else "もう在る"
    if row["state"] == "waiting":
        return "一式の金額"
    return "締日が空"


def _needs_hand(row):
    """人の手が要る作らなかった行か ("waiting"・"no_closing_day"・作り直しが要る "exists")。"""
    return row["state"] in ("waiting", "no_closing_day") or (row["state"] == "exists" and bool(row["shipments"]))


class ClosedInvoices(list):
    """締めの一括の返す物 (作った請求書の list。並びは得意先のコード)。入力の土台は saved_message・saved_note・saved_level を知らせに使う
    (一括出荷の shipping.services.ShippedLines と同じ形)。skipped = 作らなかった行 (closing_plan の行のうち state が "make" でない物)。
    """

    def __init__(self, invoices, closing_on, skipped):
        super().__init__(invoices)
        self.closing_on = closing_on
        self.skipped = list(skipped)

    @property
    def saved_message(self) -> str:
        """「<締める日> 締めで <n> 社の請求書を作りました (<最初の番号>〜<最後の番号>)。」(1 枚なら番号は 1 つ)。"""
        if not self:
            return ""
        numbers = self[0].invoice_no if len(self) == 1 else f"{self[0].invoice_no}〜{self[-1].invoice_no}"
        return f"{self.closing_on:%Y-%m-%d} 締めで {len(self)} 社の請求書を作りました ({numbers})。"

    @property
    def saved_note(self) -> str:
        """「作らなかった得意先 <n> 社: <名前> (<短い訳>)・…。」(名前は 5 社まで、6 社目からは「ほか n 社」)。作らなかった行が無ければ空の字。"""
        if not self.skipped:
            return ""
        names = [f"{row['customer'].name} ({_closing_label(row)})" for row in self.skipped[:_CLOSING_NAMES_SHOWN]]
        if len(self.skipped) > _CLOSING_NAMES_SHOWN:
            names.append(f"ほか {len(self.skipped) - _CLOSING_NAMES_SHOWN} 社")
        return f"作らなかった得意先 {len(self.skipped)} 社: {'・'.join(names)}。"

    @property
    def saved_level(self) -> str:
        """人の手が要る作らなかった行があれば "warning" (閉じるまで残る黄)。ほかは空の字 (緑)。"""
        return "warning" if any(_needs_hand(row) for row in self.skipped) else ""


def create_closing_invoices(closing_on, user, *, issued_on):
    """締める日 (closing_on) が締日の得意先の請求書を、得意先ごとに 1 枚ずつまとめて作る (締めの一括)。返す物は ClosedInvoices (作った請求書の並び)。

    先に _refuse_early_closing (締める日が今日より後なら何も読まずに止める)。1 つの transaction.atomic の中で、締める日が締日の得意先
    (_closing_customers) の行を pk の順に select_for_update でロックし (create_invoice と同じく得意先の行を先にロックする。2 人が同時に押すと
    後の人は待ち、前の人が作った請求書を "exists" と読む)、ロックの後に closing_plan と同じ中身 (_closing_rows) で行を決める。
    締日が空の得意先の行は作らないので、ロックしない。state が "make" の行ごとに _issue_invoice(得意先, 行の出荷, issued_on, user) を呼ぶ
    (R09・期間・R23・支払期日・発行の時の写し・変更の履歴は今の 1 か所を通す)。発行日は人が入れた日、支払期日は期間の終わりから決まる。
    "make" の行が 1 つも無ければ、何も作らずに ValueError (内訳は もう在る・人の手が要る・締日が空 の社の数のうち 0 でない物)。
    どれかの得意先で RuleViolation か ValueError が出たら全部を戻し、断りの文の頭に得意先の名前を付けて同じ型で投げ直す
    (一括出荷 shipping.services.ship_lines と同じ形。決まりの番号と止めの link はそのまま)。
    返す物の saved_message・saved_note・saved_level は ClosedInvoices が作る (作った請求書ごとの _period_note の字は使わない)。
    訳: 締日の得意先の請求書を 1 回の操作でまとめて作り、月末に得意先の数だけ同じ操作を繰り返さないため。得意先ごとに R09 と R23 を今の 1 か所 (_issue_invoice) で通し、どれかの得意先で断られたら 1 枚も作らない (一括出荷 ship_lines と同じ)。同じ期間の請求書がもう在る得意先と、金額を決めていない一式の明細が残る得意先は作らずに挙げる (直した後に押し直すと、残りの得意先だけを作る)。締日が空の得意先は月末で出すとミスの元なので入れず、作り忘れに気づけるよう挙げる。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M12)・RULES.md R09 と R23・本人の決め 11-11 09:5x。
    変えてよい条件 = 本人が締めの一括の決めを決め直したとき。
    """
    _refuse_early_closing(closing_on)
    with transaction.atomic():
        customers = list(_closing_customers(closing_on).select_for_update().order_by("pk"))
        rows = _closing_rows(closing_on, customers, hide_money=True)
        skipped = [row for row in rows if row["state"] != "make"]
        if len(skipped) == len(rows):
            raise ValueError(
                f"発行できません。{closing_on:%Y-%m-%d} 締めで作る請求書がありません。{_closing_breakdown(skipped)}。"
                + ("得意先ごとの訳を確かめてください。" if skipped else "締める日を確かめてください。")
            )
        created = []
        for row in rows:
            if row["state"] != "make":
                continue
            customer = row["customer"]
            try:
                created.append(_issue_invoice(customer, row["shipments"], issued_on, user))
            except RuleViolation as exc:
                raise RuleViolation(
                    exc.code, f"発行できません。{customer.name}: {exc.message.removeprefix('発行できません。')}", link=exc.link
                ) from exc
            except ValueError as exc:
                raise ValueError(f"発行できません。{customer.name}: {str(exc).removeprefix('発行できません。')}") from exc
    return ClosedInvoices(created, closing_on, skipped)


def _closing_breakdown(skipped):
    """作る行が無い時の内訳の字 (文末の「。」は呼ぶ側)。行が無ければ「この締日の得意先に、請求できる出荷がありません」。"""
    if not skipped:
        return "この締日の得意先に、請求できる出荷がありません"
    counts = (
        ("もう在る", sum(1 for row in skipped if row["state"] == "exists" and not _needs_hand(row))),
        ("人の手が要る", sum(1 for row in skipped if row["state"] != "no_closing_day" and _needs_hand(row))),
        ("締日が空の", sum(1 for row in skipped if row["state"] == "no_closing_day")),
    )
    return "・".join(f"{label} {count} 社" for label, count in counts if count)


def _payment_values(amount, method, note):
    """入金の金額・方法・備考の値を見て、Decimal に直した金額を返す (読むだけ)。record_payment と record_outside_payment が同じ検査を使う。

    金額は 0 より大きい数。入金の方法は 振込 / 現金 / 手形 / その他。備考は 500 字まで (番号の無い入力の誤り = ValueError)。
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
    return amount


def record_payment(invoice, received_on, amount, user, *, method=Payment.METHOD_TRANSFER, note=""):
    """請求書に入金を 1 件記録する。記録した人は user (pk が無ければ空)。

    金額は 0 より大きい数。入金の方法は 振込 / 現金 / 手形 / その他。備考は 500 字まで (番号の無い入力の誤り = ValueError)。
    取消した請求書には記録できない (R21)。多い入金 (残を超える額)・未来の入金日・入金済みの請求書へのさらなる入金は止めない (現物優先)。
    返す入金に、画面の知らせの字 saved_message (「請求書 <番号> に ¥<金額> を入金として記録しました。」) を付ける (表に書かない)。
    訳: 取消した請求書に入金を記録させないため (なぜ要るかは未確認。原則の「記録どうしの矛盾」に当たるかは本人が決める)。出所 = RULES.md R21 (決め 11-03 14:1x。理由の字は作者の会期前の決まりの表 (2026-11-01) に無い)。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    amount = _payment_values(amount, method, note)
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
        record_history(payment, user)
        # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
        payment.saved_message = f"請求書 {locked.invoice_no} に ¥{_yen(amount)} を入金として記録しました。"
        return payment


def record_outside_payment(shipment, received_on, amount, user, *, method=Payment.METHOD_CASH, note=""):
    """請求書の外で受け取った入金 (現金など) を出荷に 1 件記録する。記録した人は user (pk が無ければ空)。

    請求の候補 (billable_shipments) と同じ出荷にだけ記録する: 発行の請求書に入った出荷・取消でない請求書の外の入金がもうある出荷には
    記録せず、この順に見て ValueError (頭は「記録できません。」。作者の決め)。納品書を発行したかどうかは見ない (本人の決め 11-11 10:1x)。
    一式の明細の出荷と、受注の明細に「請求しない」の印がある出荷は見ない (断りはこの 2 つだけ)。
    記録した出荷は、請求の候補 (billable_shipments・unbilled_in_period) と納品書の詳細の緑の「請求書作成」に出ない。
    出荷の金額 (ダッシュボードの出荷の金額と売上の数え方) は変えない。ダッシュボードの今月の入金 (core.dashboard.payments_this_month) に入る。
    入金を取り消す (cancel_payment) と、その出荷は請求の候補に戻る。金額・方法・備考の値の検査は record_payment と同じ (_payment_values)。
    金額は人が入れた値で、出荷の金額と比べない (多い入金も少ない入金も止めない = 現物優先)。方法の既定は現金 (請求書の外で受け取る場面の多くのため)。
    出荷の行をロックしてから書く (請求書の作成 = create_invoice と納品書の取消 = cancel_delivery_note も出荷の行をロックするので、同時に走ってもどちらかが待つ)。
    返す入金に、画面の知らせの字 saved_message (「出荷 <番号> の ¥<金額> を、請求書の外で受け取った入金として記録しました。」) を付ける (表に書かない)。
    使う場面: 一式でない明細の出荷の代金を、請求書を作らずに受け取ったとき (作者の決め)。
    訳: 同じ代金を 2 回受け取った記録 (請求書の入金と請求書の外の入金、または請求書の外の入金の 2 件) を作らないため (二重の記録はダッシュボードの今月の入金を誤らせ、後で直す手が大きい)。出所 = 作者の決め。
    変えてよい条件 = 作者が請求書の外で受け取った入金の扱いを決め直したとき。
    """
    amount = _payment_values(amount, method, note)
    with transaction.atomic():
        locked = _Shipment.objects.select_for_update().get(pk=shipment.pk)
        _refuse_outside_payment(locked)
        payment = Payment.objects.create(
            shipment=locked,
            received_on=received_on,
            amount=amount,
            method=method,
            note=note or "",
            recorded_by=user if getattr(user, "pk", None) is not None else None,
        )
        record_history(payment, user)
    # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
    payment.saved_message = f"出荷 {locked.shipment_no} の ¥{_yen(amount)} を、請求書の外で受け取った入金として記録しました。"
    return payment


def _refuse_outside_payment(shipment):
    """請求書の外の入金を記録できない出荷なら ValueError (ロックした出荷の行で見る。順は 発行の請求書 → 外の入金)。

    字は段 2 の型「記録できません。<理由>。<次にすること>。」。外の入金の字に金額は入れない (金額を見られない人にも同じ字を出すため)。
    納品書を発行したかどうかは見ない (請求の候補と同じ。納品書は出荷の記録を刷った物 = 本人の決め 11-11 10:1x)。
    訳: 請求の候補でない出荷に請求書の外の入金を記録させず、同じ代金を 2 回受け取った記録を作らないため。出所 = 作者の決め。
    変えてよい条件 = 作者が請求書の外で受け取った入金の扱いを決め直したとき。
    """
    invoiced = (
        InvoiceLine.objects.select_related("invoice")
        .filter(shipment=shipment, invoice__status=Invoice.STATUS_ISSUED)
        .order_by("pk")
        .first()
    )
    if invoiced is not None:
        raise ValueError(
            f"記録できません。出荷 {shipment.shipment_no} は請求書 {invoiced.invoice.invoice_no} に入っています。"
            "代金は請求書の入金として記録してください。"
        )
    paid = Payment.objects.filter(shipment=shipment, status=Payment.STATUS_RECORDED).order_by("pk").first()
    if paid is not None:
        raise ValueError(
            f"記録できません。出荷 {shipment.shipment_no} には、請求書の外で受け取った入金 ({paid.received_on:%Y-%m-%d}) が"
            "もう記録されています。直すときは、出荷の詳細でその入金を取り消してから記録し直してください。"
        )


def cancel_payment(payment, user):
    """記録した入金を取消にする。行は消さない (履歴)。請求書の状態は見ない (取消の請求書に残った入金も取り消せる)。

    請求書の外で受け取った入金を取り消すと、その出荷は請求の候補に戻る。
    """
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status == Payment.STATUS_CANCELLED:
            raise ValueError("取り消せません。この入金は取り消し済みです。請求書の詳細を開き直してください。")
        before = history.saved_state(locked)
        locked.status = Payment.STATUS_CANCELLED
        locked.save(update_fields=["status"])
        record_history(locked, user, before=before)
        # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
        locked.saved_message = "取り消しました。"
        return locked


def billable_shipments(customer=None):
    """請求できる出荷 (読むだけ)。出荷の記録がある出荷のうち、取消でない請求書に入っていない物 (納品書を発行したかどうかは見ない。
    納品書が無い出荷と、取消の納品書を指したままの出荷も返す。本人の決め 11-11 10:1x)。

    create_invoice の断る条件の請求済み (_refuse_unbillable) と同じ。customer を渡せば、その得意先の出荷だけ。
    受注の明細に「請求しない」の印 (no_bill) がある出荷は除く (作者の決め。受注の明細の列を外部キーの道で読む)。
    請求書の外で受け取った入金 (取消でない) のある出荷は除く (作者の決め。出荷の逆の関係 payments)。
    """
    # 一式の行 (出荷が空) を外す。空の値が NOT IN に入ると、どの出荷も外れるため。
    invoiced = InvoiceLine.objects.filter(invoice__status=Invoice.STATUS_ISSUED, shipment__isnull=False).values("shipment_id")
    queryset = (
        _Shipment.objects.exclude(pk__in=invoiced)
        .exclude(order_line__no_bill=True)
        .exclude(payments__status=Payment.STATUS_RECORDED)
    )
    if customer is not None:
        queryset = queryset.filter(order_line__order__customer=customer)
    return queryset


def shipment_amount(customer, shipment):
    """出荷 1 つの請求の金額 (読むだけ)。shipping.services.amount_of_shipment(出荷, partner=得意先) (部品の単位は数量 × 受注の明細の単価を丸めた値。一式の明細は明細を出し終えた出荷で一式の全額、ほかは 0 = 作者の決め)。

    create_invoice が明細の金額に使い、請求書の新規登録の画面 (billing.forms.InvoiceForm の rows) が行の金額の見込みに使う。
    訳: 画面に出す見込みと保存する明細の金額を 1 つの式にし、一式の明細は全部を出し終えた出荷で全額を請求に載せるため。出所 = RULES.md R03・作者の決め。
    変えてよい条件 = 金額の式を変えるときは、この関数の 1 か所を変える (画面の見込みと保存が同じ関数を通る)。丸めを round_amount に任せること (R03) は変えない。
    """
    return amount_of_shipment(shipment, partner=customer)


def unbilled_in_period(customer, period_from, period_to):
    """得意先の出荷のうち、出荷日が period_from〜period_to (両端を含む) にあり、取消でない請求書に入っていない物 (読むだけ)。

    並びは出荷日・出荷番号。
    出荷には状態の列が無いので、出荷の取消は見ない。create_invoice が作った後の知らせ (_period_note) に使う。
    受注の明細に「請求しない」の印 (no_bill) がある出荷は除く (作った後の知らせに数えると、入れ忘れと読み違えるため。作者の決め)。
    請求書の外で受け取った入金 (取消でない) のある出荷も除く (作者の決め。訳は同じ)。
    """
    # 一式の行 (出荷が空) を外す。空の値が NOT IN に入ると、どの出荷も外れるため。
    invoiced = InvoiceLine.objects.filter(invoice__status=Invoice.STATUS_ISSUED, shipment__isnull=False).values("shipment_id")
    return (
        _Shipment.objects.filter(
            order_line__order__customer=customer, shipped_at__gte=period_from, shipped_at__lte=period_to
        )
        .exclude(pk__in=invoiced)
        .exclude(order_line__no_bill=True)
        .exclude(payments__status=Payment.STATUS_RECORDED)
        .order_by("shipped_at", "shipment_no")
    )


def unbilled_lumps(customer=None, *, include_no_bill=False):
    """出し切らずに終えた一式の受注の明細の並び (読むだけ。並びは受注番号・行番号)。請求書の新規登録の黄の 1 行が出す物 (作者の決め)。

    次の 6 つが全部当たる受注の明細: (1) 単価の単位が式 (2) 出荷が 1 つ以上ある (納品書を発行したかどうかは見ない)
    (3) 明細を出し終えた出荷の印 (finishes_line) の付いた出荷が無い (4) 終わっている = 受注が取消か、出荷の合計が数量以上か、受注の明細が完了 (RULES.md R25)
    (5) 発行の請求書に、この明細の一式の行 (出荷を指さない明細) がまだ無い (6) 受注の明細に「請求しない」の印 (no_bill) が無い
    (人が請求しないと決めた明細を一式の表から外す = 作者の決め。include_no_bill が真なら (6) を見ない = 作り直しの写しの見方)。
    customer を渡せば、その得意先の明細だけ。
    (2) は R09 (請求書の明細は、出荷済みの物だけ。「出荷済み」は出荷の記録がある出荷。本人の決め 11-11 10:1x) を一式の行にも当てるため。
    出荷の無い取消の一式 (何も納めていない) は請求の元が無いので出さない。(3) は印の付いた出荷が一式の全額を持つので、二重に請求しないため。
    (4) はまだ出荷を続ける明細を出さないため。
    並びの明細に、出荷の合計 (shipped_total) と最後の出荷日 (last_shipped_at) を付ける (lump_facts が読む)。
    受注の明細の表は出荷の外部キーの先として引く (orders を import しない)。出荷で絞る所は pk__in の副問い合わせで書く
    (多い側の関係を絞りと集計の両方で結ぶと、出荷の合計が倍に数えられることがあるため)。副問い合わせの order_line_id は空にならないので、
    NOT IN に空の値は入らない。
    """
    shipments = _Shipment.objects.values("order_line_id")
    issued_lumps = InvoiceLine.objects.filter(invoice__status=Invoice.STATUS_ISSUED, shipment__isnull=True).values(
        "order_line_id"
    )
    queryset = (
        _OrderLine.objects.filter(price_basis=PRICE_BASIS_LUMP, pk__in=shipments)
        .exclude(pk__in=shipments.filter(finishes_line=True))
        .exclude(pk__in=issued_lumps)
    )
    if not include_no_bill:
        queryset = queryset.filter(no_bill=False)
    if customer is not None:
        queryset = queryset.filter(order__customer=customer)
    queryset = (
        queryset.annotate(shipped_total=Sum("shipments__qty"), last_shipped_at=Max("shipments__shipped_at"))
        .select_related("order__customer", "item")
        .order_by("order__order_no", "line_no")
    )
    return [
        line for line in queryset if line.order.status == _Order.STATUS_CANCELLED or line.completed or line.shipped_total >= line.qty
    ]


def lumps_shipped_after(customer=None):
    """発行の請求書に一式の行がある受注の明細のうち、出荷の合計がその一式の行の数量より多い物の (受注の明細, その一式の行) の並び
    (読むだけ。並びは受注番号・行番号。問い合わせ 2 回)。請求書の新規登録の黄の表が「一式の行 IV-… の後に出荷あり」の行で出す (作者の決め)。

    受注の明細に「請求しない」の印 (no_bill) がある明細は除く。customer を渡せば、その得意先の明細だけ。
    1 つの明細に発行の一式の行が 2 行あれば (R09 で止まるので無い見込み)、pk の小さい方。
    「後の出荷」を、一式の行の数量より出荷の合計が多いことで見る訳: 一式の行の数量は、作った時のその明細の出荷の合計の写し (lump_facts) なので、
    後に増えた分だけが後の出荷になる。一式の行で請求した後に前の出荷を直すと、出荷の合計が一式の行の数量より少なくなることがある
    (止めずに検査の層の R27 で知らせる。本人の決め 11-11 12:5x〜13:0x)。列を足さずに見られる。
    読むだけで断らない。止めない (作者の決め)。一式の行の請求書を取り消すと、この並びから外れ、明細は終わっていれば unbilled_lumps の並びに戻る (金額を決め直して載せる)。
    """
    rows = InvoiceLine.objects.filter(
        invoice__status=Invoice.STATUS_ISSUED, shipment__isnull=True, order_line__no_bill=False
    ).select_related("invoice", "order_line__order__customer", "order_line__item")
    if customer is not None:
        rows = rows.filter(order_line__order__customer=customer)
    first = {}
    for row in rows.order_by("pk"):
        first.setdefault(row.order_line_id, row)
    if not first:
        return []
    totals = dict(
        _Shipment.objects.filter(order_line_id__in=list(first))
        .order_by()
        .values("order_line_id")
        .annotate(total=Sum("qty"))
        .values_list("order_line_id", "total")
    )
    found = [(row.order_line, row) for pk, row in first.items() if (totals.get(pk) or _ZERO) > row.qty]
    found.sort(key=lambda pair: (pair[0].order.order_no, pair[0].line_no))
    return found


def changed_lines(invoice):
    """請求した時の数量と今の出荷の数量が違う明細の (明細, 請求した時の数量, 今の数量) の並び (読むだけ。明細の並びのまま。問い合わせは 2 回まで)。

    出荷の行は、明細の数量 (請求した時の出荷の数量の写し) と出荷の今の数量を比べる。一式の行は、明細の数量 (請求した時のその受注の明細の
    出荷の合計の写し = lump_facts) と、その受注の明細の今の出荷の合計を比べる (出荷が 1 つも無くなった明細の今の数量は 0)。
    取消の請求書は空の並び (もう請求していないため)。
    請求済みの出荷を直すか消しても止めない (直す前に検査の層の R27 が知らせる。本人の決め 11-11 12:5x〜13:0x) ので、直した後の食い違いを
    請求書の側から読む口。請求書の詳細の印 (2 段目) が使う。問い合わせは、明細と出荷で 1 回 (select_related)、
    一式の行の受注の明細ごとの出荷の合計で 1 回 (一式の行が無ければ引かない)。
    """
    if invoice.status == Invoice.STATUS_CANCELLED:
        return []
    lines = list(InvoiceLine.objects.filter(invoice=invoice).select_related("shipment").order_by("pk"))
    lump_pks = {line.order_line_id for line in lines if line.shipment_id is None}
    totals = {}
    if lump_pks:
        totals = dict(
            _Shipment.objects.filter(order_line_id__in=lump_pks)
            .order_by()
            .values("order_line_id")
            .annotate(total=Sum("qty"))
            .values_list("order_line_id", "total")
        )
    found = []
    for line in lines:
        current = line.shipment.qty if line.shipment_id is not None else (totals.get(line.order_line_id) or _ZERO)
        if current != line.qty:
            found.append((line, line.qty, current))
    return found


def lump_facts(order_line):
    """一式の受注の明細の請求の材料 (読むだけ)。{"shipped_qty", "last_shipped_at", "full_amount", "shipped_amount"}。

    shipped_qty = 出荷の合計、last_shipped_at = 最後の出荷日、full_amount = 一式の全額 (masters.services.line_amount(得意先, 数量, 単価, 式))、
    shipped_amount = 出した分 = round_amount(得意先, 単価 × 出荷の合計 ÷ 数量) (出荷の合計が数量以上なら一式の全額)。
    unbilled_lumps が足した shipped_total・last_shipped_at があれば読み、無ければ出荷から集める。
    出した分は人が金額を決めるときの目安で、保存の決まりではない (作者の決めの「一式の全額か出した分だけの金額か」)。丸めは round_amount (R03)。
    訳: 人が一式の金額を決めるときの目安 (全額と出した分) を、どの画面でも同じ値にするため 1 か所で数える。出所 = 作者の決め・RULES.md R03 (丸め)。出した分を数量の割合で数えるのは AI の案。
    変えてよい条件 = 作者が目安の数え方を決め直したとき (保存の決まりではないので、変えても出した請求書の金額は変わらない)。
    """
    shipped_qty = getattr(order_line, "shipped_total", None)
    last_shipped_at = getattr(order_line, "last_shipped_at", None)
    if shipped_qty is None:
        totals = _Shipment.objects.filter(order_line=order_line).aggregate(
            shipped_total=Sum("qty"), last_shipped_at=Max("shipped_at")
        )
        shipped_qty = totals["shipped_total"] or _ZERO
        last_shipped_at = totals["last_shipped_at"]
    customer = order_line.order.customer
    full_amount = line_amount(customer, order_line.qty, order_line.unit_price, PRICE_BASIS_LUMP)
    if shipped_qty >= order_line.qty:
        shipped_amount = full_amount
    else:
        shipped_amount = round_amount(customer, order_line.unit_price * shipped_qty / order_line.qty)
    return {
        "shipped_qty": shipped_qty,
        "last_shipped_at": last_shipped_at,
        "full_amount": full_amount,
        "shipped_amount": shipped_amount,
    }


def invoice_period(customer, shipments, issued_on=None):
    """請求書の期間 (読むだけ)。(期間の初め, 期間の終わり) の日付の組。shipments は請求書に入れる出荷。

    締日でまとめる得意先: 最後の出荷日を含む締め期間 = closing_period(customer, 最後の出荷日)。
    都度の得意先: 最初の出荷日〜最後の出荷日 (都度は締めないので、期間は出荷の日)。
    出荷が無い (一式の行だけの請求書) ときは発行日 issued_on で決める: 締日でまとめる得意先は closing_period(customer, 発行日)、
    都度の得意先は (発行日, 発行日) (作者の決め。一式の行は期間を動かさず、取引の日は一式の行の最後の出荷日で行に残す)。
    create_invoice と作り直し (reissue_invoice) が同じ関数を通る (期間を決める所を 2 か所に持たない)。
    """
    days = [shipment.shipped_at for shipment in shipments]
    if not days:
        if customer.billing_method == _Partner.BILLING_PER_DELIVERY:
            return issued_on, issued_on
        return closing_period(customer, issued_on)
    if customer.billing_method == _Partner.BILLING_PER_DELIVERY:
        return min(days), max(days)
    return closing_period(customer, max(days))


def closing_period(customer, day):
    """day を含む取引先の締め期間 (得意先の請求書と、協力企業からの仕入明細 = purchasing の create_self_invoice が使う。読むだけ)。(期間の初め, 期間の終わり) の日付の組。

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
    """支払期日 (得意先の請求書と、協力企業からの仕入明細 = purchasing の create_self_invoice が使う。読むだけ)。base_date は期間の終わり (締日でまとめる得意先は締日、都度の得意先は納品の日)。

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


def months_later(day, count):
    """day の count か月後の同じ日 (その月に同じ日が無ければ月末) を返す。読むだけ。count は 0 以上の整数。

    締め期間と支払期日と同じ月の数え方を、ほかの部品 (purchasing の 60 日の知らせと masters の取引先の保存の知らせ) から使う口。
    月の数え方を purchasing と masters に写さないため。式は _add_months と _day_in_month のまま。
    例: 2027-01-31 の 1 か月後は 2027-02-28 (月末)、2026-08-31 の 1 か月後は 2026-09-30、2026-07-01 の 2 か月後は 2026-09-01。
    """
    year, month = _add_months(day.year, day.month, count)
    return date(year, month, _day_in_month(year, month, day.day))


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
    (空なら会社の値。会社の既定は切捨。作者の決め)。金額の端数の扱い (rounding) は読まない。
    取引先と会社の消費税の端数の扱い (tax_mode) も読まない (選べる値はトータルだけ。作者の決め)。
    確かめた資料: 中小企業共通 EDI ガイドブック 1.1。国税庁の資料と条文は未読。
    訳: 適格請求書の消費税額を、税率ごとの合計に 1 回だけ計算する法令の決まりに合わせるため。出所 = 法令 (インボイス制度。作者の決め。確かめた資料は上の 1 つ)。
    変えてよい条件 = 率が増えたら、明細の率で分けた合計ごとに同じ計算を足す。明細ごとに丸めて足す形には、法令が変わったときだけ戻す。
    """
    return round_tax(customer, sum(amounts, _ZERO) * TAX_RATE)


def issued_invoices_overlapping(customer, period_from, period_to):
    """取消でない請求書のうち、請求書の期間が period_from〜period_to と重なる物 (読むだけ)。並びは期間の終わり・番号。

    受けた仕入明細の詳細の「請求書と比べる」の枠 (connect) が、仕入明細の期間と同じ得意先の請求書を並べて見るために呼ぶ。止めず、金額を書き換えない。
    """
    return (
        Invoice.objects.filter(customer=customer, period_from__lte=period_to, period_to__gte=period_from)
        .exclude(status=Invoice.STATUS_CANCELLED)
        .order_by("period_to", "invoice_no")
    )


def consumption_tax(partner, amounts):
    """消費税の計算の 1 か所 (_tax) を、ほかの部品 (purchasing の仕入明細) から呼ぶ口。読むだけ。

    インボイス制度では消費税は税率ごとの合計に 1 回だけ計算する (作者の決め)。請求書と仕入明細で計算が違うと、同じ取引で相手と金額が合わないため、式を写さない。
    """
    return _tax(partner, amounts)


def consumption_tax_rate():
    """消費税の率 (百分率の Decimal。今は 10) を、ほかの部品から読む口。率は TAX_RATE の 1 か所 (写さない)。

    インボイス制度では仕入明細書にも適用税率を書く (仕入明細書の記載事項は適格請求書と同じ)。
    """
    return TAX_RATE * 100
