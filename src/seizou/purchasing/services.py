"""purchasing の公開する関数 (01 の 10.4)。画面とほかの部品は、この関数だけを通して発注を書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は記録のために受ける。記録の列がまだ無いので、今は受けるだけで書かない。
- ext (会社ごとの項目) は core の fields.save_ext に渡す。登録が無ければ save_ext が ValueError を投げる。
- 決まりの番号の無い入力の誤り (協力企業でない・明細が無い・知らない鍵・数量と単価が 0 以下) は日本語の ValueError。
  RULES.md の R02 の対象に発注は入っていないので RuleViolation にしない。DB の制約は付ける。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- 保存と同時に送る (02)。発注を保存したトランザクションを抜けた後で、取引先に向き「送る」の
  つながりがあれば connect.services.send_order を呼ぶ。相手への HTTP は atomic の外 (R17)。
- 明細の辞書の鍵は item・qty・unit・unit_price・due_date・note。
- 受入は receive。受入の数と残は列に持たず received_qty・remaining_qty・annotate_received で出す。
- 受入は stock の部品があれば在庫の入庫も作る (関数の中で import。同じ transaction.atomic の中)。
- 受入の後、seizou どうしで送った発注 (送った注文の行がある物) なら connect.services.send_acceptance で検収を相手へ送る (HTTP は atomic の外 = R17)。
"""

from __future__ import annotations

from decimal import Decimal
import uuid

from django.db import transaction
from django.utils import timezone

from seizou.core.services import next_number
from seizou.masters.services import round_amount

from .models import PurchaseOrder, PurchaseOrderLine
from .models import Receipt
from .models import _qty_text

from datetime import timedelta

from django.apps import apps
from django.db.models import DecimalField, ExpressionWrapper, F, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce

# 数の合計の型 (PurchaseOrderLine.qty と同じ桁)
_QTY_FIELD = DecimalField(max_digits=12, decimal_places=2)
_ZERO = Decimal("0")

_LINE_KEYS = frozenset({"item", "qty", "unit", "unit_price", "due_date", "note"})

_POSITIVE_MESSAGE = "数量と単価は 0 より大きい数にしてください。"
_CANCEL_AFTER_SENT_MESSAGE = (
    "送った後の取消は相手への連絡が要るので、会期後の版です。"
    "取り消せるのは状態が「作成」か「断られた」の発注だけです。"
)


