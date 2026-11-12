"""shipping の公開する関数 (部品の公開する関数の設計の決め)。画面とほかの部品は、この関数だけを通して出荷と納品書を書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (services の書き方の設計の決め)。
- user は記録のために受け、変更の履歴 (core.services.record_history。作者の決めの伝票の「出荷」「納品書」) に書く。書く前に core.history.saved_state で前を取り、
  書いた後に同じ transaction.atomic の中で残す (止まって戻ったら履歴も残らない)。納品書にまとめた出荷は、出荷の詳細の「履歴」にも納品書の前と後が残る。
  出荷の知らせの列 (番号の uuid・送った日時・相手の返事) は core が残さない列なので、assign_notice_number と record_notice_response は残さない。
- 中心の決まりに当たったら RuleViolation(code, message)。message は RULES.md の番号の文と、次にすること。
- 分納を許さない設定 (partial_shipment が偽) の違反も RuleViolation で、code は設定の画面の名前「分納」。
- 決まりの番号の無い入力の誤り (出荷の一覧が空) は日本語の ValueError。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- 受注より多い出荷のポカよけは検査の層 (seizou.core.checks) が services の前に見る。
  ここでは止めず、残を超える数もそのまま保存する (現物優先)。
- 出荷済みの数と残の数は orders.services の関数で出す (検査の層と同じ関数)。
  集計の前に、受注の明細の行を select_for_update でロックする (services の書き方の設計の決め)。
- 受注 (Order・OrderLine) は書かない。出荷を作った後、同じトランザクションの中で orders.services.refresh_status を呼び、受注の状態を進める。
  「残りは出さずに完了にする」出荷 (ship の close_rest) は、同じトランザクションの中で orders.services.mark_completed を呼ぶ (完了の印を書くのは orders)。
- 納品書を出した後の出荷は、cancel_delivery_note で納品書を取り消してから update_shipment で直す (R08)。
  納品書は出荷の記録を刷った物なので、請求済みの出荷 (発行の請求書に入った出荷・受注の明細に発行の請求書の一式の行がある出荷・
  請求書の外で受け取った入金を記録した出荷) のある納品書も取り消せる (本人の決め 11-11 10:1x・13:0x)。請求済みの事は、
  検査の層の R27 が取り消す前に知らせる (文は billed_notice の 1 か所。請求書の明細は billing を import せず、出荷の逆の関係で引く)。
- 打ち間違えた出荷は delete_shipment で消す (断る訳は delete_refusal の 1 か所。本人の決め 11-08 00:23・00:25)。
- 請求済みの出荷 (billed_by) の直しと削除と、その出荷の入った納品書の取消は断らない。検査の層の R27 が services の前に知らせる (本人の決め 11-11 12:5x〜13:0x)。
  請求書に入った出荷を消しても、請求書の明細は出荷番号と出荷日の写しを持って残る (依頼書 N403c)。
  請求書の明細の一式の行は、最後の出荷日 (last_shipped_at) がある行で見分ける (出荷を消した出荷の行も出荷が空のため)。
- 出荷の後、受信した注文で作った受注の出荷なら connect.services.send_shipping_notice で出荷の知らせを相手へ送る (HTTP は atomic の外 = R17)。
  出荷の知らせの列 (notice_*) は assign_notice_number・record_notice_response が書く。update_shipment は送り直さない。
- 一括出荷 (ship_lines) は、選んだ明細を 1 つの atomic で ship と同じ決まりに通し、出荷の知らせは全部を保存した後に送る (R17)。
- 出荷 1 つの金額は amount_of_shipment の 1 か所 (一式の明細は明細を出し終えた出荷 finishes_line で全額、ほかは 0。作者の決め)。
"""

from __future__ import annotations

from decimal import Decimal
import uuid

from django.core.exceptions import FieldDoesNotExist
from django.db import transaction
from django.utils import timezone

from seizou.core import history
from seizou.core.conf import setting
from seizou.core.exceptions import RuleViolation
from seizou.core.services import next_number, record_history, record_history_deleted
from seizou.core.services import keep_report_copy, note_copy_failed
from seizou.masters.services import delivery_amount, reaches_line_qty
from seizou.orders.services import mark_completed, refresh_status, remaining_qty, shipped_qty

from .models import DeliveryNote, Shipment

_R02_MESSAGE = "保存できません。数量と単価は 0 より大きい。0 より大きい数を入れてください。"
_R05_RULE = "取消した受注には、出荷も指示書も作れない。"
_R08_RULE = "納品書の数量は出荷の数量と一致する。納品書を出した後の出荷は、納品書を取り消してから直す。"
MESSAGE_NOTICE_SEED = "作り物の種の読み込みなので出荷の知らせを送っていません (相手の記録は相手の種が持つ)。"

# 受注の明細の表は、出荷の外部キーの先として引く (orders の models を import しない)。
_OrderLine = Shipment._meta.get_field("order_line").related_model


