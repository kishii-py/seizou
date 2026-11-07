"""purchasing の公開する関数 (01 の 10.4)。画面とほかの部品は、この関数だけを通して発注を書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は記録のために受ける。記録の列がまだ無いので、今は受けるだけで書かない。
- ext (会社ごとの項目) は core の fields.save_ext に渡す。登録が無ければ save_ext が ValueError を投げる。
- 決まりの番号の無い入力の誤り (協力企業でない・明細が無い・知らない鍵・数量と単価が 0 以下) は日本語の ValueError。
  RULES.md の R02 の対象に発注は入っていないので RuleViolation にしない。DB の制約は付ける。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- 保存と同時に送る (02)。発注を保存したトランザクションを抜けた後で、取引先に向き「送る」の
  つながりがあれば connect.services.send_order を呼ぶ。相手への HTTP は atomic の外 (R17)。
- 明細の辞書の鍵は item・qty・unit・unit_price・price_basis・due_date・note。
  price_basis は単価の単位 (unit か lump。無ければ unit = 今と同じ)。
- 受入は receive。受入の数と残は列に持たず received_qty・remaining_qty・annotate_received で出す。
- 受入は stock の部品があれば在庫の入庫も作る (関数の中で import。同じ transaction.atomic の中)。
- 受入の後、seizou どうしで送った発注 (送った注文の行がある物) なら connect.services.send_acceptance で検収を相手へ送る (HTTP は atomic の外 = R17)。
- 注文回答の 4 つの答え (受諾 AP = 相手が受け入れた・条件付受諾 CA = 相手が条件付で受けた・辞退 RE = 断られた・保留 AJ = 相手が保留した) は、
  connect の受け口が mark_accepted・mark_conditional・mark_rejected・mark_on_hold で答えを待っている発注 (送った・相手が保留した) に当てる。
- 「相手が受け入れた」の発注に一部の受入が入ると「一部納品」、全部そろえば「納品された」。受入が先の発注に後から受諾が届いたときも「一部納品」。
"""

from __future__ import annotations

from decimal import Decimal
import uuid

from django.db import transaction
from django.utils import timezone

from seizou.core import edi_codes
from seizou.core.services import next_number
from seizou.masters.services import PRICE_BASIS_UNIT, line_amount, refuse_inactive, refuse_unknown_price_basis, round_amount

from .models import PurchaseOrder, PurchaseOrderLine
from .models import Receipt
from .models import _qty_text

from datetime import timedelta

from django.apps import apps
from django.db.models import DecimalField, ExpressionWrapper, F, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Count, IntegerField
from django.db.models import Case, When
from django.db.models import Exists

# 数の合計の型 (PurchaseOrderLine.qty と同じ桁)
_QTY_FIELD = DecimalField(max_digits=12, decimal_places=2)
_ZERO = Decimal("0")

_LINE_KEYS = frozenset({"item", "qty", "unit", "unit_price", "price_basis", "due_date", "note"})

_POSITIVE_MESSAGE = "保存できません。数量か単価が 0 以下です。数量と単価は 0 より大きい数にしてください。"
#: 第 1 版の注文回答を当てられる発注の状態 (答えを待っている状態)。保留の後は、もう一度答えが届く
REPLY_WAITING = (PurchaseOrder.STATUS_SENT, PurchaseOrder.STATUS_ON_HOLD)
#: 注文回答を当てられない状態の断りの字 ({status} は今の状態の字、{target} は行き先の状態の字)
_REPLY_STATUS_MESSAGE = (
    "状態が「{status}」の発注は「{target}」にできません。"
    "「送った」の発注だけを進めてください (「相手が保留した」の発注も進められます)。"
)
_CANCEL_AFTER_SENT_MESSAGE = (
    "取り消せません。状態が「{status}」の発注は取り消せません。取り消せるのは「下書き」「断られた」の発注と、"
    "相手がまだ出荷していない「送った」「相手が受け入れた」の発注です。"
)
_CANCEL_SHIPPED_MESSAGE = (
    "取り消せません。相手が一部を出荷済みなので、取消は送れません。"
    "出していない明細を取り消し、出した明細の数を出荷済みの数に直す変更を「変更」で送ってください。"
)


