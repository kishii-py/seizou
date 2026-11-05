"""shipping の公開する関数 (01 の 10.4)。画面とほかの部品は、この関数だけを通して出荷と納品書を書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は記録のために受ける。記録の列がまだ無いので、今は受けるだけで書かない。
- 中心の決まりに当たったら RuleViolation(code, message)。message は RULES.md の番号の文と、次にすること。
- 分納を許さない設定 (partial_shipment が偽) の違反も RuleViolation で、code は設定の画面の名前「分納」。
- 決まりの番号の無い入力の誤り (出荷の一覧が空) は日本語の ValueError。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- 受注より多い出荷のポカよけは検査の層 (seizou.core.checks) が services の前に見る。
  ここでは止めず、残を超える数もそのまま保存する (現物優先)。
- 出荷済みの数と残の数は orders.services の関数で出す (検査の層と同じ関数)。
  集計の前に、受注の明細の行を select_for_update でロックする (01 の 10.1)。
- 受注 (Order・OrderLine) は書かない。出荷を作った後、同じトランザクションの中で orders.services.refresh_status を呼び、受注の状態を進める。
- 納品書を出した後の出荷は、cancel_delivery_note で納品書を取り消してから update_shipment で直す (R08)。
  発行の請求書に入った出荷の納品書は取り消せない (請求書の明細は billing を import せず、出荷の逆の関係で引く)。
- 出荷の後、受信した注文で作った受注の出荷なら connect.services.send_shipping_notice で出荷の知らせを相手へ送る (HTTP は atomic の外 = R17)。
  出荷の知らせの列 (notice_*) は assign_notice_number・record_notice_response が書く。update_shipment は送り直さない。
- 一括出荷 (ship_lines) は、選んだ明細を 1 つの atomic で ship と同じ決まりに通し、出荷の知らせは全部を保存した後に送る (R17)。
"""

from __future__ import annotations

from decimal import Decimal
import uuid

from django.core.exceptions import FieldDoesNotExist
from django.db import transaction
from django.utils import timezone

from seizou.core.conf import setting
from seizou.core.exceptions import RuleViolation
from seizou.core.services import next_number
from seizou.orders.services import refresh_status, remaining_qty

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
    """知らせに出す数の字。末尾の 0 を落とす (10.00 → 10、4.50 → 4.5)。core.checks.builtin._num と同じ形。"""
    dec = _dec(value).normalize()
    if dec == dec.to_integral_value():
        return str(dec.quantize(Decimal(1)))
    return format(dec, "f")


def _record_shipment(order_line, qty, shipped_at, user, note):
    """出荷の保存の部分 (ship と ship_lines が atomic の中で呼ぶ)。明細に行の錠を掛け、R05・R02・分納を見て、出荷を作り、受注の状態を直す。

    返す物は (出荷, 錠を掛けた明細)。HTTP は送らない (送るのは _after_ship。atomic の外 = R17)。
    """
    qty = _dec(qty)
    locked = _OrderLine.objects.select_for_update().get(pk=order_line.pk)
    if locked.order.status == "cancelled":
        raise RuleViolation(
            "R05", f"保存できません。{_R05_RULE}取消していない受注の明細を選んでください。"
        )
    if not qty > 0:
        raise RuleViolation("R02", _R02_MESSAGE)
    if not setting("partial_shipment"):
        remaining = remaining_qty(locked)
        if qty != remaining:
            raise RuleViolation(
                "分納",
                f"保存できません。この会社は分納を許していません (設定「分納」)。残りの数 {_qty_text(remaining)} をまとめて出荷してください。",
            )
    shipment = Shipment.objects.create(
        shipment_no=next_number("shipment"),
        order_line=locked,
        qty=qty,
        shipped_at=shipped_at,
        note=note,
    )
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