def _dec(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _refuse_non_subcontractor(partner):
    if not partner.is_subcontractor:
        raise ValueError(
            f"取引先 {partner} は協力企業ではありません。取引先の種類が協力企業か両方の取引先を選んでください。"
        )


def _refuse_bad_lines(lines):
    for line in lines:
        unknown = sorted(set(line) - _LINE_KEYS)
        if unknown:
            raise ValueError(
                f"知らない項目 ({'・'.join(unknown)}) があります。発注の明細の列の名前だけを渡してください。"
            )
        if not (_dec(line["qty"]) > 0 and _dec(line["unit_price"]) > 0):
            raise ValueError(_POSITIVE_MESSAGE)


def _save_ext(obj, ext):
    if ext is not None:
        from seizou.core import fields

        fields.save_ext(obj, ext)


def _add_line(purchase_order, line, line_no):
    item = line["item"]
    qty = _dec(line["qty"])
    unit_price = _dec(line["unit_price"])
    return PurchaseOrderLine.objects.create(
        purchase_order=purchase_order,
        line_no=line_no,
        item=item,
        qty=qty,
        unit=line.get("unit") or item.unit,
        unit_price=unit_price,
        amount=round_amount(purchase_order.partner, qty * unit_price),
        due_date=line.get("due_date"),
        note=line.get("note", ""),
    )


def _has_send_connection(partner):
    """取引先に向き「送る」のつながりがあるか (読むだけ。connect の model を import しない)。"""
    connections = partner.connections
    return connections.filter(direction=connections.model.DIRECTION_SEND).exists()


def create_purchase_order(partner, lines, user, *, order_date, note="", ext=None, send=True):
    """発注を作る。明細は lines の並びの順に行番号を 1 から付ける。

    保存の後、取引先に向き「送る」のつながりがあれば相手の受け口へ送る (送れなくても例外にせず、
    送った注文の表に返事を残し、発注は「作成」のまま)。つながりが無ければ送らない。
    send が偽なら、つながりがあっても送らない (作り物のデータの種が、相手に送らずに発注を入れるため)。
    """
    _refuse_non_subcontractor(partner)
    if not lines:
        raise ValueError("明細がありません。明細を 1 行以上入れてください。")
    _refuse_bad_lines(lines)
    with transaction.atomic():
        purchase_order = PurchaseOrder.objects.create(
            po_no=next_number("purchase_order"),
            partner=partner,
            order_date=order_date,
            note=note,
        )
        for line_no, line in enumerate(lines, start=1):
            _add_line(purchase_order, line, line_no)
        _save_ext(purchase_order, ext)

    # 相手への HTTP は atomic の外 (R17)。connect と purchasing の循環を避けるため関数の中で import する。
    if send and _has_send_connection(partner):
        from seizou.connect import services as connect_services

        connect_services.send_order(purchase_order, user)
    return PurchaseOrder.objects.get(pk=purchase_order.pk)


def cancel_purchase_order(purchase_order, user, *, reason=""):
    """発注を取消にする。状態が「作成」か「断られた」のときだけ (断られた発注は相手が受けていないので、連絡なしに取り消せる。精査 A1 の想定外 S9)。

    reason が空でなければ、備考の末尾に改行して「取消の理由: …」を足す (受注の取消と同じ形)。
    """
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if locked.status not in (PurchaseOrder.STATUS_DRAFT, PurchaseOrder.STATUS_REJECTED):
            raise ValueError(_CANCEL_AFTER_SENT_MESSAGE)
        if reason:
            added = f"取消の理由: {reason}"
            note = f"{locked.note}\n{added}" if locked.note else added
            limit = PurchaseOrder._meta.get_field("note").max_length
            if len(note) > limit:
                raise ValueError(
                    f"取消の理由を足すと備考が {limit} 字を超えます。理由を短くしてください。"
                )
            locked.note = note
        locked.status = PurchaseOrder.STATUS_CANCELLED
        locked.save()
        return locked


def mark_sent(purchase_order, user):
    """「作成」の発注を「送った」にする。connect.services.send_order が相手の受け取りの返事で呼ぶ。

    既に「送った」(か、その先の状態) ならそのまま返す。「取消」なら ValueError。
    """
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if locked.status == PurchaseOrder.STATUS_CANCELLED:
            raise ValueError("取り消した発注は「送った」にできません。新しく発注を入れてください。")
        if locked.status == PurchaseOrder.STATUS_DRAFT:
            locked.status = PurchaseOrder.STATUS_SENT
            locked.save(update_fields=["status"])
        return locked


def mark_accepted(purchase_order, user, *, reply_message_id=None):
    """「送った」の発注を「相手が受け入れた」にする。それ以外の状態は ValueError。

    注文回答の日時 (こちらに届いて当てた時刻) を書く。reply_message_id は相手の注文回答の番号
    (connect の受け口が渡す。同じ番号の 2 回目は受け口が見分けて呼ばない。DB の一意の制約もある)。
    """
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if locked.status != PurchaseOrder.STATUS_SENT:
            raise ValueError(
                f"状態が「{locked.get_status_display()}」の発注は「相手が受け入れた」にできません。"
                "「送った」の発注だけを進めてください。"
            )
        locked.status = PurchaseOrder.STATUS_ACCEPTED
        locked.replied_at = timezone.now()
        fields = ["status", "replied_at"]
        if reply_message_id is not None:
            locked.reply_message_id = reply_message_id
            fields.append("reply_message_id")
        locked.save(update_fields=fields)
        return locked


def mark_rejected(purchase_order, user, *, reason, reply_message_id=None):
    """「送った」の発注を「断られた」にする。それ以外の状態は ValueError。

    reason は相手の断りの理由 (500 字で切る。空でもよい)。注文回答の日時と番号は mark_accepted と同じ。
    """
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if locked.status != PurchaseOrder.STATUS_SENT:
            raise ValueError(
                f"状態が「{locked.get_status_display()}」の発注は「断られた」にできません。"
                "「送った」の発注だけを進めてください。"
            )
        text = reason if isinstance(reason, str) else ""
        limit = PurchaseOrder._meta.get_field("reply_note").max_length
        locked.status = PurchaseOrder.STATUS_REJECTED
        locked.replied_at = timezone.now()
        locked.reply_note = text[:limit]
        fields = ["status", "replied_at", "reply_note"]
        if reply_message_id is not None:
            locked.reply_message_id = reply_message_id
            fields.append("reply_message_id")
        locked.save(update_fields=fields)
        return locked


# --- 受入 (協力企業管理の 2 つ目の画面。受け入れた数 × 発注の単価が支払の金額) ---


def receive(line, qty, received_on, user, *, note=""):
    """発注の明細に受入を 1 件入れる。金額は 受け入れた数 × 明細の単価 を発注の取引先の方法で丸める (R03 の形)。

    断るのは「取消」の発注と 0 以下の数量だけ (「断られた」や「作成」の発注も受け入れる = 現物優先)。
    受入の残より多くても止めない。stock の部品があれば、同じ atomic の中で在庫の入庫も作る。
    全部の明細で受入の数が明細の数量以上になれば、発注を「納品された」(STATUS_DELIVERED) にする。
    seizou どうしで送った発注なら、atomic を抜けた後に検収を相手へ送り、送った後の列を持つ受入を返す
    (送れなくても例外にしない。受入は成立する)。
    """
    qty = _dec(qty)
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=line.purchase_order_id)
        if locked.status == PurchaseOrder.STATUS_CANCELLED:
            raise ValueError(
                "取り消した発注には受入を入れられません。取消していない発注の明細を選んでください。"
            )
        if not qty > 0:
            raise ValueError("数量は 0 より大きい数にしてください。")
        current = PurchaseOrderLine.objects.select_related("purchase_order", "item").get(pk=line.pk)
        receipt = Receipt.objects.create(
            line=current,
            received_on=received_on,
            qty=qty,
            amount=round_amount(locked.partner, qty * current.unit_price),
            note=note or "",
            received_by=user if getattr(user, "pk", None) is not None else None,
        )
        if apps.is_installed("seizou.stock"):
            from seizou.stock import services as stock_services

            stock_services.record_received(receipt, user)
        if locked.status != PurchaseOrder.STATUS_DELIVERED:
            lines = list(annotate_received(locked.lines.all()))
            if lines and all(row.remaining_total <= 0 for row in lines):
                locked.status = PurchaseOrder.STATUS_DELIVERED
                locked.save(update_fields=["status"])

    # 検収を相手へ送る。HTTP は atomic の外 (R17)。connect と purchasing の循環を避けるため関数の中で import する。
    if _is_sent_order(locked):
        from seizou.connect import services as connect_services

        return connect_services.send_acceptance(receipt, user)
    return receipt


