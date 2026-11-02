"""purchasing の公開する関数 (01 の 10.4)。画面とほかの部品は、この関数だけを通して発注を書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は記録のために受ける。記録の列がまだ無いので、今は受けるだけで書かない。
- ext (会社ごとの項目) は core の fields.save_ext に渡す。登録が無ければ save_ext が ValueError を投げる。
- 決まりの番号の無い入力の誤り (外注先でない・明細が無い・知らない鍵・数量と単価が 0 以下) は日本語の ValueError。
  RULES.md の R02 の対象に発注は入っていないので RuleViolation にしない。DB の制約は付ける。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- 保存と同時に送る (02)。発注を保存したトランザクションを抜けた後で、取引先に向き「送る」の
  つながりがあれば connect.services.send_order を呼ぶ。相手への HTTP は atomic の外 (R17)。
- 明細の辞書の鍵は item・qty・unit・unit_price・due_date・note。
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from seizou.core.services import next_number
from seizou.masters.services import round_amount

from .models import PurchaseOrder, PurchaseOrderLine

_LINE_KEYS = frozenset({"item", "qty", "unit", "unit_price", "due_date", "note"})

_POSITIVE_MESSAGE = "数量と単価は 0 より大きい数にしてください。"
_CANCEL_AFTER_SENT_MESSAGE = (
    "送った後の取消は相手への連絡が要るので、会期後の版です。"
    "取り消せるのは状態が「作成」の発注だけです。"
)


def _dec(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _refuse_non_subcontractor(partner):
    if not partner.is_subcontractor:
        raise ValueError(
            f"取引先 {partner} は外注先ではありません。区分が外注先か両方の取引先を選んでください。"
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


def create_purchase_order(partner, lines, user, *, order_date, note="", ext=None):
    """発注を作る。明細は lines の並びの順に行番号を 1 から付ける。

    保存の後、取引先に向き「送る」のつながりがあれば相手の受け口へ送る (送れなくても例外にせず、
    送った注文の表に返事を残し、発注は「作成」のまま)。つながりが無ければ送らない。
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
    if _has_send_connection(partner):
        from seizou.connect import services as connect_services

        connect_services.send_order(purchase_order, user)
    return PurchaseOrder.objects.get(pk=purchase_order.pk)


def cancel_purchase_order(purchase_order, user, *, reason=""):
    """発注を取消にする。状態が「作成」のときだけ。

    reason が空でなければ、備考の末尾に改行して「取消の理由: …」を足す (受注の取消と同じ形)。
    """
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if locked.status != PurchaseOrder.STATUS_DRAFT:
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


def mark_accepted(purchase_order, user):
    """「送った」の発注を「相手が受けた」にする。それ以外の状態は ValueError。"""
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if locked.status != PurchaseOrder.STATUS_SENT:
            raise ValueError(
                f"状態が「{locked.get_status_display()}」の発注は「相手が受けた」にできません。"
                "「送った」の発注だけを進めてください。"
            )
        locked.status = PurchaseOrder.STATUS_ACCEPTED
        locked.save(update_fields=["status"])
        return locked