def ship(order_line, qty, shipped_at, user, *, note="", send=True):
    """受注の明細を qty だけ出荷する。出荷番号は core の連番が付ける。

    send が偽なら、受信した注文で作った受注の出荷でも出荷の知らせを送らず (番号も付けず)、出荷の返事の欄に MESSAGE_NOTICE_SEED を書く。
    """
    with transaction.atomic():
        shipment, locked = _record_shipment(order_line, qty, shipped_at, user, note)
    # 出荷の知らせを相手へ送る。HTTP は atomic の外 (R17)。shipping と connect の循環を避けるため関数の中で import する。
    return _after_ship(shipment, locked.order, user, send)


class ShippedLines(list):
    """一括出荷の返す物 (知らせの後の出荷の list)。入力の土台は saved_message を知らせの字にする。

    1 件の出荷の返す物 (出荷) は saved_level を持たないので、ここも持たない (知らせは緑)。
    """

    @property
    def saved_message(self) -> str:
        return f"{len(self)} 件の出荷を登録しました。"


def ship_lines(lines, shipped_at, user, *, note=""):
    """選んだ受注の明細を、同じ出荷日でまとめて出荷する (一括出荷。注文残の盤の「選んだ行の操作」。N124)。

    lines は (受注の明細, 数量) の組の list。1 行ずつ ship と同じ決まり (R05・R02・分納) を通し、どれか 1 行が断られたら
    全部を保存しない (1 つの atomic)。断りの文の頭に受注番号と行の番号を付ける。
    出荷の知らせ (受信した注文で作った受注) は、全部を保存した後に atomic の外で 1 件ずつ送る (R17)。
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
        for shipment in locked:
            shipment.delivery_note = note
            shipment.save(update_fields=["delivery_note"])
        return note


def _issued_invoice_of(shipment):
    """出荷が入っている発行の請求書 (読むだけ)。無いか、請求書の部品が無ければ None。

    請求書の明細の表は出荷の逆の関係 invoice_lines の先として引き、「発行」の値は
    明細の外部キー invoice の先の表の STATUS_ISSUED から読む (billing を import しない)。
    """
    try:
        lines = Shipment._meta.get_field("invoice_lines").related_model
    except FieldDoesNotExist:
        return None
    issued = lines._meta.get_field("invoice").related_model.STATUS_ISSUED
    line = (
        lines._default_manager.select_related("invoice")
        .filter(shipment=shipment, invoice__status=issued)
        .order_by("pk")
        .first()
    )
    return line.invoice if line is not None else None


def cancel_delivery_note(note, user):
    """発行の納品書を取消にする。入っていた出荷は納品書を指したまま残す (取消の納品書の詳細に出る)。

    取消の納品書の出荷は請求できず (billing が R09 で断る)、直してから新しい納品書にまとめ直せる。
    発行の請求書に入った出荷があれば、何も変えずに ValueError (先に請求書を取り消す)。
    """
    with transaction.atomic():
        locked = DeliveryNote.objects.select_for_update().get(pk=note.pk)
        if locked.status == DeliveryNote.STATUS_CANCELLED:
            raise ValueError("取り消せません。この納品書は取り消し済みです。納品書の詳細を開き直してください。")
        shipments = list(
            Shipment.objects.select_for_update().filter(delivery_note=locked).order_by("pk")
        )
        for shipment in shipments:
            invoice = _issued_invoice_of(shipment)
            if invoice is not None:
                raise ValueError(
                    f"取り消せません。納品書 {locked.note_no} の出荷 {shipment.shipment_no} は請求書 {invoice.invoice_no} に"
                    "入っています。先に請求書を取り消してから、納品書を取り消してください。"
                )
        locked.status = DeliveryNote.STATUS_CANCELLED
        locked.save(update_fields=["status"])
        # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
        locked.saved_message = "取り消しました。"
        return locked


def update_shipment(shipment, qty, shipped_at, user, *, note=""):
    """出荷の数量と出荷日と備考を直す。出荷番号と受注の明細は変えない。

    発行の納品書に入った出荷は R08 で断る (先に cancel_delivery_note)。
    受注より多い数はここでは止めない (現物優先。検査の層が services の前に見る)。
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
        locked.qty = qty
        locked.shipped_at = shipped_at
        locked.note = note
        locked.save(update_fields=["qty", "shipped_at", "note"])
        refresh_status(line.order, user)
        return locked


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