def _dec(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _refuse_non_subcontractor(partner):
    if not partner.is_subcontractor:
        raise ValueError(
            f"保存できません。取引先 {partner} は協力企業ではありません。取引先の種類が協力企業か両方の取引先を選んでください。"
        )


def _refuse_bad_lines(lines):
    for line in lines:
        unknown = sorted(set(line) - _LINE_KEYS)
        if unknown:
            raise ValueError(
                f"保存できません。知らない項目 ({'・'.join(unknown)}) があります。発注の明細の列の名前だけを渡してください。"
            )
        if "price_basis" in line:
            refuse_unknown_price_basis(line["price_basis"])
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
    price_basis = line.get("price_basis", PRICE_BASIS_UNIT)
    return PurchaseOrderLine.objects.create(
        purchase_order=purchase_order,
        line_no=line_no,
        item=item,
        qty=qty,
        unit=line.get("unit") or item.unit,
        unit_price=unit_price,
        price_basis=price_basis,
        amount=line_amount(purchase_order.partner, qty, unit_price, price_basis),
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
    送った注文の表に返事を残し、発注は「作成」のまま。届かなければ、返す発注に黄の知らせの字を付ける)。
    つながりが無ければ送らない。
    send が偽なら、つながりがあっても送らない (作り物のデータの種が、相手に送らずに発注を入れるため)。
    """
    _refuse_non_subcontractor(partner)
    if not lines:
        raise ValueError("保存できません。明細がありません。明細を 1 行以上入れてください。")
    _refuse_bad_lines(lines)
    refuse_inactive(partner, [line.get("item") for line in lines])
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

        outbox = connect_services.send_order(purchase_order, user)
        return _with_delivery_notice(PurchaseOrder.objects.get(pk=purchase_order.pk), outbox)
    return PurchaseOrder.objects.get(pk=purchase_order.pk)


def cancel_purchase_order(purchase_order, user, *, reason=""):
    """発注を取消にする。状態が「作成」か「断られた」なら送らずに取り消す (断られた発注は相手が受けていないので、連絡なしに取り消せる。精査 A1 の想定外 S9)。

    状態が「送った」「相手が受け入れた」で、相手がどの明細も出荷していない (can_cancel_sent_order) なら、同じ atomic で状態を取消・版を 1 上げ・
    目的を "1" (打切り)・全部の明細を明細状態コード 2 にし、atomic の外で取消の版を相手へ送る (connect.services.send_order)。届いたかの知らせを付けて返す。
    一部でも相手が出荷済みの明細があれば、取消は送らず「変更」の道を案内する字で断る。「納品された」「取消」とつながりの無い送った発注も断る。
    reason が空でなければ、備考の末尾に改行して「取消の理由: …」を足す (受注の取消と同じ形)。
    訳: 相手が 1 個も出していない送った発注は、取消 (中小企業共通 EDI の注文書目的コード 1 打切り) を相手へ送れるようにし、出した後は数を直す変更で残りを止めるため。出所 = 決め EDIT-1 (11-06 23:2x)・PRICE-4f (11-06 23:1x)・C96-1 (11-07 06:33。取消を送れるのは相手が 1 個も出していない間だけ)。
    変えてよい条件 = C96-1 の答えが変わったとき。
    """
    sent = False
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if locked.status in CHANGEABLE_STATUSES:
            lines = _live_lines(locked)
            if any(known_shipped_qty(line) > 0 for line in lines):
                raise ValueError(_CANCEL_SHIPPED_MESSAGE)
            if not _has_send_connection(locked.partner):
                raise ValueError(
                    f"取り消せません。取引先 {locked.partner.name} へのつながりが無いので、取消を相手へ送れません。"
                    "設定の画面の「取引先との接続」で登録してください。"
                )
            sent = True
        elif locked.status not in (PurchaseOrder.STATUS_DRAFT, PurchaseOrder.STATUS_REJECTED):
            raise ValueError(_CANCEL_AFTER_SENT_MESSAGE.format(status=locked.get_status_display()))
        if reason:
            added = f"取消の理由: {reason}"
            note = f"{locked.note}\n{added}" if locked.note else added
            limit = PurchaseOrder._meta.get_field("note").max_length
            if len(note) > limit:
                raise ValueError(
                    f"取り消せません。取消の理由を足すと備考が {limit} 字を超えます。理由を短くしてください。"
                )
            locked.note = note
        locked.status = PurchaseOrder.STATUS_CANCELLED
        if sent:
            # 取消の版 (注文書目的コード 1 打切り)。全部の明細を明細状態コード 2 にする (行は残す)。
            locked.revision_no += 1
            locked.purpose_code = edi_codes.PURPOSE_CANCEL
            locked.lines.update(line_status_code=edi_codes.LINE_DELETED, line_status_reason_code="")
        locked.save()
        if not sent:
            return locked

    # 相手への HTTP は atomic の外 (R17)。connect と purchasing の循環を避けるため関数の中で import する。
    from seizou.connect import services as connect_services

    outbox = connect_services.send_order(PurchaseOrder.objects.get(pk=locked.pk), user)
    return _with_delivery_notice(PurchaseOrder.objects.get(pk=locked.pk), outbox)


def can_cancel_sent_order(purchase_order):
    """送った発注の取消を相手へ送れるか (読むだけ)。状態が CHANGEABLE_STATUSES (送った・相手が保留した・相手が条件付で受けた・相手が受け入れた・一部納品) で、相手がどの明細も出荷していず (known_shipped_qty が 0)、
    取引先に向き「送る」のつながりがある。取消の確かめ (purchasing.checks の LawOrderCancel) が使う。状態の外ならほかを読まずに偽。
    """
    if purchase_order.status not in CHANGEABLE_STATUSES:
        return False
    lines = _live_lines(purchase_order)
    if not lines or not getattr(lines[0], "can_send", False):
        return False
    return all(known_shipped_qty(line) <= 0 for line in lines)


def record_reply_after_cancel(purchase_order, user, *, reason, reply_message_id):
    """取消を送った後に届いた第 1 版の注文回答を、発注の状態を変えずに第 1 版の答えの列 (replied_at・reply_note・reply_message_id) にだけ書く。

    connect の受け口 (_receive_reply) が呼ぶ。取消の版と第 1 版の答えが行き違っても、相手に誤った断りを返さないため。返す物は取り直した発注。
    """
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        limit = PurchaseOrder._meta.get_field("reply_note").max_length
        locked.replied_at = timezone.now()
        locked.reply_note = (reason if isinstance(reason, str) else "")[:limit]
        locked.reply_message_id = reply_message_id
        locked.save(update_fields=["replied_at", "reply_note", "reply_message_id"])
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


def mark_accepted(purchase_order, user, *, reply_message_id=None, replied_at=None):
    """答えを待っている発注 (送った・相手が保留した) を「相手が受け入れた」にする。それ以外の状態は ValueError。

    注文回答の日時 (こちらに届いて当てた時刻。replied_at を渡せばその日時 = 種の読み込みが使う) を書く。
    reply_message_id は相手の注文回答の番号
    (connect の受け口が渡す。同じ番号の 2 回目は受け口が見分けて呼ばない。DB の一意の制約もある)。
    注文回答のタイプを受諾 (AP) にし、明細の答えを空に戻す。
    受入が先に入った発注は「一部納品」にする (決め C45-2 (11-07 00:2x) と決め C97-2 (11-07 06:40)。受入と注文回答の届く順に関わらず、一部だけ戻った発注を一部納品にするため)。
    全部そろった発注は receive が先に「納品された」にしているので、ここには来ない。
    訳: 注文回答は答えを待っている発注 (送った・相手が保留した) にだけ当て、届く順が前後しても記録を戻さないため。保留の後は本当の答えが届く (中小企業共通 EDI の注文回答タイプコードの保留 AJ)。出所 = 決め PRICE-4e (11-07 00:0x) と中小企業共通 EDI の注文回答の項目。
    変えてよい条件 = 答えを当てる状態の決めが変わったとき。
    """
    with transaction.atomic():
        locked = _lock_waiting_order(purchase_order, PurchaseOrder.STATUS_ACCEPTED)
        locked.status = PurchaseOrder.STATUS_PARTIAL if locked.receipts.exists() else PurchaseOrder.STATUS_ACCEPTED
        locked.replied_at = replied_at or timezone.now()
        locked.reply_type = edi_codes.REPLY_ACCEPTED
        fields = ["status", "replied_at", "reply_type"]
        if reply_message_id is not None:
            locked.reply_message_id = reply_message_id
            fields.append("reply_message_id")
        locked.save(update_fields=fields)
        _clear_line_replies(locked)
        return locked


def mark_rejected(purchase_order, user, *, reason, reply_message_id=None):
    """答えを待っている発注 (送った・相手が保留した) を「断られた」にする。それ以外の状態は ValueError。

    reason は相手の断りの理由 (500 字で切る。空でもよい)。注文回答の日時と番号は mark_accepted と同じ。
    注文回答のタイプを辞退 (RE) にし、明細の答えを空に戻す。
    訳: 注文回答は答えを待っている発注 (送った・相手が保留した) にだけ当て、届く順が前後しても記録を戻さないため。保留の後は本当の答えが届く (中小企業共通 EDI の注文回答タイプコードの保留 AJ)。出所 = 決め PRICE-4e (11-07 00:0x) と中小企業共通 EDI の注文回答の項目。
    変えてよい条件 = 答えを当てる状態の決めが変わったとき。
    """
    with transaction.atomic():
        locked = _lock_waiting_order(purchase_order, PurchaseOrder.STATUS_REJECTED)
        text = reason if isinstance(reason, str) else ""
        limit = PurchaseOrder._meta.get_field("reply_note").max_length
        locked.status = PurchaseOrder.STATUS_REJECTED
        locked.replied_at = timezone.now()
        locked.reply_note = text[:limit]
        locked.reply_type = edi_codes.REPLY_REJECTED
        fields = ["status", "replied_at", "reply_note", "reply_type"]
        if reply_message_id is not None:
            locked.reply_message_id = reply_message_id
            fields.append("reply_message_id")
        locked.save(update_fields=fields)
        _clear_line_replies(locked)
        return locked


def mark_conditional(purchase_order, user, *, lines, reply_message_id=None, replied_at=None):
    """答えを待っている発注 (送った・相手が保留した) を「相手が条件付で受けた」にし、相手の明細ごとの答えを明細に写す。それ以外の状態は ValueError。

    中小企業共通 EDI の条件付受諾の項目を元にした (ガイドブック 3.2.2「発注者は受注者と協議し、合意した条件で注文変更処理を行う」)。
    発注の値は変えない (直すのは発注元が変更を送るとき = change_purchase_order)。
    lines の形は record_line_replies と同じ。注文回答の日時と番号は mark_accepted と同じ。注文回答のタイプは条件付受諾 (CA)。
    訳: 相手の答えを発注に残し、発注元が直すかを決められるようにするため。出所 = 決め PRICE-4e (11-07 00:0x)・PRICE-4f (11-06 23:1x) と中小企業共通 EDI の注文回答の項目。
    変えてよい条件 = 条件付受諾の扱いの決めが変わったとき。

    取適法: 合意した単価・数量・納期は、発注の変更 (change_purchase_order) で相手へ送ると、発注の中身を電磁的方法で示した形になる。
    直さずに進めると、発注と相手の受注の値が違ったままになり、発注の内容を書面で示す決まりに当たるおそれがある。
    この関数は止めない (詳細の黄の 1 行で「変更」を案内するだけ)。確かめた資料 = 作り手の知識 (公正取引委員会の資料は未読)。
    """
    with transaction.atomic():
        locked = _lock_waiting_order(purchase_order, PurchaseOrder.STATUS_CONDITIONAL)
        record_line_replies(locked, lines)
        locked.status = PurchaseOrder.STATUS_CONDITIONAL
        locked.replied_at = replied_at or timezone.now()
        locked.reply_type = edi_codes.REPLY_CONDITIONAL
        fields = ["status", "replied_at", "reply_type"]
        if reply_message_id is not None:
            locked.reply_message_id = reply_message_id
            fields.append("reply_message_id")
        locked.save(update_fields=fields)
        return locked


def mark_on_hold(purchase_order, user, *, reply_message_id=None, replied_at=None):
    """答えを待っている発注 (送った・相手が保留した) を「相手が保留した」にする。それ以外の状態は ValueError。

    注文回答のタイプを保留 (AJ) にし、明細の答えを空に戻す。注文回答の日時と番号は mark_accepted と同じ
    (流れの枠と段の帯の「回答」は注文回答の日時で読む。保留も注文回答の 1 つなので回答の段に印が付く)。
    訳: 相手がすぐ答えられない注文を、答えを待つ状態として見せ、後で届く本当の答えを当てられるようにするため。出所 = 決め PRICE-4e (11-07 00:0x。保留は受注を作らず会話で話す)。
    変えてよい条件 = 保留の扱いの決めが変わったとき。
    """
    with transaction.atomic():
        locked = _lock_waiting_order(purchase_order, PurchaseOrder.STATUS_ON_HOLD)
        locked.status = PurchaseOrder.STATUS_ON_HOLD
        locked.replied_at = replied_at or timezone.now()
        locked.reply_type = edi_codes.REPLY_PENDING
        fields = ["status", "replied_at", "reply_type"]
        if reply_message_id is not None:
            locked.reply_message_id = reply_message_id
            fields.append("reply_message_id")
        locked.save(update_fields=fields)
        _clear_line_replies(locked)
        return locked


def mark_change_accepted(purchase_order, user):
    """発注の変更 (第 2 版から) に相手の受諾 (AP) が届いたときに、connect の受け口が呼ぶ。返す物は取り直した発注。

    状態が「相手が条件付で受けた」なら「相手が受け入れた」(受入が 1 つでもあれば「一部納品」) にし、明細の答えを空に戻す。
    ほかの状態では何もしない。断る処理は無い。
    相手の条件に合わせて直した変更を相手が受け入れたら、条件の話し合いが済んだ印として状態を進める。
    変更への答えで発注の状態を変えない決まりの、ただ 1 つの例外。出所 = 決め PRICE-4e (11-07 00:0x。発注元の変更を待つ) と
    決め C97-3 (11-07 06:42。条件付で受けた発注を直して送った変更を相手が受け入れたら、状態を進めて明細の回答を消す)。
    """
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if locked.status != PurchaseOrder.STATUS_CONDITIONAL:
            return locked
        locked.status = PurchaseOrder.STATUS_PARTIAL if locked.receipts.exists() else PurchaseOrder.STATUS_ACCEPTED
        locked.save(update_fields=["status"])
        _clear_line_replies(locked)
        return locked


def record_line_replies(purchase_order, lines):
    """相手の注文回答の明細ごとの答えを、発注の明細に写す。呼ぶ側が発注を select_for_update した atomic の中で呼ぶ。

    lines は [{"line_no": int, "reply_type": "AP"|"CA"|"RE", "unit_price": Decimal|None, "qty": Decimal|None, "due_date": date|None}]。
    lines にある明細はその答え (条件付受諾 CA の明細だけ値を入れ、AP と RE は値を空)、lines に無い明細は受諾 (AP。EDI の既定) にして値を空にする。
    発注に無い行番号は ValueError (受け口が相手へ返す文。画面には出ない)。どれかの行番号が無ければ、どの明細も書かない。
    mark_conditional と、connect の版 2 からの答えが呼ぶ (明細の答えの写し方を 1 か所にする)。
    訳: 相手の明細ごとの答えを発注の明細に写し、発注に無い行番号の答えを当てずに相手へ返すため。出所 = 中小企業共通 EDI の注文回答明細回答タイプコード (既定 AP) と決め PRICE-4f (11-06 23:1x)。
    変えてよい条件 = 送り合いが行番号でなく明細の識別で明細を指すようになったとき。
    """
    by_no = {line.line_no: line for line in purchase_order.lines.all()}
    answers = {}
    for row in lines:
        number = row["line_no"]
        if number not in by_no:
            raise ValueError(f"注文回答の明細の行番号 {number} が発注 {purchase_order.po_no} にありません。")
        answers[number] = row
    for number, line in by_no.items():
        row = answers.get(number, {})
        line.reply_type = row.get("reply_type", edi_codes.REPLY_ACCEPTED)
        conditional = line.reply_type == edi_codes.REPLY_CONDITIONAL
        line.reply_unit_price = row.get("unit_price") if conditional else None
        line.reply_qty = row.get("qty") if conditional else None
        line.reply_due_date = row.get("due_date") if conditional else None
    PurchaseOrderLine.objects.bulk_update(
        list(by_no.values()), ["reply_type", "reply_unit_price", "reply_qty", "reply_due_date"]
    )


def _lock_waiting_order(purchase_order, target):
    """発注を select_for_update で引き、答えを待っている状態 (REPLY_WAITING) でなければ ValueError。target は行き先の状態の値。

    mark_accepted・mark_rejected・mark_conditional・mark_on_hold が使う 1 つの門 (断りの字の形をそろえるため)。
    訳: 注文回答は答えを待っている発注 (送った・相手が保留した) にだけ当て、届く順が前後しても記録を戻さないため。保留の後は本当の答えが届く (中小企業共通 EDI の注文回答タイプコードの保留 AJ)。出所 = 決め PRICE-4e (11-07 00:0x) と中小企業共通 EDI の注文回答の項目。
    変えてよい条件 = 答えを当てる状態の決めが変わったとき。
    """
    locked = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
    if locked.status not in REPLY_WAITING:
        raise ValueError(
            _REPLY_STATUS_MESSAGE.format(
                status=locked.get_status_display(), target=dict(PurchaseOrder.STATUS_CHOICES)[target]
            )
        )
    return locked


def _clear_line_replies(purchase_order):
    """発注の明細の答えを空に戻す (前の答えの明細の値が、次の答えの後に残って読み違えられないようにする)。"""
    purchase_order.lines.update(reply_type="", reply_unit_price=None, reply_qty=None, reply_due_date=None)


# 送った後の知らせの字 (入力の土台 core.views.with_saved_note が、返した発注の saved_message を「保存しました。」の代わりに出し、saved_level が "warning" なら黄の知らせにする)
UNDELIVERED_MESSAGE = "注文は相手に届いていません。{response} 直したら「再送」を押してください。"
RESENT_MESSAGE = "相手に送りました。"


def _with_delivery_notice(purchase_order, outbox, *, resent=False):
    """送った注文の行 (Outbox) の返事が「受け取った」でなければ、発注に黄の知らせの字を付けて返す。

    届いていて resent が真なら「相手に送りました。」を付ける。届いていて resent が偽なら何も付けない (「保存しました。」のまま)。
    版が 2 以上 (変更と取消) の発注は、版と目的を見て字を選ぶ (変更の画面の保存も、詳細の「再送」も同じ字): 届かなければ
    「第 n 版の変更 (取消) は相手に届いていません。<返事> 直したら「再送」を押してください。」(返事が「断られた」で始まるときは「相手と話して決めてください。」)、
    届けば resent に依らず「相手に第 n 版の変更を送りました。」か「相手に取消を送りました。」。
    属性は画面の知らせのためだけで、表に書かない。
    """
    if purchase_order.revision_no >= 2:
        return _with_revision_notice(purchase_order, outbox)
    if outbox.response != "受け取った":
        purchase_order.saved_message = UNDELIVERED_MESSAGE.format(response=outbox.response)
        purchase_order.saved_level = "warning"
    elif resent:
        purchase_order.saved_message = RESENT_MESSAGE
    return purchase_order


# 変更と取消の版 (版 2 から) を送った後の知らせの字
REVISION_UNDELIVERED_MESSAGE = "第 {revision_no} 版の{kind}は相手に届いていません。{response} {next_step}"
REVISION_RESEND_STEP = "直したら「再送」を押してください。"
REVISION_REFUSED_STEP = "相手と話して決めてください。"
REVISION_SENT_MESSAGE = "相手に第 {revision_no} 版の変更を送りました。"
CANCEL_SENT_MESSAGE = "相手に取消を送りました。"
NO_CHANGE_MESSAGE = "変えた所が無いので、変更を送っていません。"
#: 相手が版 2 からの変更 (取消) を断ったときの、発注の詳細の黄色の 1 行 (発注は前の版へ戻さない)
REVISION_REFUSED_ALERT = "相手は第 {revision_no} 版の{kind}を断りました。理由: {reason}。相手の受注は前の版の中身のままです。{next_step}"
REVISION_REFUSED_CHANGE_STEP = "同じ中身をもう一度送るときは「変更」から保存してください。"


def revision_alert(purchase_order, state):
    """版 2 からの発注の詳細の黄色の 1 行の字 (届いていない・相手が断った)。無ければ空の字。state は purchasing_tags.revision_state の辞書。"""
    if state is None:
        return ""
    cancel = purchase_order.purpose_code == edi_codes.PURPOSE_CANCEL
    if not state["delivered"]:
        refused = state["response"].startswith("断られた")
        return REVISION_UNDELIVERED_MESSAGE.format(
            revision_no=purchase_order.revision_no,
            kind=state["kind"],
            response=state["response"],
            next_step=REVISION_REFUSED_STEP if refused else REVISION_RESEND_STEP,
        )
    if state["reply"] == edi_codes.REPLY_REJECTED:
        return REVISION_REFUSED_ALERT.format(
            revision_no=purchase_order.revision_no,
            kind=state["kind"],
            reason=state["reply_note"] or "書いてありません",
            next_step=REVISION_REFUSED_STEP if cancel else REVISION_REFUSED_CHANGE_STEP,
        )
    return ""


def _with_revision_notice(purchase_order, outbox):
    """版 2 から (変更と取消) の発注に、送った後の知らせの字を付けて返す (_with_delivery_notice が呼ぶ)。"""
    cancel = purchase_order.purpose_code == edi_codes.PURPOSE_CANCEL
    if outbox.response != "受け取った":
        refused = outbox.response.startswith("断られた")
        purchase_order.saved_message = REVISION_UNDELIVERED_MESSAGE.format(
            revision_no=purchase_order.revision_no,
            kind="取消" if cancel else "変更",
            response=outbox.response,
            next_step=REVISION_REFUSED_STEP if refused else REVISION_RESEND_STEP,
        )
        purchase_order.saved_level = "warning"
    else:
        purchase_order.saved_message = (
            CANCEL_SENT_MESSAGE if cancel else REVISION_SENT_MESSAGE.format(revision_no=purchase_order.revision_no)
        )
    return purchase_order


def resend_purchase_order(purchase_order, user):
    """発注を相手へ送り直し (connect.services.send_order)、取り直した発注を返す (画面が発注の詳細へ戻るため)。

    「下書き」の発注と、届いていない変更と取消の版 (版 2 から。同じ版の番号で送り直す) を送る。
    届かなければ返す発注に黄の知らせの字を付け、届けば「相手に送りました。」を付ける。
    つながりの無い取引先と取消の発注は send_order の ValueError のまま (精査 A7 の 1-1 の S15)。
    """
    # 相手への HTTP は send_order の中 (atomic の外。R17)。connect と purchasing の循環を避けるため関数の中で import する。
    from seizou.connect import services as connect_services

    outbox = connect_services.send_order(purchase_order, user)
    return _with_delivery_notice(PurchaseOrder.objects.get(pk=purchase_order.pk), outbox, resent=True)


# --- 送った発注の変更 (中小企業共通 EDI の注文変更の方式 2。相手が出荷し終えるまで) ---

#: 変更を送れる発注の状態
#: 変更と取消を相手へ送れる発注の状態 (C96)。C97 (注文回答の 4 つ) の「相手が保留した」「相手が条件付で受けた」「一部納品」も入れる
#: (条件付の知らせは「変更」で発注を直して送る道を案内し、一部納品は「変更」で残りを止める。取消は can_cancel_sent_order が出荷の数も見る)。
CHANGEABLE_STATUSES = (
    PurchaseOrder.STATUS_SENT,
    PurchaseOrder.STATUS_ON_HOLD,
    PurchaseOrder.STATUS_CONDITIONAL,
    PurchaseOrder.STATUS_ACCEPTED,
    PurchaseOrder.STATUS_PARTIAL,
)
#: 変更の明細の辞書の鍵 (既にある明細は line に PurchaseOrderLine、足す明細は新規登録と同じ鍵)
_LINE_KEYS_ON_CHANGE = _LINE_KEYS | {"line"}


def known_shipped_qty(line):
    """発注元から見える、相手が出した数 = 出荷の知らせの数の合計と受入の数の大きい方。読むだけ。

    訳: 発注元から見えるのは知らせと受入だけで、知らせは遅れることがあるため (受入の方が多ければ受入の数)。
    """
    received = getattr(line, "received_total", None)
    if received is None:
        received = received_qty(line)
    notices = getattr(line, "notice_total", None)
    if notices is None:
        notices = line.shipping_notice["total"]
    return max(_dec(notices), _dec(received))


def _live_lines(purchase_order):
    """取り消していない明細 (明細状態コード 2 でない)。行番号の順。1 回の問い合わせで、受入の数 (received_total)・
    出荷の知らせの数の合計 (notice_total)・取引先に向き「送る」のつながりがあるか (can_send) を足す (発注の詳細の問い合わせの数を増やさない)。
    connect の無い会社では知らせの数は 0、つながりは無い。
    """
    lines = annotate_received(purchase_order.lines.exclude(line_status_code=edi_codes.LINE_DELETED))
    try:
        notice_model = PurchaseOrderLine._meta.get_field("shipping_notices").related_model
        connection_model = purchase_order.partner.connections.model
    except (FieldDoesNotExist, AttributeError):
        return list(lines.annotate(notice_total=Value(_ZERO, output_field=_QTY_FIELD)).order_by("line_no"))
    notices = (
        notice_model.objects.filter(line=OuterRef("pk")).order_by().values("line").annotate(total=Sum("qty")).values("total")[:1]
    )
    sendable = connection_model.objects.filter(
        partner_id=OuterRef("purchase_order__partner_id"), direction=connection_model.DIRECTION_SEND
    )
    return list(
        lines.annotate(
            notice_total=Coalesce(Subquery(notices, output_field=_QTY_FIELD), Value(_ZERO), output_field=_QTY_FIELD),
            can_send=Exists(sendable),
        ).order_by("line_no")
    )


def can_change_order(purchase_order):
    """送った発注を変更として送れるか (読むだけ)。詳細の「変更」のボタンと変更の画面が使う。

    状態が「送った」か「相手が受け入れた」・取り消していない明細のどれかで相手が出した数 (known_shipped_qty) が数量より少ない・
    取引先に向き「送る」のつながりがある。状態を先に見て、外ならつながり・出荷の知らせ・受入を読まずに偽を返す。
    """
    if purchase_order.status not in CHANGEABLE_STATUSES:
        return False
    lines = _live_lines(purchase_order)
    if not lines or not getattr(lines[0], "can_send", False):
        return False
    return any(known_shipped_qty(line) < line.qty for line in lines)


def _reason_code(qty_changed, price_changed, due_changed, note_changed):
    """変えた明細の明細状態理由コード。単価が変われば 3006 (ほかも変わっていても)、納期と数は 3003、納期だけ 3001、数だけ 3002、備考だけ 3099。"""
    if price_changed:
        return edi_codes.REASON_PRICE
    if qty_changed and due_changed:
        return edi_codes.REASON_DUE_QTY
    if due_changed:
        return edi_codes.REASON_DUE
    if qty_changed:
        return edi_codes.REASON_QTY
    if note_changed:
        return edi_codes.REASON_OTHER
    return ""


def _refuse_bad_change_lines(partner, lines):
    """変更の明細の形を見る (鍵・line と item のどちらか 1 つ・数量と単価が 0 より大きい・足す明細の部品が使用中)。"""
    for line in lines:
        unknown = sorted(set(line) - _LINE_KEYS_ON_CHANGE)
        if unknown:
            raise ValueError(
                f"保存できません。知らない項目 ({'・'.join(unknown)}) があります。発注の明細の列の名前だけを渡してください。"
            )
        if ("line" in line) == ("item" in line):
            raise ValueError("保存できません。明細の行に line と item の両方があるか、どちらもありません。どちらか 1 つにしてください。")
        if "qty" in line and not _dec(line["qty"]) > 0:
            raise ValueError(_POSITIVE_MESSAGE)
        if "unit_price" in line and not _dec(line["unit_price"]) > 0:
            raise ValueError(_POSITIVE_MESSAGE)
        if "item" in line and not ("qty" in line and "unit_price" in line):
            raise ValueError(_POSITIVE_MESSAGE)
    refuse_inactive(partner, [line["item"] for line in lines if "item" in line])


def change_purchase_order(purchase_order, data, lines, user, *, ext=None):
    """送った発注を直し、第 n 版の変更として相手へ送る (中小企業共通 EDI の注文変更の方式 2)。返す物は取り直した発注。

    data は note だけ。lines は直した後の明細の全部 (既にある明細は鍵 line に PurchaseOrderLine と qty・unit_price・due_date・note。
    足す明細は新規登録と同じ鍵)。lines に無い既にある明細のうち、取り消していない明細は取り消す (行は残して明細状態コード 2)。既に 2 の明細は 2 のまま。
    明細状態コードは前の版と比べてこの版でしたこと (足した = 1・変えた = 3・触っていない = 39・取り消した = 2)、理由コードは _reason_code。
    足す明細の行番号は、取消の行も含めた今の行の一番大きい番号 + 1 から。
    変えた所が無いときは版を上げず送らず、返す発注に「変えた所が無いので、変更を送っていません。」を付ける。ただし今の版 (版 2 から) を相手が断っていれば
    (今の版の最初の送った注文の行の reply_type が "RE")、コードを表のまま版を 1 上げて送る (相手と話した後に同じ中身をもう一度送るため)。
    送るときは同じ atomic の中で版を 1 上げ、目的を "4" にし、取り消していない明細の全部の受入が数以上なら「納品された」にする。状態はほかに変えない
    (変更への答えを待つ間も受入と出荷の知らせを今のまま受けるため)。atomic の外で connect.services.send_order を呼び、届いたかの知らせを付けて返す。
    断る (頭は「保存できません。」): 状態が「送った」「相手が受け入れた」の外・相手が全部の明細を出荷し終えている・相手が出荷済みの明細の単価の変更と、
    出荷済みの数より少ない数と取消・取り消していない明細が 0 行・つながりが無い・数量と単価が 0 以下・知らない鍵・使用停止の部品。
    訳: 相手が出荷し終えるまでは発注を直して変更として送れるようにし、出した物の記録と食い違う直し (出荷済みの明細の単価・数・取消) を止めるため。出所 = 決め PRICE-4f (11-06 23:1x)・EDIT-1 (11-06 23:2x)・中小企業共通 EDI に合わせた決め EDIT-2 (受注者が出した物の金額は仕入明細の流れで確かめる)。
    変えてよい条件 = PRICE-4f・EDIT-1・EDIT-2 の答えが変わったとき。
    """
    partner = purchase_order.partner
    _refuse_bad_change_lines(partner, lines)
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if locked.status not in CHANGEABLE_STATUSES:
            raise ValueError(
                f"保存できません。状態が「{locked.get_status_display()}」の発注は、変更を送れません。"
                "変更を送れるのは「送った」か「相手が受け入れた」の発注です。"
            )
        live = annotate_received(locked.lines.exclude(line_status_code=edi_codes.LINE_DELETED)).order_by("line_no")
        live = {line.pk: line for line in live}
        shipped = {pk: known_shipped_qty(line) for pk, line in live.items()}
        if live and all(shipped[pk] >= line.qty for pk, line in live.items()):
            raise ValueError(
                "保存できません。相手が全部の明細を出荷し終えているので、変更は送れません "
                "(送った発注を直せるのは、相手が出荷し終えるまで)。相手と話して決めてください。"
            )
        kept = {}
        for line in lines:
            if "line" not in line:
                continue
            target = line["line"]
            current = live.get(getattr(target, "pk", None))
            if current is None:
                raise ValueError("保存できません。明細の行が、この発注の取り消していない明細ではありません。発注の詳細を開き直してください。")
            kept[current.pk] = line
            done = shipped[current.pk]
            if done > 0 and "unit_price" in line and _dec(line["unit_price"]) != current.unit_price:
                raise ValueError(
                    f"保存できません。明細 {current.line_no} は相手が出荷済みなので、単価は変えられません。"
                    "出した後の単価の違いは相手と話して決めてください。"
                )
            if "qty" in line and _dec(line["qty"]) < done:
                raise ValueError(
                    f"保存できません。明細 {current.line_no} の数を、相手が出荷済みの数 ({_qty_text(done)}) より少なくはできません。"
                )
        dropped = [line for pk, line in live.items() if pk not in kept]
        for line in dropped:
            if shipped[line.pk] > 0:
                raise ValueError(
                    f"保存できません。明細 {line.line_no} は相手が出荷済みなので取り消せません。数を出荷済みの数に直してください。"
                )
        added = [line for line in lines if "item" in line]
        if not kept and not added:
            raise ValueError("保存できません。明細が無くなります。全部を止めるときは「取消」を使ってください。")
        if not _has_send_connection(partner):
            raise ValueError(
                f"保存できません。取引先 {partner.name} へのつながりがありません。設定の画面の「取引先との接続」で登録してください。"
            )

        changes = {}
        for pk, line in kept.items():
            current = live[pk]
            qty = _dec(line.get("qty", current.qty))
            unit_price = _dec(line.get("unit_price", current.unit_price))
            due_date = line.get("due_date", current.due_date)
            note = line.get("note", current.note) or ""
            flags = (qty != current.qty, unit_price != current.unit_price, due_date != current.due_date, note != current.note)
            changes[pk] = (qty, unit_price, due_date, note, flags)
        note = data.get("note", locked.note) or ""
        changed = bool(added or dropped or note != locked.note or any(any(flags) for *_values, flags in changes.values()))
        if not changed:
            if not _revision_refused(locked):
                locked.saved_message = NO_CHANGE_MESSAGE
                return locked
        else:
            for pk, (qty, unit_price, due_date, line_note, flags) in changes.items():
                current = live[pk]
                current.qty, current.unit_price, current.due_date, current.note = qty, unit_price, due_date, line_note
                current.amount = round_amount(partner, qty * unit_price)
                if any(flags):
                    current.line_status_code = edi_codes.LINE_CHANGED
                    current.line_status_reason_code = _reason_code(*flags)
                else:
                    current.line_status_code, current.line_status_reason_code = edi_codes.LINE_NEW, ""
                current.save(
                    update_fields=["qty", "unit_price", "due_date", "note", "amount", "line_status_code", "line_status_reason_code"]
                )
            for line in dropped:
                line.line_status_code, line.line_status_reason_code = edi_codes.LINE_DELETED, ""
                line.save(update_fields=["line_status_code", "line_status_reason_code"])
            next_no = max((row.line_no for row in locked.lines.all()), default=0) + 1
            for offset, line in enumerate(added):
                row = _add_line(locked, line, next_no + offset)
                row.line_status_code = edi_codes.LINE_ADDED
                row.save(update_fields=["line_status_code"])
            locked.note = note
        locked.revision_no += 1
        locked.purpose_code = edi_codes.PURPOSE_CHANGE
        rows = list(annotate_received(locked.lines.exclude(line_status_code=edi_codes.LINE_DELETED)))
        if rows and all(row.remaining_total <= 0 for row in rows):
            locked.status = PurchaseOrder.STATUS_DELIVERED
        locked.save()
        _save_ext(locked, ext)

    # 相手への HTTP は atomic の外 (R17)。connect と purchasing の循環を避けるため関数の中で import する。
    from seizou.connect import services as connect_services

    outbox = connect_services.send_order(PurchaseOrder.objects.get(pk=locked.pk), user)
    return _with_delivery_notice(PurchaseOrder.objects.get(pk=locked.pk), outbox)


def _revision_refused(purchase_order):
    """今の版 (版 2 から) を相手が断ったか (今の版の最初の送った注文の行の reply_type が "RE")。逆の関係 outbox_messages を読むだけ。"""
    if purchase_order.revision_no < 2:
        return False
    outbox = getattr(purchase_order, "outbox_messages", None)
    if outbox is None:
        return False
    first = outbox.filter(revision_no=purchase_order.revision_no).order_by("pk").first()
    return first is not None and first.reply_type == edi_codes.REPLY_REJECTED


# --- 受入 (協力企業管理の 2 つ目の画面。受け入れた数 × 発注の単価が支払の金額) ---


def receive(line, qty, received_on, user, *, note=""):
    """発注の明細に受入を 1 件入れる。金額は 受け入れた数 × 明細の単価 を発注の取引先の方法で丸める (R03 の形)。

    断るのは「取消」の発注と 0 以下の数量だけ (「断られた」や「作成」の発注も受け入れる = 現物優先)。
    受入の残より多くても止めない。stock の部品があれば、同じ atomic の中で在庫の入庫も作る。
    全部の明細で受入の数が明細の数量以上になれば、発注を「納品された」(STATUS_DELIVERED) にする。
    「相手が受け入れた」の発注に一部の受入が入ると「一部納品」(決め C45-2 (11-07 00:2x) と決め C97-1 (11-07 06:38)。
    「下書き」「送った」「相手が保留した」「相手が条件付で受けた」は一部の受入では変えない。後で届く注文回答を当て、
    相手と話す・発注を直す仕事を一覧とタブから消さないため)。
    seizou どうしで送った発注なら、atomic を抜けた後に検収を相手へ送り、送った後の列を持つ受入を返す
    (送れなくても例外にしない。受入は成立する)。
    """
    qty = _dec(qty)
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=line.purchase_order_id)
        if locked.status == PurchaseOrder.STATUS_CANCELLED:
            raise ValueError(
                "受け入れられません。取り消した発注です。取消していない発注の明細を選んでください。"
            )
        if not qty > 0:
            raise ValueError("受け入れられません。数量が 0 以下です。数量は 0 より大きい数にしてください。")
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
            # 取り消した明細 (明細状態コード 2) は「納品された」の見方に入れない (受入は断らない = 現物優先。候補に出さないだけ)。
            lines = list(annotate_received(locked.lines.exclude(line_status_code=edi_codes.LINE_DELETED)))
            if lines and all(row.remaining_total <= 0 for row in lines):
                locked.status = PurchaseOrder.STATUS_DELIVERED
                locked.save(update_fields=["status"])
            elif locked.status == PurchaseOrder.STATUS_ACCEPTED:
                locked.status = PurchaseOrder.STATUS_PARTIAL
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
    取り消した発注の差は 0 (受け入れない発注に残を出さない。精査 A7 の 1-1 の S9)。
    発注の一覧が使う。読むだけ。
    """
    ordered = (
        PurchaseOrderLine.objects.filter(purchase_order=OuterRef("pk"))
        # 取り消した明細 (明細状態コード 2) の数は明細の数量の合計に入れない。受入の数の合計は外さない (受け入れた現物は数える)。
        .exclude(line_status_code=edi_codes.LINE_DELETED)
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
        receive_remaining=Case(
            When(status=PurchaseOrder.STATUS_CANCELLED, then=Value(_ZERO)),
            default=ExpressionWrapper(F("ordered_total") - F("received_total"), output_field=_QTY_FIELD),
            output_field=_QTY_FIELD,
        )
    )


def annotate_order_notices(queryset):
    """発注の queryset に、相手から届いた出荷の知らせの数 (notice_count) を足す。読むだけ。

    発注の一覧と詳細の 2 秒ごとの更新が、行の状態の字 (purchasing_tags.live_state) に使う。明細ごとの知らせを発注で数える
    Subquery (annotate_order_received と同じ形。join を重ねない)。connect を import せず、逆の関係 shipping_notices を
    model の _meta から引く。connect の無い会社では 0。
    """
    try:
        field = PurchaseOrderLine._meta.get_field("shipping_notices")
    except FieldDoesNotExist:
        return queryset.annotate(notice_count=Value(0, output_field=IntegerField()))
    notices = (
        field.related_model.objects.filter(line__purchase_order=OuterRef("pk"))
        .order_by()
        .values("line__purchase_order")
        .annotate(n=Count("pk"))
        .values("n")[:1]
    )
    return queryset.annotate(
        notice_count=Coalesce(Subquery(notices, output_field=IntegerField()), Value(0), output_field=IntegerField())
    )


# 受けすぎの知らせ (受入の数の合計が明細の数量を超えた明細)。知らせるだけで、受入は止めない (receive の決まりのまま)。
OVER_RECEIPT_MESSAGE = "受入の数 {received} が発注の数 {qty} を超えています。"


def over_receipt_message(line):
    """明細 (annotate_received の received_total つき) の受けすぎの知らせの字。超えていなければ空の字。読むだけで、問い合わせをしない。"""
    received = getattr(line, "received_total", None)
    if received is None or not received > line.qty:
        return ""
    return OVER_RECEIPT_MESSAGE.format(received=_qty_text(received), qty=_qty_text(line.qty))


def over_receipt_lines(line_ids):
    """明細の番号の集まりのうち、受入の数の合計が数量を超えた明細 (received_total つき) の list。

    集計は 1 回 (annotate_received の Sum を HAVING で絞る。行ごとに数えない)。受入の一覧の黄の知らせが使う。読むだけ。
    """
    lines = PurchaseOrderLine.objects.filter(pk__in=line_ids).select_related("purchase_order", "item")
    return list(
        annotate_received(lines).filter(received_total__gt=F("qty")).order_by("purchase_order__po_no", "line_no", "pk")
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


def notice_rests(line):
    """出荷の知らせの行ごとの、まだ受け入れていない数。返す物は [{"notice", "rest", "rest_text"}, …] (並びは shipping_notice の rows と同じ出荷日の古い順)。

    受入の数の合計 (received_total。無ければ received_qty) を知らせの古い順に当てていき、行の数から当てた数を引いた物をその行の rest にする (0 より小さくしない)。
    rest の合計は 知らせの合計 − 受入の合計 (受入の方が多ければ 0)。読むだけ。発注の詳細の「受入を記録する」の数量に使う (精査 A7 の 1-1 の S6)。
    """
    received = getattr(line, "received_total", None)
    if received is None:
        received = received_qty(line)
    left = _dec(received)
    rests = []
    for row in line.shipping_notice["rows"]:
        used = min(row.qty, max(left, _ZERO))
        left -= used
        rest = row.qty - used
        rests.append({"notice": row, "rest": rest, "rest_text": _qty_text(rest)})
    return rests


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