# --- 検収 (受入を相手の seizou の受注の明細へ送る記録。EDI の 4 段目) ---


def _is_sent_order(purchase_order):
    """発注に送った注文 (connect の Outbox) の行があるか (= seizou どうしで送った発注か)。

    逆の関係 outbox_messages を読むだけ (connect の model を import しない)。connect の無い会社では属性が無く偽。
    """
    outbox = getattr(purchase_order, "outbox_messages", None)
    return outbox is not None and outbox.exists()


def assign_acceptance_number(receipt, user):
    """受入に検収の番号を付けて返す。既にあればそのまま返す (送り直しは 1 回目の番号を使い回す)。

    connect.services.send_acceptance が送る前に呼ぶ (届かなくても番号が残る)。返す物は uuid.UUID。
    """
    with transaction.atomic():
        locked = Receipt.objects.select_for_update().get(pk=receipt.pk)
        if locked.acceptance_message_id is None:
            locked.acceptance_message_id = uuid.uuid4()
            locked.save(update_fields=["acceptance_message_id"])
        return locked.acceptance_message_id


def record_acceptance_response(receipt, response, user, *, sent=False):
    """検収の相手の返事を受入に書く (500 字で切る)。sent が真なら検収を送った日時も書く。

    sent が偽なら送った日時は今の値のまま (消さない)。connect.services.send_acceptance が呼ぶ。
    返す物は取り直した Receipt。
    """
    limit = Receipt._meta.get_field("acceptance_response").max_length
    with transaction.atomic():
        locked = Receipt.objects.select_for_update().get(pk=receipt.pk)
        locked.acceptance_response = response[:limit]
        fields = ["acceptance_response"]
        if sent:
            locked.acceptance_sent_at = timezone.now()
            fields.append("acceptance_sent_at")
        locked.save(update_fields=fields)
        return locked


def received_qty(line):
    """発注の明細の受入の数 (受入から集計。列に持たない。orders.services.shipped_qty と同じ形)。"""
    return line.receipts.aggregate(total=Coalesce(Sum("qty"), Value(_ZERO), output_field=_QTY_FIELD))["total"]


def remaining_qty(line):
    """発注の明細の受入の残 = 明細の数量 − 受入の数。マイナスも返す (現物優先)。"""
    return line.qty - received_qty(line)