def _dec(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _qty_text(value) -> str:
    """知らせに出す数の字。末尾の 0 を落とす (10.00 → 10、4.50 → 4.5)。core.checks.builtin._num と同じ形。

    訳: 知らせに出す数の末尾の 0 を落とし、_num と同じ字にするため。出所 = 未確認 (_num は本人の初見テスト 2 回目の所見の直し = docs/nights/20261103.md。本人の言葉は記録に無い)。
    変えてよい条件 = Issue「決まりの提案」でこの訳に対して議論してから。
    """
    dec = _dec(value).normalize()
    if dec == dec.to_integral_value():
        return str(dec.quantize(Decimal(1)))
    return format(dec, "f")


def _record_shipment(order_line, qty, shipped_at, user, note, *, close_rest=False):
    """出荷の保存の部分 (ship と ship_lines が atomic の中で呼ぶ)。明細に行の錠を掛け、R05・R02・分納を見て、出荷を作り、受注の状態を直す。

    返す物は (出荷, 錠を掛けた明細)。HTTP は送らない (送るのは _after_ship。atomic の外 = R17)。
    明細を出し終えた出荷の印 (finishes_line) も付ける (入れた順で、明細の出荷の合計を数量の手前から数量以上にした最初の出荷。印の付いた出荷が既にあるか、発行の請求書に一式の行がある明細には付けない)。
    close_rest が真 (残りは出さずに完了にする出荷。ship が渡す) なら、分納の検査をしない。
    訳: 取消した受注に出荷を作らず (R05)、0 以下の数を出荷にせず (R02)、分納を許さない会社では残りの数と違う出荷を止めるため。出所 = RULES.md R05・R02 と 会社の設定 partial_shipment (既定は許す)。
    残りは出さずに完了にする出荷は分納に数えない (この出荷の後に残りを出さないので、分けて出すことにならない。出所 = 本人の決め 11-11 08:4x・09:3x を受けた作者の決め。開発の記録 docs/nights/20261111/N400.md)。
    変えてよい条件 = R05・R02 は変えない (R は廃止で残す)。分納は会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """
    qty = _dec(qty)
    locked = _OrderLine.objects.select_for_update().get(pk=order_line.pk)
    if locked.order.status == "cancelled":
        raise RuleViolation(
            "R05", f"保存できません。{_R05_RULE}取消していない受注の明細を選んでください。"
        )
    if not qty > 0:
        raise RuleViolation("R02", _R02_MESSAGE)
    if not setting("partial_shipment") and not close_rest:
        remaining = remaining_qty(locked)
        if qty != remaining:
            raise RuleViolation(
                "分納",
                f"保存できません。この会社は分納を許していません (設定「分納」)。残りの数 {_qty_text(remaining)} をまとめて出荷してください。",
            )
    # 明細を出し終えた出荷の印 (作者の決め)。明細の行の錠の中で数えるので、2 人が同じ明細を同時に出荷しても印は 1 つ。
    done = shipped_qty(locked)
    finished = Shipment.objects.filter(order_line=locked, finishes_line=True).exists() or _has_issued_lump_line(locked)
    shipment = Shipment.objects.create(
        shipment_no=next_number("shipment"),
        order_line=locked,
        qty=qty,
        shipped_at=shipped_at,
        note=note,
        finishes_line=not finished and reaches_line_qty(locked.qty, done, qty),
    )
    record_history(shipment, user)
    refresh_status(locked.order, user)
    return shipment, locked


def _after_ship(shipment, order, user, send):
    """出荷の後の知らせ。受信した注文で作った受注の出荷なら、出荷の知らせを相手へ送る (atomic の外で呼ぶ = R17)。"""
    if _is_edi_order(order):
        if not send:
            return record_notice_response(shipment, MESSAGE_NOTICE_SEED, user)
        from seizou.connect import services as connect_services

        return connect_services.send_shipping_notice(shipment, user)
    return shipment


def ship(order_line, qty, shipped_at, user, *, note="", send=True, close_rest=False):
    """受注の明細を qty だけ出荷する。出荷番号は core の連番が付ける。

    send が偽なら、受信した注文で作った受注の出荷でも出荷の知らせを送らず (番号も付けず)、出荷の返事の欄に MESSAGE_NOTICE_SEED を書く。
    close_rest が真なら「残りは出さずに完了にする」出荷: 同じ atomic の中で出荷を作った後に、残 (orders.services.remaining_qty。RULES.md R25) が
    0 より大きければ orders.services.mark_completed を呼ぶ (この出荷で数量に届いたら何もしない)。分納を許さない会社でも分納の検査で断らない
    (_record_shipment)。足りないまま完了にした時は、返す出荷に saved_message「出荷を保存しました。<mark_completed の字>」を付ける (表に書かない)。
    完了の明細への出荷は断らない (現物優先。候補の口が残の 0 より大きい明細だけを出す)。
    """
    with transaction.atomic():
        shipment, locked = _record_shipment(order_line, qty, shipped_at, user, note, close_rest=close_rest)
        closed = mark_completed(locked, user) if close_rest and remaining_qty(locked) > 0 else None
    # 出荷の知らせを相手へ送る。HTTP は atomic の外 (R17)。shipping と connect の循環を避けるため関数の中で import する。
    result = _after_ship(shipment, locked.order, user, send)
    if closed is not None:
        # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
        result.saved_message = f"出荷を保存しました。{closed.saved_message}"
    return result


class ShippedLines(list):
    """一括出荷の返す物 (知らせの後の出荷の list)。入力の土台は saved_message を知らせの字にする。

    1 件の出荷の返す物 (出荷) は saved_level を持たないので、ここも持たない (知らせは緑)。
    """

    @property
    def saved_message(self) -> str:
        return f"{len(self)} 件の出荷を登録しました。"


def ship_lines(lines, shipped_at, user, *, note=""):
    """選んだ受注の明細を、同じ出荷日でまとめて出荷する (一括出荷。注文残の盤の「選んだ行の操作」。開発の記録 docs/nights/20261104/N124.md)。

    lines は (受注の明細, 数量) の組の list。1 行ずつ ship と同じ決まり (R05・R02・分納) を通し、どれか 1 行が断られたら
    全部を保存しない (1 つの atomic)。断りの文の頭に受注番号と行の番号を付ける。
    出荷の知らせ (受信した注文で作った受注) は、全部を保存した後に atomic の外で 1 件ずつ送る (R17)。
    訳: 一括出荷でも 1 件の出荷と同じ決まり (R05・R02・分納) を 1 行ずつ通すため (どれか 1 行が断られたら全部を保存しない)。出所 = RULES.md R05・R02 と 会社の設定 partial_shipment (1 行ずつの見方は _record_shipment)。
    変えてよい条件 = R05・R02 は変えない (R は廃止で残す)。分納は会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """
    if not lines:
        raise ValueError("出荷できません。出荷する明細がありません。出荷する明細を 1 行以上選んでください。")
    with transaction.atomic():
        recorded = []
        for line, qty in lines:
            try:
                recorded.append(_record_shipment(line, qty, shipped_at, user, note))
            except RuleViolation as exc:
                raise RuleViolation(exc.code, f"出荷できません。{line.order.order_no} の {line.line_no} 行目: {exc.message.removeprefix('保存できません。')}") from exc
    return ShippedLines(_after_ship(shipment, locked.order, user, True) for shipment, locked in recorded)


def create_delivery_note(customer, shipments, user, *, issued_on=None):
    """得意先の出荷をまとめて納品書を作る。納品書番号は core の連番が付ける。

    納品書は数量の写しを持たない。結ばれた出荷は delivery_note.shipments.all() で引く。
    issued_on (発行日) は無ければ今日。種の読み込み (core.demo) が出荷日を渡す。
    発行の時の帳票の写しを残す (R34)。描けなければ、返す納品書の saved_note に字を足して黄にする (core.services.keep_report_copy・note_copy_failed)。
    shipping.reports は頭で .services を import するので、関数の中で import する (輪を避ける)。
    訳: 納品書を得意先ごとに作り、発行の納品書に入っている出荷を別の納品書に入れないため (納品書の数量は出荷の数量と一致する)。出所 = RULES.md R08 (作者の会期前の決まりの表 (2026-11-01) の理由の列「伝票の整合」)。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    pks = sorted({shipment.pk for shipment in shipments})
    if not pks:
        raise ValueError("発行できません。出荷がありません。納品書にまとめる出荷を 1 つ以上選んでください。")
    with transaction.atomic():
        locked = list(Shipment.objects.select_for_update().filter(pk__in=pks).order_by("pk"))
        for shipment in locked:
            if shipment.order_line.order.customer_id != customer.pk:
                raise RuleViolation(
                    "R08",
                    f"発行できません。{_R08_RULE}納品書は得意先ごとに作るので、"
                    f"出荷 {shipment.shipment_no} を外してください。",
                )
            current = shipment.delivery_note
            if current is not None and current.status == DeliveryNote.STATUS_ISSUED:
                raise RuleViolation(
                    "R08",
                    f"発行できません。{_R08_RULE}出荷 {shipment.shipment_no} は納品書 {current.note_no} に入っているので、"
                    "外してください。",
                )
        note = DeliveryNote.objects.create(
            note_no=next_number("delivery_note"),
            customer=customer,
            issued_on=issued_on or timezone.localdate(),
        )
        record_history(note, user)
        for shipment in locked:
            before = history.saved_state(shipment)
            shipment.delivery_note = note
            shipment.save(update_fields=["delivery_note"])
            record_history(shipment, user, before=before)
        from .reports import DELIVERY_NOTE_TEMPLATE, delivery_note_context

        if keep_report_copy(note, user, title="納品書", template=DELIVERY_NOTE_TEMPLATE, context=delivery_note_context(note)) is None:
            note_copy_failed(note)
        return note


def _has_issued_lump_line(order_line):
    """受注の明細に、発行の請求書の一式の行 (最後の出荷日がある請求書の明細) があるか (読むだけ。問い合わせ 1 回)。

    請求書の部品が無いか、請求書の明細が受注の明細を指していなければ偽。請求書の明細の表は受注の明細の逆の関係
    invoice_lines の先として引き、「発行」の値は明細の外部キー invoice の先の表の STATUS_ISSUED から読む
    (billing を import しない。invoiced_shipments と同じ読み方)。
    一式の行は最後の出荷日 (last_shipped_at) がある行で見る (billing.models.InvoiceLine.is_lump。出荷を消した出荷の行も出荷が空なので、出荷が空かでは見ない)。
    訳: 一式の行で人が金額を決めて載せた明細に、後の出荷で一式の全額をもう一度自動で載せないため。出所 = 作者の決め。
    変えてよい条件 = 作者が、一式の行を載せた後の出荷の金額の決め方を決め直したとき。
    """
    try:
        lines = _OrderLine._meta.get_field("invoice_lines").related_model
    except FieldDoesNotExist:
        return False
    issued = lines._meta.get_field("invoice").related_model.STATUS_ISSUED
    return lines._default_manager.filter(order_line=order_line, last_shipped_at__isnull=False, invoice__status=issued).exists()


def invoiced_shipments(note):
    """納品書の出荷のうち、発行の請求書に入っている物の (出荷, 請求書) の並び (出荷の pk の順。読むだけ。問い合わせ 1 回)。

    請求書の部品が無ければ空の並び。請求書の明細の表は出荷の逆の関係 invoice_lines の先として引き、「発行」の値は
    明細の外部キー invoice の先の表の STATUS_ISSUED から読む (billing を import しない)。
    1 つの出荷が発行の請求書の明細に 2 行あれば、明細の pk の小さい方の請求書だけを返す。
    """
    try:
        lines = Shipment._meta.get_field("invoice_lines").related_model
    except FieldDoesNotExist:
        return []
    issued = lines._meta.get_field("invoice").related_model.STATUS_ISSUED
    found = {}
    rows = (
        lines._default_manager.select_related("invoice", "shipment")
        .filter(shipment__delivery_note=note, invoice__status=issued)
        .order_by("shipment_id", "pk")
    )
    for line in rows:
        found.setdefault(line.shipment_id, (line.shipment, line.invoice))
    return list(found.values())


def lump_billed_shipments(note):
    """納品書の出荷のうち、受注の明細に発行の請求書の一式の行 (最後の出荷日がある請求書の明細) がある物の (出荷, 請求書) の並び
    (出荷の pk の順。読むだけ。問い合わせ 2 回)。発行の請求書に出荷の行で入った出荷 (invoiced_shipments) は除く。

    請求書の部品が無ければ空の並び。読み方は invoiced_shipments・_has_issued_lump_line と同じ (billing を import しない)。
    同じ受注の明細の、出荷を消した出荷の行 (出荷が空で、最後の出荷日も空) は一式の行と数えない。
    1 つの受注の明細に発行の一式の行が 2 行あれば、明細の pk の小さい方の請求書を返す (作者の決め)。
    """
    try:
        lines = Shipment._meta.get_field("invoice_lines").related_model
    except FieldDoesNotExist:
        return []
    issued = lines._meta.get_field("invoice").related_model.STATUS_ISSUED
    shipments = list(note.shipments.exclude(invoice_lines__invoice__status=issued).order_by("pk"))
    if not shipments:
        return []
    found = {}
    rows = (
        lines._default_manager.select_related("invoice")
        .filter(last_shipped_at__isnull=False, invoice__status=issued, order_line_id__in={s.order_line_id for s in shipments})
        .order_by("pk")
    )
    for line in rows:
        found.setdefault(line.order_line_id, line.invoice)
    return [(s, found[s.order_line_id]) for s in shipments if s.order_line_id in found]


def outside_paid_shipments(note):
    """納品書の出荷のうち、請求書の外で受け取った入金 (取消でない) を記録した物の (出荷, 入金) の並び
    (出荷の pk の順。読むだけ。問い合わせ 1 回)。1 つの出荷に取消でない入金が 2 件あれば、入金の pk の小さい方を返す。

    入金の部品が無ければ空の並び。入金の表は出荷の逆の関係 payments の先として引き、「記録」の値はその表の
    STATUS_RECORDED から読む (billing を import しない。invoiced_shipments と同じ読み方。作者の決め)。
    """
    try:
        payments = Shipment._meta.get_field("payments").related_model
    except FieldDoesNotExist:
        return []
    found = {}
    rows = (
        payments._default_manager.select_related("shipment")
        .filter(shipment__delivery_note=note, status=payments.STATUS_RECORDED)
        .order_by("shipment_id", "pk")
    )
    for payment in rows:
        found.setdefault(payment.shipment_id, (payment.shipment, payment))
    return list(found.values())


def billed_by(shipment):
    """出荷 1 つが請求済みなら、何で請求済みかの (種類, 記録) の組を返す (読むだけ。問い合わせ 3 回まで)。請求済みでなければ None。

    種類と記録 (見る順は billed_notice の文の順と同じ。最初に当たった 1 つを返す):
    "line" = 発行の請求書に出荷の行で入っている (記録はその請求書の明細。明細の pk の小さい方)。
    "lump" = 受注の明細に発行の請求書の一式の行 (最後の出荷日がある明細。出荷を消した出荷の行は数えない) がある (記録はその一式の行。明細の pk の小さい方)。
    "outside" = 請求書の外で受け取った入金 (取消でない) を記録している (記録はその入金。入金の pk の小さい方)。
    請求書の明細は請求書を一緒に引く (呼ぶ側が請求書の番号を字にするため)。取消の請求書と取消の入金は見ない。
    請求書の部品が無ければ None。読み方は invoiced_shipments・_has_issued_lump_line・outside_paid_shipments と同じ
    (請求書の明細と入金の表は出荷の逆の関係 invoice_lines・payments の先として引き、billing を import しない)。
    検査の層の R27 (core.checks.builtin。請求済みの出荷を直すか消そうとしたら知らせる) が、出荷の編集と削除の前に読む。
    """
    try:
        lines = Shipment._meta.get_field("invoice_lines").related_model
    except FieldDoesNotExist:
        return None
    issued = lines._meta.get_field("invoice").related_model.STATUS_ISSUED
    rows = lines._default_manager.select_related("invoice").filter(invoice__status=issued).order_by("pk")
    row = rows.filter(shipment=shipment).first()
    if row is not None:
        return "line", row
    row = rows.filter(order_line_id=shipment.order_line_id, last_shipped_at__isnull=False).first()
    if row is not None:
        return "lump", row
    try:
        payments = Shipment._meta.get_field("payments").related_model
    except FieldDoesNotExist:
        return None
    paid = payments._default_manager.filter(shipment=shipment, status=payments.STATUS_RECORDED).order_by("pk").first()
    if paid is not None:
        return "outside", paid
    return None


def billed_notice(note, *, found=None, lumps=None, outside=None) -> str:
    """納品書に請求済みの出荷があるときの知らせの文 (読むだけ)。請求済みの出荷が無ければ空の字。金額と入金の件数は書かない。

    見る順は 出荷の行の請求書 (invoiced_shipments) → 一式の行の請求書 (lump_billed_shipments) → 外の入金 (outside_paid_shipments)
    (billed_by と同じ順。最初に当たった種類の、出荷の pk の最も小さい 1 つで文を作る)。
    出荷の行:「納品書 <番号> の出荷 <番号> は請求書 <番号> に入っています。納品書を取り消しても、請求書は変わりません。」
    一式の行:「納品書 <番号> の出荷 <番号> の受注の明細は、請求書 <番号> の一式の行で請求済みです。納品書を取り消しても、請求書は変わりません。」
    外の入金:「納品書 <番号> の出荷 <番号> には、請求書の外で受け取った入金が記録されています。納品書を取り消しても、入金の記録は変わりません。」
    found・lumps・outside = 呼ぶ側が引いた invoiced_shipments(note)・lump_billed_shipments(note)・outside_paid_shipments(note)
    の並び (取消の確かめのカードの tag が渡す)。省けばここで引く。納品書の状態は見ない。
    検査の層の R27 (納品書の取消。core.checks.builtin) と取消の確かめのカード (shipping_tags の deliverynote_billing) が同じ文を使う。
    """
    if found is None:
        found = invoiced_shipments(note)
    if found:
        shipment, invoice = found[0]
        return (
            f"納品書 {note.note_no} の出荷 {shipment.shipment_no} は請求書 {invoice.invoice_no} に入っています。"
            "納品書を取り消しても、請求書は変わりません。"
        )
    if lumps is None:
        lumps = lump_billed_shipments(note)
    if lumps:
        shipment, invoice = lumps[0]
        return (
            f"納品書 {note.note_no} の出荷 {shipment.shipment_no} の受注の明細は、請求書 {invoice.invoice_no} の一式の行で請求済みです。"
            "納品書を取り消しても、請求書は変わりません。"
        )
    if outside is None:
        outside = outside_paid_shipments(note)
    if outside:
        shipment, _payment = outside[0]
        return (
            f"納品書 {note.note_no} の出荷 {shipment.shipment_no} には、請求書の外で受け取った入金が記録されています。"
            "納品書を取り消しても、入金の記録は変わりません。"
        )
    return ""


def cancel_delivery_note(note, user):
    """発行の納品書を取消にする。入っていた出荷は納品書を指したまま残す (取消の納品書の詳細に出る)。

    取消の納品書の出荷も請求できる (納品書は出荷の記録を刷った物。本人の決め 11-11 10:1x)。直してから新しい納品書にまとめ直せる。
    断るのは、取り消し済みの納品書 (ValueError) だけ。請求済みの出荷 (発行の請求書に入った出荷・一式の行で請求した明細の出荷・
    請求書の外で受け取った入金を記録した出荷) のある納品書も取り消し、請求書と入金は触らない (知らせは検査の層の R27。文は billed_notice)。
    訳: 納品書は出荷の記録を刷った物で、取り消して作り直しても請求書の明細 (写し) は変わらないので、請求済みの出荷のある納品書も取り消せる。請求済みの事は検査の層の R27 が知らせる。出所 = 本人の決め 11-11 10:1x・13:0x。変えてよい条件 = 本人が決め直したとき。
    """
    with transaction.atomic():
        locked = DeliveryNote.objects.select_for_update().get(pk=note.pk)
        if locked.status == DeliveryNote.STATUS_CANCELLED:
            raise ValueError("取り消せません。この納品書は取り消し済みです。納品書の詳細を開き直してください。")
        # 出荷の行をロックする (請求書の作成と同時に走っても、どちらかが待つ)。
        list(Shipment.objects.select_for_update().filter(delivery_note=locked).order_by("pk"))
        before = history.saved_state(locked)
        locked.status = DeliveryNote.STATUS_CANCELLED
        locked.save(update_fields=["status"])
        record_history(locked, user, before=before)
        # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
        locked.saved_message = "取り消しました。"
        return locked


def _has_reverse_rows(model, name, obj) -> bool:
    """obj を指す逆の関係 name の行が 1 行でもあるか (読むだけ。問い合わせ 1 回)。

    逆の関係の先の表は model._meta.get_field(name) で引く (先の部品を import しない。invoiced_shipments と同じ読み方)。
    その部品が入っていなくて関係が無ければ偽。
    """
    try:
        relation = model._meta.get_field(name)
    except FieldDoesNotExist:
        return False
    return relation.related_model._default_manager.filter(**{relation.field.name: obj}).exists()


def delete_refusal(shipment) -> str:
    """出荷を消せない訳の文 (読むだけ)。消せるなら空の字。delete_shipment と削除の確かめのカードが同じ文を使う。

    見る順 (最初に当たった 1 つの文を返す。どの文も「削除できません。」で始め、出荷番号と次にすることを書く):
    (ii) 請求書の外で受け取った入金の記録が指している。入金の状態は問わない (取り消しても行は残り、入金の記録は出荷を持ち主にして
         変更の履歴を残すので、持ち主が消えると履歴の置き場が無くなる。表の外部キーは PROTECT)。
         入金の表は出荷の逆の関係 payments の先として引く (billing を import しない)。
         請求書の明細が指す出荷は断らない (明細は出荷番号と出荷日の写しを持って残り、出荷の外部キーは空になる。知らせるのは検査の層の R27)。
    (i) 発行の納品書に入っている (取消の納品書を指す出荷は断らない)。
    (iii) 取引先につながる: 受信した注文で作った受注の出荷 (_is_edi_order)・出荷の知らせを送った出荷 (notice_sent_at が空でない)・
         受注の明細に検収が 1 件でも届いた出荷 (受注の明細の逆の関係 acceptances。connect を import しない。検収は出荷でなく明細を指すので明細で見る)。
    (iv) 明細を出し終えた出荷 (finishes_line)。
    訳: 金額の書類 (納品書・入金) が指す出荷と、取引先につながる出荷と、明細を出し終えた出荷を消さないため
    (何を見るかの訳は delete_shipment の訳と同じ)。出所 = 本人の決め 11-08 00:23・00:25 (エンタープライズ化の調べを受けた作者の決め)。
    請求書の明細が指す出荷を (ii) から外したのは、請求済みの出荷の直しと削除を止めない本人の決め 11-11 12:5x〜13:0x による
    (請求書の明細は出荷番号と出荷日を写して残す。依頼書 N403c)。請求書の外で受け取った入金が指す出荷は、入金を取り消した後も
    今のまま消せない (本人の決め 11-11 20:1x「それでOK」。入金に出荷番号を写して消せるようにする形は作らない)。
    (ii) を (i) より先に見るのは、(ii) は入金を取り消しても外れないので、納品書を取り消させた後に断らないため。出所 = AI の案 (開発の記録 docs/nights/20261111/N377.md)。
    変えてよい条件 = 本人が決め直したとき (例: 相手につながる出荷の直し方を設計したとき)。
    """
    number = shipment.shipment_no
    if _has_reverse_rows(Shipment, "payments", shipment):
        return (
            f"削除できません。出荷 {number} は請求書の外で受け取った入金の記録が指しています"
            " (入金を取り消しても、記録は出荷を指したまま残ります)。数や日付の誤りは「編集」で直してください。"
        )
    note = shipment.delivery_note
    if note is not None and note.status == DeliveryNote.STATUS_ISSUED:
        return (
            f"削除できません。出荷 {number} は納品書 {note.note_no} に入っています。"
            "先に納品書を取り消してください (納品書を取り消す・作り直すのは、金額を見られる人です)。"
        )
    order = shipment.order_line.order
    if _is_edi_order(order) or shipment.notice_sent_at is not None or _has_reverse_rows(_OrderLine, "acceptances", shipment.order_line):
        return (
            f"削除できません。出荷 {number} は取引先につながる記録 (受信した注文の出荷・送った出荷の知らせ・届いた検収) です。"
            "消しても相手の記録は戻らないので、数の誤りは相手に会話で伝えてください。"
        )
    if shipment.finishes_line:
        return (
            f"削除できません。出荷 {number} は明細を出し終えた出荷です (一式の明細なら、一式の金額がこの出荷に付きます)。"
            "数や日付の誤りは「編集」で直してください。"
        )
    return ""


def update_shipment(shipment, qty, shipped_at, user, *, note=""):
    """出荷の数量と出荷日と備考を直す。出荷番号と受注の明細は変えない。

    発行の納品書に入った出荷は R08 で断る (先に cancel_delivery_note)。
    受注より多い数はここでは止めない (現物優先。検査の層が services の前に見る)。
    請求済みの出荷 (billed_by) は断らず、検査の層の R27 が知らせる (本人の決め 11-11 12:5x〜13:0x)。
    明細を出し終えた出荷の印 (finishes_line) も付ける (入れた順で、明細の出荷の合計を数量の手前から数量以上にした最初の出荷。印の付いた出荷が既にあるか、発行の請求書に一式の行がある明細には付けない)。
    直した出荷の印だけを数え直す (ほかの出荷に付け直さない)。
    訳: 取消した受注の出荷を直さず (R05)、発行の納品書に入った出荷は納品書を取り消してから直し (R08)、0 以下の数にせず (R02)、分納を許さない会社では残りの数と違う数を止めるため。出所 = RULES.md R05・R08・R02 と 会社の設定 partial_shipment (既定は許す)。
    変えてよい条件 = R05・R08・R02 は変えない (R は廃止で残す)。分納は会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """
    qty = _dec(qty)
    with transaction.atomic():
        line = _OrderLine.objects.select_for_update().get(pk=shipment.order_line_id)
        locked = Shipment.objects.select_for_update().get(pk=shipment.pk)
        if line.order.status == "cancelled":
            raise RuleViolation("R05", f"保存できません。{_R05_RULE}この出荷の受注は取り消してあります。")
        current = locked.delivery_note
        if current is not None and current.status == DeliveryNote.STATUS_ISSUED:
            raise RuleViolation(
                "R08",
                f"保存できません。{_R08_RULE}出荷 {locked.shipment_no} は納品書 {current.note_no} に入っているので、"
                "先に納品書を取り消してください。",
            )
        if not qty > 0:
            raise RuleViolation("R02", _R02_MESSAGE)
        if not setting("partial_shipment"):
            remaining = remaining_qty(line) + locked.qty
            if qty != remaining:
                raise RuleViolation(
                    "分納",
                    f"保存できません。この会社は分納を許していません (設定「分納」)。この出荷を除いた残りの数 {_qty_text(remaining)} をまとめて出荷してください。",
                )
        before = history.saved_state(locked)
        # 明細を出し終えた出荷の印を、直した出荷の分だけ数え直す (作者の決め。ほかの出荷の印は触らない)。
        others = shipped_qty(line) - locked.qty
        finished = (
            Shipment.objects.filter(order_line=line, finishes_line=True).exclude(pk=locked.pk).exists()
            or _has_issued_lump_line(line)
        )
        locked.finishes_line = not finished and reaches_line_qty(line.qty, others, qty)
        locked.qty = qty
        locked.shipped_at = shipped_at
        locked.note = note
        locked.save(update_fields=["qty", "shipped_at", "note", "finishes_line"])
        record_history(locked, user, before=before)
        refresh_status(line.order, user)
        return locked


def delete_shipment(shipment, user):
    """打ち間違えた出荷を消す (違う受注の明細へ入れた出荷・二重に入れた出荷)。既に無ければ日本語の ValueError。

    atomic の中で、受注の明細と出荷の行に錠を掛けて取り直す (update_shipment と同じ順)。消せない訳 (delete_refusal) があれば
    何も変えずに ValueError。消せるなら、その時の値を変更の履歴に「消した」で user で残し、行を消し、受注の状態を決め直す
    (refresh_status。取消の受注は変えない)。在庫は出荷を動きの表に書かず集計で引くので、行を消せば出荷の前の数に戻る。
    出荷の知らせは送らない (HTTP を呼ばない)。添付と書式つきのメモは消さない (付けた先の無い行が残る)。
    返す物 (消した出荷。pk は None) に、画面の知らせの字 saved_message (「出荷 <番号> を削除しました。」) を付ける (表に書かない)。
    請求済みの出荷 (billed_by) は断らず、検査の層の R27 が知らせる (本人の決め 11-11 12:5x〜13:0x。delete_refusal が断らない出荷のうち、
    請求書に出荷の行で入った出荷と、一式の行で請求した明細の出荷が当たる)。
    請求書の明細が指す出荷も消せる (明細には出荷番号と出荷日の写しが残る)。検査の層の R27 が知らせる。消すと、指していた請求書の明細の
    出荷の外部キーは空になる (SET_NULL。取消の請求書の明細も同じ)。明細の数量・単価・金額と請求書の合計は変えない。消した事は今のとおり変更の履歴に残る。
    訳: 違う受注の明細へ入れた出荷などの打ち間違いを消し、受注の状態と在庫を出荷の前に戻すため。発行の納品書が指す出荷は、先に納品書を取り消す。
    請求書の外の入金が指す出荷は、取り消しても入金の行が残るので消さず、「編集」で直す (金額の書類を黙って変えない)。
    取引先につながる出荷 (受信した注文の出荷・送った出荷の知らせ・届いた検収) は消しても相手の記録が戻らないので断り、直し方は別に設計する (会期の外)。
    明細を出し終えた出荷 (finishes_line) は一式の明細の金額が付くので断る。添付と書式つきのメモは消さない (付けた先の無い行が残る。扱いは未定)。
    出所 = 本人の決め 11-08 00:23・00:25 (エンタープライズ化の調べを受けた作者の決め)。
    変えてよい条件 = 本人が決め直したとき (例: 相手につながる出荷の直し方を設計したとき)。
    """
    with transaction.atomic():
        line = _OrderLine.objects.select_for_update().get(pk=shipment.order_line_id)
        locked = Shipment.objects.select_for_update().filter(pk=shipment.pk).first()
        if locked is None:
            raise ValueError("削除できません。この出荷は既に消してあります。")
        refusal = delete_refusal(locked)
        if refusal:
            raise ValueError(refusal)
        number = locked.shipment_no
        record_history_deleted(locked, user)
        locked.delete()
        refresh_status(line.order, user)
    # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「削除しました。」の代わりに出す)。表に書かない。
    locked.saved_message = f"出荷 {number} を削除しました。"
    return locked


def amount_of_shipment(shipment, *, partner=None, rounding=None):
    """出荷 1 つの金額 (読むだけ)。masters.services.delivery_amount(得意先, 出荷の数量, 受注の明細の単価, 単価の単位, completes=明細を出し終えた出荷か)。
    partner の既定は受注の得意先 (渡すとそれで丸める。請求書と納品書は得意先を渡す)。rounding は round_amount と同じ (会社の既定を輪の前に 1 回読んで渡す)。
    請求書 (billing.services.shipment_amount)・納品書の帳票 (reports)・ダッシュボードの出荷の金額が読む 1 か所。呼ぶ側は order_line (と得意先) を一緒に引く。

    訳: 一式の明細は全部を出し終えた出荷で一式の全額、それまでの出荷と多い出荷は 0 円にし、同じ出荷の金額を請求書・納品書・ダッシュボードで同じにするため。出所 = 作者の決め。
    変えてよい条件 = 作者が一式の金額を請求に載せる時を決め直したとき (masters.services.delivery_amount と一緒に直す)。
    """
    line = shipment.order_line
    return delivery_amount(
        partner if partner is not None else line.order.customer,
        shipment.qty,
        line.unit_price,
        line.price_basis,
        completes=shipment.finishes_line,
        rounding=rounding,
    )


# --- 出荷の知らせ (出荷を相手の seizou の発注の明細へ送る記録。EDI の 3 段目) ---


def _is_edi_order(order):
    """受注が受信した注文 (seizou どうし) で作った物か。

    入口 (source) を先に見て、手入力と読み取りの受注では問い合わせを起こさない。受信した注文は 1 対 1 の逆の関係
    inbox_message を読むだけ (connect の model を import しない)。無ければ RelatedObjectDoesNotExist
    (AttributeError の子) なので getattr が None を返す。connect の無い会社では属性が無く偽。
    """
    return order.source == order.SOURCE_INBOX and getattr(order, "inbox_message", None) is not None


def assign_notice_number(shipment, user):
    """出荷に出荷の知らせの番号を付けて返す。既にあればそのまま返す (送り直しは 1 回目の番号を使い回す)。

    connect.services.send_shipping_notice が送る前に呼ぶ (届かなくても番号が残る)。返す物は uuid.UUID。
    """
    with transaction.atomic():
        locked = Shipment.objects.select_for_update().get(pk=shipment.pk)
        if locked.notice_message_id is None:
            locked.notice_message_id = uuid.uuid4()
            locked.save(update_fields=["notice_message_id"])
        return locked.notice_message_id


def record_notice_response(shipment, response, user, *, sent=False):
    """出荷の知らせの相手の返事を出荷に書く (500 字で切る)。sent が真なら出荷の知らせを送った日時も書く。

    sent が偽なら送った日時は今の値のまま (消さない)。connect.services.send_shipping_notice が呼ぶ。
    返す物は取り直した Shipment。
    """
    limit = Shipment._meta.get_field("notice_response").max_length
    with transaction.atomic():
        locked = Shipment.objects.select_for_update().get(pk=shipment.pk)
        locked.notice_response = response[:limit]
        fields = ["notice_response"]
        if sent:
            locked.notice_sent_at = timezone.now()
            fields.append("notice_sent_at")
        locked.save(update_fields=fields)
        return locked