def annotate_received(queryset):
    """発注の明細の queryset に、受入の数 (received_total) と受入の残 (remaining_total) を足して返す。

    received_qty・remaining_qty と同じ式 (受入の数の合計、受入が無ければ 0)。join は受入の 1 つだけ。候補の口と詳細が使う。
    """
    received = Coalesce(Sum("receipts__qty"), Value(_ZERO), output_field=_QTY_FIELD)
    return queryset.annotate(received_total=received).annotate(
        remaining_total=ExpressionWrapper(F("qty") - F("received_total"), output_field=_QTY_FIELD)
    )


def annotate_order_received(queryset):
    """発注の queryset に、明細の数量の合計 (ordered_total)・受入の数の合計 (received_total)・差 (receive_remaining) を足す。

    明細と受入の合計は別の Subquery で足す (lines と receipts の 2 つの join を 1 つの問い合わせに置くと、行が重なって数えられる)。
    発注の一覧が使う。読むだけ。
    """
    ordered = (
        PurchaseOrderLine.objects.filter(purchase_order=OuterRef("pk"))
        .order_by()
        .values("purchase_order")
        .annotate(total=Sum("qty"))
        .values("total")[:1]
    )
    received = (
        Receipt.objects.filter(line__purchase_order=OuterRef("pk"))
        .order_by()
        .values("line__purchase_order")
        .annotate(total=Sum("qty"))
        .values("total")[:1]
    )
    return queryset.annotate(
        ordered_total=Coalesce(Subquery(ordered, output_field=_QTY_FIELD), Value(_ZERO), output_field=_QTY_FIELD),
        received_total=Coalesce(Subquery(received, output_field=_QTY_FIELD), Value(_ZERO), output_field=_QTY_FIELD),
    ).annotate(
        receive_remaining=ExpressionWrapper(F("ordered_total") - F("received_total"), output_field=_QTY_FIELD)
    )


def shipping_notice_summary(line):
    """出荷の知らせ (connect の ShippingNotice) の行と数の合計。返す物は {"rows", "count", "total", "total_text"}。

    total_text は合計の字 (末尾の 0 を落とす = 行の qty_text と同じ形)。
    逆の関係 shipping_notices を読むだけで、purchasing は connect を import しない。connect の無い会社では行が無い。
    行は表の並び (出荷日の古い順)。発注の詳細の明細の表が読む。
    """
    rows = getattr(line, "shipping_notices", None)
    if rows is None:
        return {"rows": [], "count": 0, "total": _ZERO, "total_text": "0"}
    items = list(rows.all())
    total = sum((row.qty for row in items), _ZERO)
    return {"rows": items, "count": len(items), "total": total, "total_text": _qty_text(total)}


def payable_by_partner(today=None):
    """支払の見込み (先月と今月)。受入の金額を取引先ごと・月ごと (受入日の月) に足す。読むだけ。

    保存した金額の合計 (Sum) で、DB で掛け算をしない。today の既定は今日。行は取引先コードの順で、
    2 か月とも 0 の取引先は入れない。返す物 = {"months": [{"key", "label"} × 2], "rows": [{"partner",
    "last_month", "this_month"}, …], "totals": {"last_month", "this_month"}}。
    """
    if today is None:
        today = timezone.localdate()
    this_start = today.replace(day=1)
    last_start = (this_start - timedelta(days=1)).replace(day=1)
    next_start = (this_start + timedelta(days=32)).replace(day=1)
    months = [
        {"key": f"{last_start:%Y-%m}", "label": f"先月 ({last_start.month} 月)"},
        {"key": f"{this_start:%Y-%m}", "label": f"今月 ({this_start.month} 月)"},
    ]
    sums = {}
    for slot, start, end in (("last_month", last_start, this_start), ("this_month", this_start, next_start)):
        rows = (
            Receipt.objects.filter(received_on__gte=start, received_on__lt=end)
            .order_by()
            .values("line__purchase_order__partner")
            .annotate(total=Sum("amount"))
        )
        for row in rows:
            slots = sums.setdefault(row["line__purchase_order__partner"], {"last_month": _ZERO, "this_month": _ZERO})
            slots[slot] = _dec(row["total"] or _ZERO)
    shown = [pk for pk, slots in sums.items() if slots["last_month"] or slots["this_month"]]
    partner_model = PurchaseOrder._meta.get_field("partner").related_model
    partners = partner_model._default_manager.filter(pk__in=shown).order_by("code", "pk") if shown else []
    result_rows = [{"partner": partner, **sums[partner.pk]} for partner in partners]
    totals = {
        "last_month": sum((row["last_month"] for row in result_rows), _ZERO),
        "this_month": sum((row["this_month"] for row in result_rows), _ZERO),
    }
    return {"months": months, "rows": result_rows, "totals": totals}
