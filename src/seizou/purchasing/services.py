"""purchasing の公開する関数 (部品の公開する関数の設計の決め)。画面とほかの部品は、この関数だけを通して発注を書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (services の書き方の設計の決め)。
- user は記録のために受け、変更の履歴 (core.services.record_history。作者の決めの伝票の「発注」) に書く。書く前に core.history.saved_state で前を取り、
  書いて拡張の表も書いた後に、同じ transaction.atomic の中で残す (止まって戻ったら履歴も残らない)。明細の行は発注の詳細の「履歴」に出る (PurchaseOrderLine.history_owner)。
  明細をまとめて書く所 (queryset の update と bulk_update) は、書く前に行ごとに前を取り、書いた後に行ごとに残す。
  相手の注文回答で書く所 (connect の受け口が user を None で呼ぶ) は、人の字を「<発注の取引先の名前> の seizou」にする (_partner_by)。
- ext (会社ごとの項目) は core の fields.save_ext に渡す。登録が無ければ save_ext が ValueError を投げる。
- 決まりの番号の無い入力の誤り (協力企業でない・明細が無い・知らない鍵・数量と単価が 0 以下) は日本語の ValueError。
  RULES.md の R02 の対象に発注は入っていないので RuleViolation にしない。DB の制約は付ける。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- 保存と同時に送る (02)。発注を保存したトランザクションを抜けた後で、取引先に向き「送る」の
  つながりがあれば connect.services.send_order を呼ぶ。相手への HTTP は atomic の外 (R17)。
- つながりの無い取引先への発注は、発注書を seizou の外 (FAX・郵送・メールなど) で送った後に mark_sent_outside で「送った」にし、
  発注書を送った日時 (sent_outside_at) を残す (依頼書 N439)。相手の答えを待つ発注は waiting_reply (読むだけ。この列が空の「送った」の発注)。
- 明細の辞書の鍵は item・qty・unit・unit_price・price_basis・due_date・note・process・quote_no。
  price_basis は単価の単位 (unit か lump。無ければ unit = 今と同じ)。
  process は協力企業に出す工程 (masters.Process か None)、quote_no は単価表から写した見積の番号 (無ければ空の字。作者の決め)。
  明細の数量単位コード (unit_code。作者の決め) は鍵で受けず、_add_line が単位の字から決めて書く。
- 新規登録 (create_purchase_order) の明細だけは、鍵 workorder_step (協力企業に出す指示書の工程) と handed_on (現物を渡した日) も受ける
  (見比べのまとめの M01)。workorder_step のある明細は、部品と工程を指示書の工程から写す。送った発注の変更の鍵 (_LINE_KEYS_ON_CHANGE) には足さない。
  渡した日は set_handed_on で直す。出ている数・まだ渡していない数・工程ごとの集計は out_qty・waiting_qty・annotate_subcontract (読むだけ)。
- 指示書の工程を指す明細の受入は、同じ transaction.atomic の中で progress.services.record_progress を呼び、受け入れた数を良品の数にした
  実績をその工程に作って受入に結ぶ (RULES.md R32。progress の部品があるときだけ。関数の中で import)。受入を伴わない完了は実績を作らない。
- 受入は receive。受入の金額は masters.services.delivery_amount (一式は全部を受け入れた回で全額)。受入の数と残は列に持たず received_qty・remaining_qty・annotate_received で出す。
- 受入は stock の部品があれば在庫の入庫も作る (関数の中で import。同じ transaction.atomic の中)。
- 受入の後、seizou どうしで送った発注 (送った注文の行がある物) なら connect.services.send_acceptance で検収を相手へ送る (HTTP は atomic の外 = R17)。
- 注文回答の 4 つの答え (受諾 AP = 相手が受け入れた・条件付受諾 CA = 相手が条件付で受けた・辞退 RE = 断られた・保留 AJ = 相手が保留した) は、
  connect の受け口が mark_accepted・mark_conditional・mark_rejected・mark_on_hold で答えを待っている発注 (送った・相手が保留した) に当てる。
- 「相手が受け入れた」の発注に一部の受入が入ると「一部納品」、全部そろえば「納品された」。受入が先の発注に後から受諾が届いたときも「一部納品」。
- 完了の印は mark_completed と clear_completed だけが書く。受入と発注の変更では書き換えない (印と受入の数を結ばない)。
  明細が終わっているのは、完了の印が真か、受入の合計が数量以上のとき (RULES.md R25。残は remaining_qty・annotate_received の 1 か所)。
  完了は相手の seizou へ送らない (相手にも止めてほしいときは、完了を外してから「変更」で数を直して送る)。
- 仕入明細は create_self_invoice。受入をまとめ、消費税は billing の 1 か所 (consumption_tax) で合計に 1 回、期間と支払期日は billing の closing_period・payment_due_date を呼ぶ (式を写さない)。
  候補の受入は self_invoice_receipts、詳細の 60 日の黄の 1 行の字は due_date_note (どちらも読むだけ)。
"""

from __future__ import annotations

from decimal import Decimal
import uuid

from django.db import transaction
from django.utils import timezone

from seizou.core import edi_codes, history
from seizou.core.services import next_number, record_history
from seizou.core.services import keep_report_copy, note_copy_failed
from seizou.masters.services import PRICE_BASIS_UNIT, line_amount, refuse_inactive, refuse_unknown_price_basis
from seizou.masters.services import PRICE_BASIS_LUMP, delivery_amount, reaches_line_qty
from seizou.masters.services import unit_code_for
from seizou.masters.services import item_prices

from .models import PurchaseOrder, PurchaseOrderLine
from .models import Receipt
from .models import SelfInvoice, SelfInvoiceLine
from .models import _qty_text
from .reports import ORDER_SHEET_TEMPLATE, purchase_order_context

from datetime import datetime, timedelta
from typing import NamedTuple

from django.apps import apps
from django.db.models import DecimalField, ExpressionWrapper, F, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Count, IntegerField
from django.db.models import Case, When
from django.db.models import Exists
from django.db.models import BooleanField

# 数の合計の型 (PurchaseOrderLine.qty と同じ桁)
_QTY_FIELD = DecimalField(max_digits=12, decimal_places=2)
_ZERO = Decimal("0")

_LINE_KEYS = frozenset({"item", "qty", "unit", "unit_price", "price_basis", "due_date", "note", "process", "quote_no"})
#: 新規登録の明細の辞書の鍵 (指示書の工程と渡した日は新規登録だけ。送った発注の変更で足す明細は、今までどおり指示書の工程を持たない)
_LINE_KEYS_ON_CREATE = _LINE_KEYS | {"workorder_step", "handed_on"}
#: 指示書の工程を指す明細の部品か工程が、指示書の工程と違うときの断りの字 ({what} は「部品」か「工程」)
_STEP_MISMATCH_MESSAGE = (
    "保存できません。明細 {line_no} の{what}が、指示書 {wo_no} の工程 {seq} {name} と違います。"
    "指示書の工程から出すときは、部品と工程を変えずに保存してください。"
)
#: 指示書の工程の無い明細に渡した日を入れたときの断りの頭 (後ろに次にすることを続ける)
_HANDED_WITHOUT_STEP_MESSAGE = "保存できません。渡した日は、指示書の工程を指す明細だけに入れられます。"

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


def _refuse_bad_lines(lines, allowed=_LINE_KEYS):
    for line in lines:
        unknown = sorted(set(line) - allowed)
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


def _partner_by(purchase_order) -> str:
    """相手の注文回答で書く所の、変更の履歴の人の字 (受け口は人が無く user が None。人があれば core は人の字を使う)。"""
    return f"{purchase_order.partner.name} の seizou"


def _line_states(purchase_order):
    """明細ごとの変更の履歴の前 (明細, saved_state) の並び。queryset の update と bulk_update は行ごとの save を通らないため、書く前に取る。"""
    return [(line, history.saved_state(line)) for line in purchase_order.lines.all()]


def _record_lines(states, user, *, by=""):
    """_line_states で取った前と今の値を比べ、変わった明細ごとに変更の履歴を 1 行ずつ残す (変わらない明細は残さない)。"""
    for line, before in states:
        record_history(line, user, before=before, by=by)


def _add_line(purchase_order, line, line_no):
    """発注の明細を 1 行作る。単位が空なら部品の単位を写し、数量単位コードは単位の字が部品の単位と同じなら部品のコード、違えば名前から引く (一覧に無い字はコードを空にし、字はそのまま)。

    訳: 送り合いの数量に、中小企業共通 EDI の数量単位コード (付表 1 の注文数量単位コード。共通必須) を付け、渡された字は変えずに残すため (画面の入力は一覧の名前だけを渡す)。出所 = 作者の決め・中小企業共通 EDI 付表 1。
    変えてよい条件 = services を呼ぶほかの道でも、一覧に無い字を受けないと決めたとき。
    """
    item = line["item"]
    qty = _dec(line["qty"])
    unit_price = _dec(line["unit_price"])
    price_basis = line.get("price_basis", PRICE_BASIS_UNIT)
    unit = line.get("unit") or item.unit
    unit_code = item.unit_code if unit == item.unit else unit_code_for(unit)
    return PurchaseOrderLine.objects.create(
        purchase_order=purchase_order,
        line_no=line_no,
        item=item,
        qty=qty,
        unit=unit,
        unit_code=unit_code,
        unit_price=unit_price,
        price_basis=price_basis,
        amount=line_amount(purchase_order.partner, qty, unit_price, price_basis),
        due_date=line.get("due_date"),
        note=line.get("note", ""),
        # 工程の種類 (協力企業に出すか) は services で断らない (入力の欄の選択肢が絞る。作者の決め)
        process=line.get("process"),
        quote_no=line.get("quote_no", ""),
        # 指示書の工程と渡した日は新規登録の鍵だけ (変更で足す明細は鍵を受けないので空のまま)
        workorder_step=line.get("workorder_step"),
        handed_on=line.get("handed_on"),
    )


def _from_workorder_steps(lines):
    """新規登録の明細の辞書の並びを返す。指示書の工程 (workorder_step) を指す明細は、部品と工程を指示書の工程から写した新しい辞書にする
    (渡された辞書は変えない)。部品は指示書の受注の明細の部品、工程は指示書の工程の工程。

    断る (日本語の ValueError): 指示書の工程を指す明細の辞書に item か process があり、写す物と違う (部品を先に見る。process が None も違う)・
    指示書の工程の無い明細に渡した日 (handed_on) がある。
    指示書の工程が協力企業に出す工程でなくても、指示書が取消でも、1 つの工程に明細が 2 つあっても断らない (入口の選ぶ欄が絞る。明細の工程と同じ考え)。
    訳: 指示書の工程を指す明細の部品と工程が違うと、戻りを実績にする先と受け取った物が食い違い、記録が成り立たないため (表の作りの上の断り。ポカよけではない)。
    渡した日は指示書の工程に出した現物の記録なので、指示書の工程の無い明細には持たない。
    出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M01)。
    変えてよい条件 = 1 つの明細で別の部品に加工して戻す会社があると分かったとき。
    """
    resolved = []
    for line_no, line in enumerate(lines, start=1):
        step = line.get("workorder_step")
        if step is None:
            if line.get("handed_on") is not None:
                raise ValueError(_HANDED_WITHOUT_STEP_MESSAGE + "指示書の工程を選ぶか、渡した日を空にしてください。")
            resolved.append(line)
            continue
        item = step.workorder.order_line.item
        process = step.process
        for key, copied, what in (("item", item, "部品"), ("process", process, "工程")):
            if key in line and getattr(line[key], "pk", line[key]) != copied.pk:
                raise ValueError(
                    _STEP_MISMATCH_MESSAGE.format(
                        line_no=line_no, what=what, wo_no=step.workorder.wo_no, seq=step.seq, name=process.name
                    )
                )
        resolved.append({**line, "item": item, "process": process})
    return resolved


def _has_send_connection(partner):
    """取引先に向き「送る」のつながりがあるか (読むだけ。connect の model を import しない)。connect の無い会社では関係が無いので偽。"""
    connections = getattr(partner, "connections", None)
    if connections is None:
        return False
    return connections.filter(direction=connections.model.DIRECTION_SEND).exists()


def annotate_can_send(queryset):
    """発注の queryset に、取引先に向き「送る」のつながりがあるか (can_send。真偽) を足す (読むだけ。副問い合わせなので問い合わせの数は増えない)。

    見方は _has_send_connection と、明細の can_send (_live_lines) と同じ。connect の無い会社では偽。
    発注の詳細 (purchasing.views) が使い、頭の右の緑のボタンと黄の 1 行 (#po-alert) が、同じ値を can_mark_sent_outside で読む
    (発注の詳細は 2 秒ごとに取り直すので、つながりを読む問い合わせを足さないため。依頼書 N439)。
    """
    try:
        connection_model = PurchaseOrder._meta.get_field("partner").related_model._meta.get_field("connections").related_model
    except FieldDoesNotExist:
        return queryset.annotate(can_send=Value(False, output_field=BooleanField()))
    sendable = connection_model.objects.filter(partner_id=OuterRef("partner_id"), direction=connection_model.DIRECTION_SEND)
    return queryset.annotate(can_send=Exists(sendable))


def create_purchase_order(partner, lines, user, *, order_date, note="", ext=None, send=True):
    """発注を作る。明細は lines の並びの順に行番号を 1 から付ける。

    保存の後、取引先に向き「送る」のつながりがあれば相手の受け口へ送る (送れなくても例外にせず、
    送った注文の表に返事を残し、発注は「作成」のまま。届かなければ、返す発注に黄の知らせの字を付ける)。
    つながりが無ければ送らない。
    send が偽なら、つながりがあっても送らない (作り物のデータの種が、相手に送らずに発注を入れるため)。
    明細の鍵 workorder_step (指示書の工程) と handed_on (渡した日) は新規登録だけで受ける。workorder_step のある明細は部品と工程を
    指示書の工程から写し (_from_workorder_steps)、使用停止の部品の確かめは写した部品で行う。送る注文の明細の項目は今のまま (2 つは送らない)。
    発行の時の帳票の写し (第 1 版) を残す (R34)。描けなければ、返す発注の saved_note に字を足して黄にする (core.services.keep_report_copy・note_copy_failed)。
    """
    _refuse_non_subcontractor(partner)
    if not lines:
        raise ValueError("保存できません。明細がありません。明細を 1 行以上入れてください。")
    _refuse_bad_lines(lines, _LINE_KEYS_ON_CREATE)
    lines = _from_workorder_steps(lines)
    refuse_inactive(partner, [line.get("item") for line in lines])
    with transaction.atomic():
        purchase_order = PurchaseOrder.objects.create(
            po_no=next_number("purchase_order"),
            partner=partner,
            order_date=order_date,
            note=note,
        )
        created = [_add_line(purchase_order, line, line_no) for line_no, line in enumerate(lines, start=1)]
        _save_ext(purchase_order, ext)
        record_history(purchase_order, user)
        for row in created:
            record_history(row, user)
        copied = keep_report_copy(
            purchase_order,
            user,
            title="発注書",
            template=ORDER_SHEET_TEMPLATE,
            context=purchase_order_context(purchase_order),
            revision=purchase_order.revision_no,
        )

    # 相手への HTTP は atomic の外 (R17)。connect と purchasing の循環を避けるため関数の中で import する。
    if send and _has_send_connection(partner):
        from seizou.connect import services as connect_services

        outbox = connect_services.send_order(purchase_order, user)
        result = _with_delivery_notice(PurchaseOrder.objects.get(pk=purchase_order.pk), outbox)
    else:
        result = PurchaseOrder.objects.get(pk=purchase_order.pk)
    return note_copy_failed(result) if copied is None else result


def cancel_purchase_order(purchase_order, user, *, reason=""):
    """発注を取消にする。状態が「作成」か「断られた」なら送らずに取り消す (断られた発注は相手が受けていないので、連絡なしに取り消せる。作者の決め)。

    状態が「送った」「相手が受け入れた」で、相手がどの明細も出荷していない (can_cancel_sent_order) なら、同じ atomic で状態を取消・版を 1 上げ・
    目的を "1" (打切り)・全部の明細を明細状態コード 2 にし、atomic の外で取消の版を相手へ送る (connect.services.send_order)。届いたかの知らせを付けて返す。
    一部でも相手が出荷済みの明細があれば、取消は送らず「変更」の道を案内する字で断る。「納品された」「取消」とつながりの無い送った発注も断る。
    発注書を seizou の外で送った発注 (sent_outside_at が空でない = mark_sent_outside で「送った」にした発注) は、状態が CHANGEABLE_STATUSES でも
    「下書き」の取消と同じ道で、相手へ送らずに取り消す (つながりの有無を見ない。版・目的・明細状態コードも変えない)。
    受入が 1 つでもあれば (known_shipped_qty は受入の数も見る)、今の断り (_CANCEL_SHIPPED_MESSAGE) のまま。
    reason が空でなければ、備考の末尾に改行して「取消の理由: …」を足す (受注の取消と同じ形)。
    訳: 相手が 1 個も出していない送った発注は、取消 (中小企業共通 EDI の注文書目的コード 1 打切り) を相手へ送れるようにし、出した後は数を直す変更で残りを止めるため。出所 = 作者の決め (11-06 23:2x)・作者の決め (11-06 23:1x)・作者の決め (11-07 06:33。取消を送れるのは相手が 1 個も出していない間だけ)。
    変えてよい条件 = 作者の決めの答えが変わったとき。
    訳 (seizou の外で送った発注): 「発注書を送った」を押した後に、取消も変更もできない発注を作らないため。seizou は相手に何も送っていないので、
    取消を送る先が無い。取消は、取引先に自分で伝える (取消の確かめのカードに 1 行を出す)。出所 = 本体の決め 11-12 (依頼書 N439。本人の決め 11-12 14:3x・18:2x を食い違い無く作るための細かい所)。
    変えてよい条件 = 本人が、seizou の外で送った発注の取消の扱いを決め直したとき。
    """
    sent = False
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if locked.status in CHANGEABLE_STATUSES:
            lines = _live_lines(locked)
            if any(known_shipped_qty(line) > 0 for line in lines):
                raise ValueError(_CANCEL_SHIPPED_MESSAGE)
            # seizou の外で送った発注は、相手へ送らずに取り消す (sent は偽のまま。下の「下書き」の取消と同じ道)
            if locked.sent_outside_at is None:
                if not _has_send_connection(locked.partner):
                    raise ValueError(
                        f"取り消せません。取引先 {locked.partner.name} へのつながりが無いので、取消を相手へ送れません。"
                        "設定の画面の「取引先との接続」で登録してください。"
                    )
                sent = True
        elif locked.status not in (PurchaseOrder.STATUS_DRAFT, PurchaseOrder.STATUS_REJECTED):
            raise ValueError(_CANCEL_AFTER_SENT_MESSAGE.format(status=locked.get_status_display()))
        before = history.saved_state(locked)
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
        states = []
        if sent:
            # 取消の版 (注文書目的コード 1 打切り)。全部の明細を明細状態コード 2 にする (行は残す)。
            locked.revision_no += 1
            locked.purpose_code = edi_codes.PURPOSE_CANCEL
            states = _line_states(locked)
            locked.lines.update(line_status_code=edi_codes.LINE_DELETED, line_status_reason_code="")
        locked.save()
        record_history(locked, user, before=before)
        _record_lines(states, user)
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
        before = history.saved_state(locked)
        limit = PurchaseOrder._meta.get_field("reply_note").max_length
        locked.replied_at = timezone.now()
        locked.reply_note = (reason if isinstance(reason, str) else "")[:limit]
        locked.reply_message_id = reply_message_id
        locked.save(update_fields=["replied_at", "reply_note", "reply_message_id"])
        record_history(locked, user, before=before, by=_partner_by(locked))
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
            before = history.saved_state(locked)
            locked.status = PurchaseOrder.STATUS_SENT
            locked.save(update_fields=["status"])
            record_history(locked, user, before=before, by=_partner_by(locked))
        return locked


#: 「発注書を送った」を記録した後の知らせの字 (入力の土台が「保存しました。」の代わりに出す)
SENT_OUTSIDE_MESSAGE = "発注書を送った事を記録しました。状態を「送った」にしました。"


def can_mark_sent_outside(purchase_order) -> bool:
    """「発注書を送った」を出す発注か (読むだけ): 状態が「下書き」で、取引先に向き「送る」のつながりが無い。

    状態を先に見て、外ならつながりを読まない。つながりは、発注に annotate_can_send の値 (can_send) があればそれを読み (問い合わせなし)、
    無ければ _has_send_connection で読む。発注の詳細の頭の右の緑のボタンと、黄の 1 行 (#po-alert) が使う。押した時の断りは mark_sent_outside が錠の中で見直す。
    """
    if purchase_order.status != PurchaseOrder.STATUS_DRAFT:
        return False
    can_send = getattr(purchase_order, "can_send", None)
    if can_send is None:
        can_send = _has_send_connection(purchase_order.partner)
    return not can_send


def mark_sent_outside(purchase_order, user):
    """つながりの無い取引先へ発注書を seizou の外 (FAX・郵送・メールなど) で送った事を記録し、「下書き」の発注を「送った」にする。

    行を錠で引き、状態を「送った」にし、発注書を送った日時 (sent_outside_at) に今の日時を書き、変更の履歴を 1 行残す (人は user)。
    返す発注に済んだ知らせの字 (saved_message = SENT_OUTSIDE_MESSAGE) を付ける (表に書かない)。相手へは何も送らない (HTTP なし)。
    断る (頭は「記録できません。」): 状態が「下書き」でない・取引先に向き「送る」のつながりがある (つながりのある取引先は「再送」で送る)。
    押した後の発注は、ダッシュボードの「まだ送っていない発注」(状態が「下書き」) と「返事を待つ発注」(waiting_reply) のどちらにも数えない。
    受入は今までどおり (receive)。取消は相手へ送らずに取り消す (cancel_purchase_order)。押し間違えを外す操作は無い (取り消して入れ直す)。
    訳: つながりの無い取引先への発注は、発注書を刷って seizou の外で送る。送った後に押すボタンが無く、発注が「下書き」のまま残り、
    受入が全部済むまで「まだ送っていない発注」に数え続けたため。seizou では答えが届かない発注なので、返事を待つ発注には数えない。
    出所 = 本人の決め 11-12 14:3x (「足す」) と 18:2x (ボタンの名前と押す前の説明の 1 文)。列で見分ける形と取消の扱いは本体の決め 11-12 (依頼書 N439)。
    変えてよい条件 = 本人が、つながりの無い取引先への発注の送り方 (新しい状態を足す・押し間違えを外す操作を作る、など) を決め直したとき。
    """
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().select_related("partner").get(pk=purchase_order.pk)
        if locked.status != PurchaseOrder.STATUS_DRAFT:
            statuses = dict(PurchaseOrder.STATUS_CHOICES)
            raise ValueError(
                f"記録できません。「発注書を送った」を記録できるのは、状態が「{statuses[PurchaseOrder.STATUS_DRAFT]}」の発注だけです。"
                f"この発注の状態は「{locked.get_status_display()}」です。"
            )
        if _has_send_connection(locked.partner):
            raise ValueError(
                f"記録できません。取引先 {locked.partner.name} とは seizou でつながっています。「再送」で注文を送ってください。"
            )
        before = history.saved_state(locked)
        locked.status = PurchaseOrder.STATUS_SENT
        locked.sent_outside_at = timezone.now()
        locked.save(update_fields=["status", "sent_outside_at"])
        record_history(locked, user, before=before)
    locked.saved_message = SENT_OUTSIDE_MESSAGE
    return locked


def mark_accepted(purchase_order, user, *, reply_message_id=None, replied_at=None):
    """答えを待っている発注 (送った・相手が保留した) を「相手が受け入れた」にする。それ以外の状態は ValueError。

    注文回答の日時 (こちらに届いて当てた時刻。replied_at を渡せばその日時 = 種の読み込みが使う) を書く。
    reply_message_id は相手の注文回答の番号
    (connect の受け口が渡す。同じ番号の 2 回目は受け口が見分けて呼ばない。DB の一意の制約もある)。
    注文回答のタイプを受諾 (AP) にし、明細の答えを空に戻す。
    受入が先に入った発注は「一部納品」にする (作者の決め (11-07 00:2x) と作者の決め (11-07 06:40)。受入と注文回答の届く順に関わらず、一部だけ戻った発注を一部納品にするため)。
    全部そろった発注は receive が先に「納品された」にしているので、ここには来ない。
    訳: 注文回答は答えを待っている発注 (送った・相手が保留した) にだけ当て、届く順が前後しても記録を戻さないため。保留の後は本当の答えが届く (中小企業共通 EDI の注文回答タイプコードの保留 AJ)。出所 = 作者の決め (11-07 00:0x) と中小企業共通 EDI の注文回答の項目。
    変えてよい条件 = 答えを当てる状態の決めが変わったとき。
    """
    with transaction.atomic():
        locked = _lock_waiting_order(purchase_order, PurchaseOrder.STATUS_ACCEPTED)
        before = history.saved_state(locked)
        locked.status = PurchaseOrder.STATUS_PARTIAL if locked.receipts.exists() else PurchaseOrder.STATUS_ACCEPTED
        locked.replied_at = replied_at or timezone.now()
        locked.reply_type = edi_codes.REPLY_ACCEPTED
        fields = ["status", "replied_at", "reply_type"]
        if reply_message_id is not None:
            locked.reply_message_id = reply_message_id
            fields.append("reply_message_id")
        locked.save(update_fields=fields)
        by = _partner_by(locked)
        record_history(locked, user, before=before, by=by)
        _clear_line_replies(locked, user, by=by)
        return locked


def mark_rejected(purchase_order, user, *, reason, reply_message_id=None):
    """答えを待っている発注 (送った・相手が保留した) を「断られた」にする。それ以外の状態は ValueError。

    reason は相手の断りの理由 (500 字で切る。空でもよい)。注文回答の日時と番号は mark_accepted と同じ。
    注文回答のタイプを辞退 (RE) にし、明細の答えを空に戻す。
    訳: 注文回答は答えを待っている発注 (送った・相手が保留した) にだけ当て、届く順が前後しても記録を戻さないため。保留の後は本当の答えが届く (中小企業共通 EDI の注文回答タイプコードの保留 AJ)。出所 = 作者の決め (11-07 00:0x) と中小企業共通 EDI の注文回答の項目。
    変えてよい条件 = 答えを当てる状態の決めが変わったとき。
    """
    with transaction.atomic():
        locked = _lock_waiting_order(purchase_order, PurchaseOrder.STATUS_REJECTED)
        before = history.saved_state(locked)
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
        by = _partner_by(locked)
        record_history(locked, user, before=before, by=by)
        _clear_line_replies(locked, user, by=by)
        return locked


def mark_conditional(purchase_order, user, *, lines, reply_message_id=None, replied_at=None):
    """答えを待っている発注 (送った・相手が保留した) を「相手が条件付で受けた」にし、相手の明細ごとの答えを明細に写す。それ以外の状態は ValueError。

    中小企業共通 EDI の条件付受諾の項目を元にした (ガイドブック 3.2.2「発注者は受注者と協議し、合意した条件で注文変更処理を行う」)。
    発注の値は変えない (直すのは発注元が変更を送るとき = change_purchase_order)。
    lines の形は record_line_replies と同じ。注文回答の日時と番号は mark_accepted と同じ。注文回答のタイプは条件付受諾 (CA)。
    訳: 相手の答えを発注に残し、発注元が直すかを決められるようにするため。出所 = 作者の決め (11-07 00:0x)・作者の決め (11-06 23:1x) と中小企業共通 EDI の注文回答の項目。
    変えてよい条件 = 条件付受諾の扱いの決めが変わったとき。

    取適法: 合意した単価・数量・納期は、発注の変更 (change_purchase_order) で相手へ送ると、発注の中身を電磁的方法で示した形になる。
    直さずに進めると、発注と相手の受注の値が違ったままになり、発注の内容を書面で示す決まりに当たるおそれがある。
    この関数は止めない (詳細の黄の 1 行で「変更」を案内するだけ)。確かめた資料 = 作り手の知識 (公正取引委員会の資料は未読)。
    """
    with transaction.atomic():
        locked = _lock_waiting_order(purchase_order, PurchaseOrder.STATUS_CONDITIONAL)
        before = history.saved_state(locked)
        record_line_replies(locked, lines, user)
        locked.status = PurchaseOrder.STATUS_CONDITIONAL
        locked.replied_at = replied_at or timezone.now()
        locked.reply_type = edi_codes.REPLY_CONDITIONAL
        fields = ["status", "replied_at", "reply_type"]
        if reply_message_id is not None:
            locked.reply_message_id = reply_message_id
            fields.append("reply_message_id")
        locked.save(update_fields=fields)
        record_history(locked, user, before=before, by=_partner_by(locked))
        return locked


def mark_on_hold(purchase_order, user, *, reply_message_id=None, replied_at=None):
    """答えを待っている発注 (送った・相手が保留した) を「相手が保留した」にする。それ以外の状態は ValueError。

    注文回答のタイプを保留 (AJ) にし、明細の答えを空に戻す。注文回答の日時と番号は mark_accepted と同じ
    (流れの枠と段の帯の「回答」は注文回答の日時で読む。保留も注文回答の 1 つなので回答の段に印が付く)。
    訳: 相手がすぐ答えられない注文を、答えを待つ状態として見せ、後で届く本当の答えを当てられるようにするため。出所 = 作者の決め (11-07 00:0x。保留は受注を作らず会話で話す)。
    変えてよい条件 = 保留の扱いの決めが変わったとき。
    """
    with transaction.atomic():
        locked = _lock_waiting_order(purchase_order, PurchaseOrder.STATUS_ON_HOLD)
        before = history.saved_state(locked)
        locked.status = PurchaseOrder.STATUS_ON_HOLD
        locked.replied_at = replied_at or timezone.now()
        locked.reply_type = edi_codes.REPLY_PENDING
        fields = ["status", "replied_at", "reply_type"]
        if reply_message_id is not None:
            locked.reply_message_id = reply_message_id
            fields.append("reply_message_id")
        locked.save(update_fields=fields)
        by = _partner_by(locked)
        record_history(locked, user, before=before, by=by)
        _clear_line_replies(locked, user, by=by)
        return locked


def mark_change_accepted(purchase_order, user):
    """発注の変更 (第 2 版から) に相手の受諾 (AP) が届いたときに、connect の受け口が呼ぶ。返す物は取り直した発注。

    状態が「相手が条件付で受けた」なら「相手が受け入れた」(受入が 1 つでもあれば「一部納品」) にし、明細の答えを空に戻す。
    ほかの状態では何もしない。断る処理は無い。
    相手の条件に合わせて直した変更を相手が受け入れたら、条件の話し合いが済んだ印として状態を進める。
    変更への答えで発注の状態を変えない決まりの、ただ 1 つの例外。出所 = 作者の決め (11-07 00:0x。発注元の変更を待つ) と
    作者の決め (11-07 06:42。条件付で受けた発注を直して送った変更を相手が受け入れたら、状態を進めて明細の回答を消す)。
    呼ぶ所は connect.services の注文回答の受け口 (_receive_reply。今の版の変更への受諾が届いた時だけ。古い版と取消の版では呼ばない)。
    11-12 までは呼ぶ所がつながっていなかった (本人の決め 11-12 14:1x で、決めたとおりにつないだ)。
    """
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if locked.status != PurchaseOrder.STATUS_CONDITIONAL:
            return locked
        before = history.saved_state(locked)
        locked.status = PurchaseOrder.STATUS_PARTIAL if locked.receipts.exists() else PurchaseOrder.STATUS_ACCEPTED
        locked.save(update_fields=["status"])
        by = _partner_by(locked)
        record_history(locked, user, before=before, by=by)
        _clear_line_replies(locked, user, by=by)
        return locked


def record_line_replies(purchase_order, lines, user=None):
    """相手の注文回答の明細ごとの答えを、発注の明細に写す。呼ぶ側が発注を select_for_update した atomic の中で呼ぶ。

    lines は [{"line_no": int, "reply_type": "AP"|"CA"|"RE", "unit_price": Decimal|None, "qty": Decimal|None, "due_date": date|None}]。
    lines にある明細はその答え (条件付受諾 CA の明細だけ値を入れ、AP と RE は値を空)、lines に無い明細は受諾 (AP。EDI の既定) にして値を空にする。
    発注に無い行番号は ValueError (受け口が相手へ返す文。画面には出ない)。どれかの行番号が無ければ、どの明細も書かない。
    mark_conditional と、connect の版 2 からの答えが呼ぶ (明細の答えの写し方を 1 か所にする)。
    変わった明細ごとに変更の履歴を 1 行ずつ残す (bulk_update は行ごとの save を通らないので、書く前に前を取る)。人の字は user が無ければ
    「<発注の取引先の名前> の seizou」(相手の答えのため)。
    訳: 相手の明細ごとの答えを発注の明細に写し、発注に無い行番号の答えを当てずに相手へ返すため。出所 = 中小企業共通 EDI の注文回答明細回答タイプコード (既定 AP) と作者の決め (11-06 23:1x)。
    変えてよい条件 = 送り合いが行番号でなく明細の識別で明細を指すようになったとき。
    """
    by_no = {line.line_no: line for line in purchase_order.lines.all()}
    answers = {}
    for row in lines:
        number = row["line_no"]
        if number not in by_no:
            raise ValueError(f"注文回答の明細の行番号 {number} が発注 {purchase_order.po_no} にありません。")
        answers[number] = row
    states = [(line, history.saved_state(line)) for line in by_no.values()]
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
    _record_lines(states, user, by=_partner_by(purchase_order))


def _lock_waiting_order(purchase_order, target):
    """発注を select_for_update で引き、答えを待っている状態 (REPLY_WAITING) でなければ ValueError。target は行き先の状態の値。

    mark_accepted・mark_rejected・mark_conditional・mark_on_hold が使う 1 つの門 (断りの字の形をそろえるため)。
    訳: 注文回答は答えを待っている発注 (送った・相手が保留した) にだけ当て、届く順が前後しても記録を戻さないため。保留の後は本当の答えが届く (中小企業共通 EDI の注文回答タイプコードの保留 AJ)。出所 = 作者の決め (11-07 00:0x) と中小企業共通 EDI の注文回答の項目。
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


def _clear_line_replies(purchase_order, user, *, by=""):
    """発注の明細の答えを空に戻す (前の答えの明細の値が、次の答えの後に残って読み違えられないようにする)。

    queryset の update は行ごとの save を通らないので、書く前に明細ごとに前を取り、書いた後に変わった明細の変更の履歴を残す。
    """
    states = _line_states(purchase_order)
    purchase_order.lines.update(reply_type="", reply_unit_price=None, reply_qty=None, reply_due_date=None)
    _record_lines(states, user, by=by)


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
    つながりの無い取引先と取消の発注は send_order の ValueError のまま (作者の決め)。
    """
    # 相手への HTTP は send_order の中 (atomic の外。R17)。connect と purchasing の循環を避けるため関数の中で import する。
    from seizou.connect import services as connect_services

    outbox = connect_services.send_order(purchase_order, user)
    return _with_delivery_notice(PurchaseOrder.objects.get(pk=purchase_order.pk), outbox, resent=True)


# --- 送った発注の変更 (中小企業共通 EDI の注文変更の方式 2。相手が出荷し終えるまで) ---

#: 変更を送れる発注の状態
#: 変更と取消を相手へ送れる発注の状態 (作者の決め)。注文回答の 4 つのうち「相手が保留した」「相手が条件付で受けた」「一部納品」も入れる
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
    版を上げたときは、発行の時の帳票の写し (第 n 版) を残す (R34)。版を上げずに返すときは残さない。描けなければ、返す発注の saved_note に字を足して黄にする
    (core.services.keep_report_copy・note_copy_failed)。
    断る (頭は「保存できません。」): 状態が「送った」「相手が受け入れた」の外・相手が全部の明細を出荷し終えている・相手が出荷済みの明細の単価の変更と、
    出荷済みの数より少ない数と取消・取り消していない明細が 0 行・つながりが無い・数量と単価が 0 以下・知らない鍵・使用停止の部品。
    訳: 相手が出荷し終えるまでは発注を直して変更として送れるようにし、出した物の記録と食い違う直し (出荷済みの明細の単価・数・取消) を止めるため。出所 = 作者の決め (11-06 23:1x)・作者の決め (11-06 23:2x)・中小企業共通 EDI に合わせた作者の決め (受注者が出した物の金額は仕入明細の流れで確かめる)。
    変えてよい条件 = 作者の決めの答えが変わったとき。
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
        if not changed and not _revision_refused(locked):
            locked.saved_message = NO_CHANGE_MESSAGE
            return locked
        # 変更の履歴の前 (版の番号・目的のコード・明細の値と明細状態コードの前と後。作者の決め)
        before = history.saved_state(locked)
        states = _line_states(locked)
        created = []
        if changed:
            for pk, (qty, unit_price, due_date, line_note, flags) in changes.items():
                current = live[pk]
                current.qty, current.unit_price, current.due_date, current.note = qty, unit_price, due_date, line_note
                current.amount = line_amount(partner, qty, unit_price, current.price_basis)  # 一式の行は単価のまま (作者の決めの式)
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
                created.append(row)
            locked.note = note
        locked.revision_no += 1
        locked.purpose_code = edi_codes.PURPOSE_CHANGE
        rows = list(annotate_received(locked.lines.exclude(line_status_code=edi_codes.LINE_DELETED)))
        if rows and all(row.remaining_total <= 0 for row in rows):
            locked.status = PurchaseOrder.STATUS_DELIVERED
        locked.save()
        _save_ext(locked, ext)
        record_history(locked, user, before=before)
        _record_lines(states, user)
        for row in created:
            record_history(row, user)
        copied = keep_report_copy(
            locked,
            user,
            title="発注書",
            template=ORDER_SHEET_TEMPLATE,
            context=purchase_order_context(locked),
            revision=locked.revision_no,
        )

    # 相手への HTTP は atomic の外 (R17)。connect と purchasing の循環を避けるため関数の中で import する。
    from seizou.connect import services as connect_services

    outbox = connect_services.send_order(PurchaseOrder.objects.get(pk=locked.pk), user)
    result = _with_delivery_notice(PurchaseOrder.objects.get(pk=locked.pk), outbox)
    return note_copy_failed(result) if copied is None else result


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


def receive(line, qty, received_on, user, *, note="", close_rest=False):
    """発注の明細に受入を 1 件入れる。金額は masters.services.delivery_amount (部品の単位は受け入れた数 × 明細の単価を発注の取引先の方法で丸める (R03 の形)。
    一式の明細は、明細の受入の合計を数量の手前から数量以上にした回で、その明細に金額の載った受入がまだ無ければ一式の全額、ほかの回は 0 = 作者の決め)。

    close_rest が真なら「残りは受け入れずに完了にする」受入: 同じ atomic の中で受入を作った後に、受入の残 (remaining_qty。RULES.md R25) が
    0 より大きければ mark_completed を呼ぶ (この受入で数量に届いたら何もしない。一式の明細は検査の層の R29 が知らせる (services は断らない))。
    足りないまま完了にした時は、返す受入に saved_message「受入を保存しました。<mark_completed の字>」(と saved_level) を付ける (表に書かない)。
    完了の明細への受入は断らない (取り消した明細と同じ。候補に出さないだけ)。

    断るのは「取消」の発注と 0 以下の数量だけ (「断られた」や「作成」の発注も受け入れる = 現物優先)。
    受入の残より多くても止めない。stock の部品があれば、同じ atomic の中で在庫の入庫も作る (明細に工程のある受入は入れない = stock.services.record_received)。
    明細に指示書の工程 (workorder_step) があり、progress の部品があれば、同じ atomic の中で受け入れた数を良品の数にした実績をその工程に作り、
    受入の progress に結ぶ (RULES.md R32。_record_returned)。この受入の後に明細が終わっている (R25 = 完了の印が真か、受入の合計が数量以上) か、
    close_rest で完了にするときは、その実績に工程の完了の印を付ける。実績は close_rest の mark_completed より先に作るので、実績は 1 件・印は 1 回。
    取り消した明細・取消の指示書の工程・完了の印の付いた工程への受入も断らず、実績も作る (現物優先。受け取った物が戻った事実を残す)。
    実績を作ったときは、返す受入の saved_message を「受入を保存しました。<record_progress の字>」にし、close_rest の字があればその後ろに続ける。
    訳: 戻りを受入と実績で 2 回入れずに、指示書の工程を進めるため。受入のときに、受け入れた数と同じ数で実績を作る (後から人が実績を直すか消すのは止めない)。
    出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M01・コードで確かめた 2 本は、戻りの受入が実績の入力の口を通る)。
    変えてよい条件 = 本人が決め直したとき。
    全部の明細で受入の数が明細の数量以上になれば、発注を「納品された」(STATUS_DELIVERED) にする。
    「相手が受け入れた」の発注に一部の受入が入ると「一部納品」(作者の決め (11-07 00:2x) と作者の決め (11-07 06:38)。
    「下書き」「送った」「相手が保留した」「相手が条件付で受けた」は一部の受入では変えない。後で届く注文回答を当て、
    相手と話す・発注を直す仕事を一覧とタブから消さないため)。
    seizou どうしで送った発注なら、atomic を抜けた後に検収を相手へ送り、送った後の列を持つ受入を返す
    (送れなくても例外にしない。受入は成立する)。
    金額が 0 の受入も、受入日 (received_on) と受け入れた数は残る (取適法 (2026-01-01 施行。旧下請法) の代金の支払期日は物を受け取った日から
    数えるので、受け取った記録は金額に依らず残す。分けて受け取った一式の起算の日は未確認。確かめた資料 = 中小企業共通 EDI インボイス仕様編
    Ⅰ.Ⅲ「インボイス以外の改定」。公正取引委員会の一次資料は未読)。
    訳: 一式の発注を分けて受け入れたとき、全部を受け入れた回で一式の全額を載せ、それまでの回は 0 円にするため (請求書の出荷と同じ決まり)。出所 = 作者の決め (発注の受入も同じ)。
    変えてよい条件 = 作者が一式の金額を載せる時を決め直したとき。前提: 一部でも受け入れた明細の単価の単位は変わらない (発注の変更は一部でも出した明細の単価と単価の単位を直さない = 作者の決め)。この前提が変わるときは、金額の載った受入の見方を印の列に替える。
    訳: 受入は「金額に関わる操作」(core.access.MONEY_ACTIONS) に数えず、金額を見られない人にもさせる。何のため: 受入と出荷は数量を記録する操作で、現場の人が金額の閲覧なしで続けられるようにするため。
    受け入れた数 × 単価が支払の金額になる (決め 11-03 10:2x) が、数の誤りは運用で見つけて直す (本人「出荷、入荷は数量の問題なので問題があるなら運用でカバーという事にしたい」)。出所 = 作者の決め 11-07 21:2x。
    変えてよい条件 = 受入の入力に金額の欄が加わったとき (そのときは金額の閲覧の決めに従って MONEY_ACTIONS に足す)、または運用で数の誤りを見つけられないと分かったとき。
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
        # 一式の明細は、全部を受け入れた回で一式の全額、ほかの回は 0 (作者の決め)。受入は金額を受入の時に残すので、
        # 金額の載った受入があれば全額は載せ終えている。発注の行の錠の中で数えるので、2 人が同時に受け入れても全額は 1 回。
        done = received_qty(current)
        booked = current.price_basis == PRICE_BASIS_LUMP and current.receipts.filter(amount__gt=0).exists()
        completes = not booked and reaches_line_qty(current.qty, done, qty)
        receipt = Receipt.objects.create(
            line=current,
            received_on=received_on,
            qty=qty,
            amount=delivery_amount(locked.partner, qty, current.unit_price, current.price_basis, completes=completes),
            note=note or "",
            received_by=user if getattr(user, "pk", None) is not None else None,
        )
        if apps.is_installed("seizou.stock"):
            from seizou.stock import services as stock_services

            stock_services.record_received(receipt, user)
        recorded = None
        if current.workorder_step_id is not None and apps.is_installed("seizou.progress"):
            # 戻りを指示書の工程の実績にする (RULES.md R32)。close_rest の mark_completed より先に作り、印はこの 1 件にだけ付ける
            # (mark_completed は実績を作らない。先に印があるので、mark_completed の印の無い旨の 1 文も出ない)。
            recorded = _record_returned(receipt, current, locked, user, finishes=close_rest or remaining_qty(current) <= 0)
        closed = None
        if close_rest and remaining_qty(current) > 0:
            # 完了の印を書き、全部の明細が済めば「納品された」にする (mark_completed)。下の状態の見方は、書いた後の状態から始める。
            closed = mark_completed(current, user)
            locked.refresh_from_db(fields=["status"])
        if locked.status != PurchaseOrder.STATUS_DELIVERED:
            # 取り消した明細 (明細状態コード 2) は「納品された」の見方に入れない (受入は断らない = 現物優先。候補に出さないだけ)。
            lines = list(annotate_received(locked.lines.exclude(line_status_code=edi_codes.LINE_DELETED)))
            if lines and all(row.remaining_total <= 0 for row in lines):
                before = history.saved_state(locked)
                locked.status = PurchaseOrder.STATUS_DELIVERED
                locked.save(update_fields=["status"])
                record_history(locked, user, before=before)
            elif locked.status == PurchaseOrder.STATUS_ACCEPTED:
                before = history.saved_state(locked)
                locked.status = PurchaseOrder.STATUS_PARTIAL
                locked.save(update_fields=["status"])
                record_history(locked, user, before=before)

    # 検収を相手へ送る。HTTP は atomic の外 (R17)。connect と purchasing の循環を避けるため関数の中で import する。
    result = receipt
    if _is_sent_order(locked):
        from seizou.connect import services as connect_services

        result = connect_services.send_acceptance(receipt, user)
    if recorded is not None or closed is not None:
        # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出し、saved_level が "warning" なら黄の知らせ)。表に書かない。
        # 実績を作ったときは record_progress の字、足りないまま完了にしたときは mark_completed の字を、この順に続ける。
        result.saved_message = (
            "受入を保存しました。"
            + (recorded.saved_message if recorded is not None else "")
            + (closed.saved_message if closed is not None else "")
        )
    if closed is not None:
        level = getattr(closed, "saved_level", None)
        if level:
            result.saved_level = level
    return result


def _record_returned(receipt, line, purchase_order, user, *, finishes):
    """指示書の工程を指す明細の受入から、その工程の実績を作って受入に結ぶ。receive が受入を作ったのと同じ transaction.atomic の中で呼ぶ。返す物は作った実績。

    値は progress.services.record_progress に渡す: 工程 = 明細の指示書の工程・良品の数 = 受け入れた数・不良の数 0・日時 = 受入日に今の時刻を
    合わせた日時 (aware)・人 = user・備考 = 「協力企業 <取引先の名前> からの受入 (<発注番号>-<行番号>)」・作業した人は空 (社員も名前も渡さない)。
    finishes が真なら工程の完了の印を付ける (receive が、この受入の後に明細が終わっているか、close_rest で完了にするときに真で渡す)。
    実績は在庫を動かさない (工程のある明細の受入は stock.services.record_received が入庫にしない)。
    訳: 戻りを受入と実績で 2 回入れずに、指示書の工程を進めるため。受入のときに、受け入れた数と同じ数で実績を作る (後から人が実績を直すか消すのは止めない)。
    出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M01・コードで確かめた 2 本は、戻りの受入が実績の入力の口を通る)。
    変えてよい条件 = 本人が決め直したとき。
    """
    from seizou.progress import services as progress_services

    now = timezone.localtime().time().replace(microsecond=0)
    recorded_at = timezone.make_aware(datetime.combine(receipt.received_on, now))
    created = progress_services.record_progress(
        line.workorder_step,
        receipt.qty,
        recorded_at,
        user,
        note=f"協力企業 {purchase_order.partner.name} からの受入 ({purchase_order.po_no}-{line.line_no})",
        completes_step=finishes,
    )
    receipt.progress = created
    receipt.save(update_fields=["progress"])
    return created


# --- 完了の印 (人が足りないまま終える明細。RULES.md R25) ---

#: 送った発注を完了にした時の知らせの後ろの字 (完了は相手の seizou へ送らない)
COMPLETED_NOT_SENT_NOTE = (
    "相手の seizou には伝わりません。相手にも止めてほしいときは、完了を外してから、"
    "発注の「変更」で数量を受入済みの数に直して送ってください。"
)


#: 一式の明細を金額の載らないまま完了にした時の知らせの後ろの字 (知らせの設定が「通す」の会社でも、済んだ後に分かるようにする)
LUMP_ZERO_NOTE = "一式の金額は載っていないので、この明細の支払の金額は 0 円です。"

#: 指示書の工程を指す明細を、受入を伴わずに完了にした時の知らせの後ろの字 (工程に完了の印の付いた実績が無いときだけ)
STEP_NOT_COMPLETED_NOTE = (
    "指示書の工程の完了の印は変えていません。工程を終えるときは、指示書の詳細の進捗のタブで実績に完了の印を付けてください。"
)


def completes_without_lump_amount(line, *, adding_qty=None):
    """一式の明細 (単価の単位が式) を、一式の金額が載らないまま完了にするかを返す (読むだけ)。検査の層の R29 が core.checks.tools から呼ぶ。

    真になるのは、明細が一式で、完了の印が偽で、金額の載った受入が無く、次のどちらかのとき。
    人が完了にする (adding_qty が None。mark_completed) なら、受入が 1 件以上あり、受入の合計が数量より少ないとき。受入が 0 件なら払う物が無いので偽。
    受入の入力の「残りは受け入れずに完了にする」(adding_qty = 今回の受入の数。receive の close_rest) なら、今までの受入の合計 + 今回の数が数量より少ないとき
    (今回の受入は一式の全額を載せない回なので、受け入れた物が 1 件以上あるのに金額が載らない)。
    見方は receive と同じ。金額の載った受入があるかは receive の booked (一式の明細の金額が 0 より大きい受入)、今回の回で全額が載るかは
    receive の completes (reaches_line_qty。合計がちょうど数量になる回も全額が載る) の裏。
    取消の発注・取り消した明細 (明細状態コード 2) と、今回の数が 0 以下の受入は偽 (services が先に赤で断るので、黄の知らせの後に赤を出さないため。R24 の取消の請求書と同じ考え)。
    訳: 一式の発注の明細を、一式の金額が載らないまま完了にした間違い (支払の金額が 0 円のまま残る) に気づかせるため。
    出所 = 本人の決め 11-11 12:5x (ポカよけはまず分かるようにし、止めるかは会社が選ぶ)・13:1x (一式の完了の断りを知らせるに変える)。
    変えてよい条件 = 一式の金額を載せる時 (receive の booked と completes) か、完了の印の意味が変わったとき。
    """
    if line.price_basis != PRICE_BASIS_LUMP or line.completed or line.is_cancelled:
        return False
    if line.purchase_order.status == PurchaseOrder.STATUS_CANCELLED:
        return False
    receipts = line.receipts.all()
    if receipts.filter(amount__gt=0).exists():
        return False
    done = received_qty(line)
    if adding_qty is None:
        return receipts.exists() and done < line.qty
    adding = _dec(adding_qty)
    return adding > 0 and done + adding < line.qty


def _all_live_lines_done(purchase_order):
    """取り消していない明細が 1 行以上あり、全部の受入の残 (annotate_received の remaining_total。RULES.md R25) が 0 以下か (読むだけ)。"""
    rows = list(annotate_received(purchase_order.lines.exclude(line_status_code=edi_codes.LINE_DELETED)))
    return bool(rows) and all(row.remaining_total <= 0 for row in rows)


def mark_completed(line, user):
    """発注の明細を、人が完了にする (受入の足りないまま終える)。返す物は取り直した明細 (saved_message 付き)。

    発注の行に錠を掛け (receive と同じ)、印を書き、変更の履歴に残す。取り消していない明細が全部済んだ (残が 0 以下) なら、
    発注を「納品された」にして変更の履歴に残す (同じ transaction.atomic の中)。
    断る (日本語の ValueError): 取消の発注・取り消した明細 (明細状態コード 2)。
    もう完了の明細と、受入の合計が数量以上で終わっている明細は、何も書かずに返す (断らない)。
    一式の明細を金額の載らないまま完了にした時 (completes_without_lump_amount が印を書く前に真) は、知らせの字の後ろに LUMP_ZERO_NOTE を足す
    (知らせの設定が「通す」の会社でも、済んだ後に分かるようにする。saved_level は変えない)。
    seizou どうしで送った発注 (_is_sent_order) では、知らせの字の後ろに COMPLETED_NOT_SENT_NOTE を足し、saved_level を "warning" にする
    (保存は済んだが人の手が要る物。CLAUDE.md の知らせの強さの表の下の注記)。相手へは何も送らない。
    完了で「納品された」になった発注には「変更」が出ない (CHANGEABLE_STATUSES に無い) ので、相手に知らせるときは外してから「変更」で送る。
    受入を伴わない完了 (と clear_completed) は進捗の実績を作らず、工程の完了の印も変えない。指示書の工程を指す明細を完了にしたとき、
    その工程に完了の印の付いた実績が無ければ (progress.services.step_completed が偽)、知らせの字の後ろに STEP_NOT_COMPLETED_NOTE を足す
    (receive の close_rest から呼ばれたときは、receive が先に印の付いた実績を作るので出ない)。
    訳: 戻りが 0 個のまま終えるときに、数の無い実績を作らないため (コードで確かめた 2 本も、0 個で終えるときは工程を動かさない)。
    出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M01)。変えてよい条件 = 本人が決め直したとき。
    訳: 協力企業の戻りが足りないまま終える明細を、数量を受入済みに下げて元の発注の数を消さずに終え、受入の候補と「受入の残」から外すため。
    取消の発注と取り消した明細は受け入れない物なので完了にしない。一式の明細を金額の載らないまま完了にすると支払の金額が 0 円のまま残るので、
    検査の層の R29 で知らせる (止めるか通すかは会社が選ぶ = 本人の決め 11-11 12:5x・13:1x)。全部を受け入れたことにするときは、残りの数で受入を入れて全額を載せる。
    出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M03) と本人の決め 11-11 08:4x・09:3x・12:5x・13:1x。
    変えてよい条件 = 本人が完了の印の意味か、一式の明細を足りないまま終えた時の払い方を決め直したとき。
    """
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=line.purchase_order_id)
        if locked.status == PurchaseOrder.STATUS_CANCELLED:
            raise ValueError("保存できません。取り消した発注の明細は完了にできません。受け入れを続けるときは、新しく発注を入れてください。")
        current = PurchaseOrderLine.objects.get(pk=line.pk)
        if current.is_cancelled:
            raise ValueError("保存できません。取り消した明細は完了にできません。取り消した明細は受入の残に数えないので、完了は要りません。")
        label = f"{locked.po_no}-{current.line_no}"
        if received_qty(current) >= current.qty:
            # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
            current.saved_message = f"{label} は数量まで受け入れ済みで、終わっています。"
            return current
        # 一式の金額の載らないまま完了にするか (印を書いた後は偽になるので、書く前に見る。検査の層の R29 と同じ見方)
        lump_zero = completes_without_lump_amount(current)
        if not current.completed:
            before = history.saved_state(current)
            current.completed = True
            current.save(update_fields=["completed"])
            record_history(current, user, before=before)
            if locked.status != PurchaseOrder.STATUS_DELIVERED and _all_live_lines_done(locked):
                before = history.saved_state(locked)
                locked.status = PurchaseOrder.STATUS_DELIVERED
                locked.save(update_fields=["status"])
                record_history(locked, user, before=before)
        message = (
            f"{label} を完了にしました。残り {_qty_text(unfilled_qty(current))} は受け入れずに終えます。"
            "また受け入れるときは、明細の ⋮ から完了を外してください。"
        )
        if _is_sent_order(locked):
            message += COMPLETED_NOT_SENT_NOTE
            current.saved_level = "warning"
        if lump_zero:
            message += LUMP_ZERO_NOTE
        if _step_left_open(current):
            message += STEP_NOT_COMPLETED_NOTE
        current.saved_message = message
        return current


def _step_left_open(line):
    """明細が指示書の工程を指し、その工程に完了の印の付いた実績が無いか (progress.services.step_completed が偽。読むだけ)。progress の部品が無ければ偽。"""
    if line.workorder_step_id is None or not apps.is_installed("seizou.progress"):
        return False
    from seizou.progress import services as progress_services

    return not progress_services.step_completed(line.workorder_step_id)


def _reopened_status(purchase_order):
    """完了を外して受入の残が戻った「納品された」の発注の、決め直した状態 (読むだけ)。

    受入が 1 つでもあり、第 1 版の注文回答のタイプが受諾なら「一部納品」。ほかは注文回答のタイプから決める
    (受諾 → 相手が受け入れた・条件付 → 相手が条件付で受けた・保留 → 相手が保留した・辞退 → 断られた・空 → 送った注文があれば送った、無ければ下書き)。
    訳: 完了を外した発注を、受入の数で状態を変える今の決まり (receive の docstring = 「相手が受け入れた」の発注に一部の受入が入ると「一部納品」、
    ほかの状態は一部の受入では変えない) と同じ見方に戻し、後で届く注文回答と受入を今までどおり当てられるようにするため。
    出所 = 本人の決め 11-11 08:4x・09:3x を受けた作者の決め (開発の記録 docs/nights/20261111/N400.md)。
    変えてよい条件 = 発注の状態の決め方 (receive・mark_accepted) が変わったとき。
    """
    reply = purchase_order.reply_type
    if reply == edi_codes.REPLY_ACCEPTED and purchase_order.receipts.exists():
        return PurchaseOrder.STATUS_PARTIAL
    by_reply = {
        edi_codes.REPLY_ACCEPTED: PurchaseOrder.STATUS_ACCEPTED,
        edi_codes.REPLY_CONDITIONAL: PurchaseOrder.STATUS_CONDITIONAL,
        edi_codes.REPLY_PENDING: PurchaseOrder.STATUS_ON_HOLD,
        edi_codes.REPLY_REJECTED: PurchaseOrder.STATUS_REJECTED,
    }
    if reply in by_reply:
        return by_reply[reply]
    return PurchaseOrder.STATUS_SENT if _is_sent_order(purchase_order) else PurchaseOrder.STATUS_DRAFT


def clear_completed(line, user):
    """発注の明細の完了を外す。返す物は取り直した明細 (saved_message 付き)。

    発注の行に錠を掛け (receive と同じ)、印を外し、変更の履歴に残す。外した後、発注が「納品された」で、取り消していない明細のどれかに
    受入の残があれば、状態を _reopened_status で決め直して変更の履歴に残す (同じ transaction.atomic の中)。
    取消の発注の明細は日本語の ValueError。完了でない明細は何も書かずに返す (断らない)。
    外した後の受入の残 (remaining_qty) が 0 より大きければ、知らせの字に「残り n をまた受け入れられます。」を足す。相手へは何も送らない。
    指示書の工程を指す明細でも、進捗の実績は作らず、工程の完了の印も変えない (mark_completed と同じ)。
    訳: 誤って完了にした明細や、協力企業がやはり残りを納めると言った明細を、また受入の候補と「受入の残」に戻すため。
    取消の発注は受け入れない物なので外さない。
    出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M03) と本人の決め 11-11 08:4x・09:3x。
    変えてよい条件 = 本人が完了の印の意味を決め直したとき。
    """
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=line.purchase_order_id)
        if locked.status == PurchaseOrder.STATUS_CANCELLED:
            raise ValueError("保存できません。取り消した発注の明細の完了は外せません。受け入れを続けるときは、新しく発注を入れてください。")
        current = PurchaseOrderLine.objects.get(pk=line.pk)
        if current.completed:
            before = history.saved_state(current)
            current.completed = False
            current.save(update_fields=["completed"])
            record_history(current, user, before=before)
            if locked.status == PurchaseOrder.STATUS_DELIVERED and not _all_live_lines_done(locked):
                before = history.saved_state(locked)
                locked.status = _reopened_status(locked)
                locked.save(update_fields=["status"])
                record_history(locked, user, before=before)
        message = f"{locked.po_no}-{current.line_no} の完了を外しました。"
        rest = remaining_qty(current)
        if rest > 0:
            message += f"残り {_qty_text(rest)} をまた受け入れられます。"
        # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
        current.saved_message = message
        return current


# --- 協力企業に出した指示書の工程 (見比べのまとめの M01。渡した日・出ている数・まだ渡していない数) ---


def set_handed_on(line, handed_on, user):
    """指示書の工程を指す発注の明細の渡した日を直す。handed_on が None なら空に戻す (まだ渡していない扱い)。返す物は取り直した明細 (saved_message 付き)。

    発注の行に錠を掛け (mark_completed と同じ)、欄を書き、変更の履歴に残す (同じ日なら履歴は増えない)。相手の seizou へは送らない。
    断る (日本語の ValueError。頭は「保存できません。」): 取消の発注・取り消した明細 (明細状態コード 2)・指示書の工程の無い明細。
    完了の明細と受入のある明細は断らない。
    返す明細の saved_message は「<発注番号>-<行番号> の渡した日を <日付> にしました。」、空に戻したときは
    「<発注番号>-<行番号> の渡した日を空にしました (まだ渡していない扱いです)。」(表に書かない)。
    訳: 現物を協力企業に渡した日を残し、出ている数とまだ渡していない数を分けて見るため。取消の発注と取り消した明細は渡す物ではなく、
    指示書の工程の無い明細は渡した記録を持たない (表の作りの上の断り)。
    出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M01。渡した記録は 3 本・コードで確かめた 1 本は渡したときに日を書く)。
    変えてよい条件 = 分けて渡す記録が要ると本人が決めたとき。
    """
    with transaction.atomic():
        locked = PurchaseOrder.objects.select_for_update().get(pk=line.purchase_order_id)
        if locked.status == PurchaseOrder.STATUS_CANCELLED:
            raise ValueError("保存できません。取り消した発注の明細の渡した日は直せません。渡すときは、新しく発注を入れてください。")
        current = PurchaseOrderLine.objects.get(pk=line.pk)
        if current.is_cancelled:
            raise ValueError("保存できません。取り消した明細の渡した日は直せません。取り消した明細は協力企業に渡す物ではありません。")
        if current.workorder_step_id is None:
            raise ValueError(_HANDED_WITHOUT_STEP_MESSAGE + "この明細は指示書の工程に結んでいないので、渡した日は要りません。")
        before = history.saved_state(current)
        current.handed_on = handed_on
        current.save(update_fields=["handed_on"])
        record_history(current, user, before=before)
        label = f"{locked.po_no}-{current.line_no}"
        if handed_on is None:
            current.saved_message = f"{label} の渡した日を空にしました (まだ渡していない扱いです)。"
        else:
            current.saved_message = f"{label} の渡した日を {handed_on:%Y-%m-%d} にしました。"
        return current


def _is_live_subcontract(line):
    """指示書の工程を指す、取り消していない明細か (取消でない発注の、明細状態コードが 2 でない明細)。読むだけ。"""
    return (
        line.workorder_step_id is not None
        and not line.is_cancelled
        and line.purchase_order.status != PurchaseOrder.STATUS_CANCELLED
    )


def out_qty(line):
    """出ている数 (協力企業に渡して、まだ戻っていない数。読むだけ)。

    指示書の工程を指し、渡した日のある、取り消していない明細 (取消でない発注の、明細状態コードが 2 でない明細) の受入の残
    (remaining_qty。RULES.md R25) のうち 0 より大きい分。ほかは 0 (指示書の工程の無い明細・取り消した明細・取消の発注の明細・
    まだ渡していない明細・完了の明細・多く戻った明細)。
    """
    if not _is_live_subcontract(line) or line.handed_on is None:
        return _ZERO
    return max(remaining_qty(line), _ZERO)


def waiting_qty(line):
    """まだ渡していない数 (協力企業に出す明細のうち、まだ現物を渡していない数。読むだけ)。

    指示書の工程を指し、渡した日の無い、取り消していない明細の受入の残 (remaining_qty。RULES.md R25) のうち 0 より大きい分。ほかは 0
    (out_qty と同じ。渡した日のある明細は 0)。
    """
    if not _is_live_subcontract(line) or line.handed_on is not None:
        return _ZERO
    return max(remaining_qty(line), _ZERO)


def annotate_subcontract(steps):
    """指示書の工程の queryset に、その工程を指す明細の出ている数の合計 (subcontract_out_total)・まだ渡していない数の合計
    (subcontract_waiting_total)・取り消していない明細の数 (subcontract_line_count) を足して返す。読むだけ。

    値は out_qty・waiting_qty の明細ごとの和と同じ (取り消していない明細の受入の残のうち 0 より大きい分を、渡した日の有る無しで分ける)。
    3 つとも別の Subquery で足し、工程の queryset の join を増やさない (annotate_order_received と同じ考え。明細ごとの受入の数は、その中の
    Subquery で数える)。生産管理ボードと指示書の詳細と今日やることが使う。
    """
    line_received = (
        Receipt.objects.filter(line=OuterRef("pk")).order_by().values("line").annotate(total=Sum("qty")).values("total")[:1]
    )
    lines = (
        PurchaseOrderLine.objects.filter(workorder_step=OuterRef("pk"))
        .exclude(line_status_code=edi_codes.LINE_DELETED)
        .exclude(purchase_order__status=PurchaseOrder.STATUS_CANCELLED)
        .annotate(
            line_received=Coalesce(Subquery(line_received, output_field=_QTY_FIELD), Value(_ZERO), output_field=_QTY_FIELD)
        )
        .annotate(
            # 受入の残 (R25) のうち 0 より大きい分: 完了の明細は 0、ほかは max(数量 − 受入の数, 0)
            open_qty=Case(
                When(completed=True, then=Value(_ZERO)),
                When(qty__gt=F("line_received"), then=ExpressionWrapper(F("qty") - F("line_received"), output_field=_QTY_FIELD)),
                default=Value(_ZERO),
                output_field=_QTY_FIELD,
            )
        )
        .order_by()
    )

    def _open_total(handed):
        rows = lines.filter(handed_on__isnull=not handed).values("workorder_step").annotate(total=Sum("open_qty")).values("total")[:1]
        return Coalesce(Subquery(rows, output_field=_QTY_FIELD), Value(_ZERO), output_field=_QTY_FIELD)

    count = lines.values("workorder_step").annotate(n=Count("pk")).values("n")[:1]
    return steps.annotate(
        subcontract_out_total=_open_total(True),
        subcontract_waiting_total=_open_total(False),
        subcontract_line_count=Coalesce(Subquery(count, output_field=IntegerField()), Value(0), output_field=IntegerField()),
    )


def step_lines(step):
    """指示書の工程を指す、取り消していない明細 (取消でない発注の、明細状態コードが 2 でない明細) の list。読むだけ。

    受入の数 (received_total) と受入の残 (remaining_total) を足し (annotate_received)、発注番号 → 行番号の順に並べる。
    発注と取引先と部品と工程は select_related で一緒に引く。
    """
    rows = (
        PurchaseOrderLine.objects.filter(workorder_step=step)
        .exclude(line_status_code=edi_codes.LINE_DELETED)
        .exclude(purchase_order__status=PurchaseOrder.STATUS_CANCELLED)
        .select_related("purchase_order__partner", "item", "process")
    )
    return list(annotate_received(rows).order_by("purchase_order__po_no", "line_no", "pk"))


def unreturned_lines(step):
    """指示書の工程を指す明細のうち、出ている数かまだ渡していない数が 0 より大きい明細の list (step_lines の並び。読むだけ)。

    取り消していない明細の受入の残 (remaining_total) が 0 より大きい明細と同じ (渡した日があれば出ている数、無ければまだ渡していない数)。
    検査の層の R33 (実績の入力) が core.checks.tools から呼ぶ。
    """
    return [line for line in step_lines(step) if line.remaining_total > 0]


def steps_to_subcontract():
    """これから協力企業に出す指示書の工程の queryset (読むだけ。指示書の順 → 工程の順)。

    工程の「協力企業に出すか」が真・指示書が発行 (取消でない)・受注が取消でない・その工程を指す取り消していない明細が無い・
    工程の完了の印の付いた実績が無い (WorkOrderStep.total_expressions() の completed_mark) 指示書の工程。
    workorders の model は import せず、外部キーの先として引く。発注を取り消すと、その工程はまた出る。
    """
    step_model = PurchaseOrderLine._meta.get_field("workorder_step").related_model
    workorder_model = step_model._meta.get_field("workorder").related_model
    order_model = workorder_model._meta.get_field("order_line").related_model._meta.get_field("order").related_model
    live_lines = (
        PurchaseOrderLine.objects.filter(workorder_step=OuterRef("pk"))
        .exclude(line_status_code=edi_codes.LINE_DELETED)
        .exclude(purchase_order__status=PurchaseOrder.STATUS_CANCELLED)
    )
    return (
        step_model.objects.filter(process__is_outsourced=True, workorder__status=workorder_model.STATUS_ISSUED)
        .exclude(workorder__order_line__order__status=order_model.STATUS_CANCELLED)
        .annotate(completed_mark=step_model.total_expressions()["completed_mark"], has_live_lines=Exists(live_lines))
        .filter(completed_mark=False, has_live_lines=False)
        .select_related("workorder", "process")
        .order_by("workorder_id", "seq", "pk")
    )


def lines_not_handed():
    """協力企業に渡していない明細の queryset (読むだけ)。

    指示書の工程を指し、渡した日の無い、取り消していない明細 (取消でない発注の、明細状態コードが 2 でない明細) のうち、まだ渡していない数
    (waiting_qty = 受入の残 (RULES.md R25) のうち 0 より大きい分) が 0 より大きい物。完了の明細は受入の残が 0 以下なので入らない。
    今日やることの「協力企業に渡していない明細」の数 (core.dashboard) と、発注の一覧の絞り「渡していない明細があるだけ」
    (orders_with_lines_not_handed) が、この 1 つの関数を使う (数と一覧が食い違わないため。見比べのまとめの M01 の 3 段目)。
    """
    rows = (
        PurchaseOrderLine.objects.filter(workorder_step__isnull=False, handed_on__isnull=True)
        .exclude(line_status_code=edi_codes.LINE_DELETED)
        .exclude(purchase_order__status=PurchaseOrder.STATUS_CANCELLED)
    )
    return annotate_received(rows).filter(remaining_total__gt=0)


def orders_with_lines_not_handed(queryset):
    """発注の queryset を、協力企業に渡していない明細 (lines_not_handed) を持つ発注に狭める (読むだけ)。

    発注の一覧の絞り「渡していない明細があるだけ」の when。明細の join を足さない副問い合わせなので、同じ発注は重ならない。
    """
    return queryset.filter(pk__in=lines_not_handed().values("purchase_order_id"))


def waiting_reply():
    """相手の答え (注文回答) を待つ発注の queryset (読むだけ): 状態が「送った」で、発注書を seizou の外で送っていない (sent_outside_at が空) 物。

    今日やることの「返事を待つ発注」の数 (core.dashboard) と、発注の一覧の絞り「返事を待っている物だけ」(orders_waiting_reply) が、
    この 1 つの関数を使う (数と一覧が食い違わないため)。送った記録の行が無い「送った」の発注 (作り物の種) も、今までどおり数える。
    訳: 「発注書を送った」で「送った」にした発注は、seizou では答えが届かないので、返事を待つ発注に数えないため。
    出所 = 本人の決め 11-12 14:3x と本体の決め 11-12 (依頼書 N439)。変えてよい条件 = 本人が、返事を待つ発注の数え方を決め直したとき。
    """
    return PurchaseOrder.objects.filter(status=PurchaseOrder.STATUS_SENT, sent_outside_at__isnull=True)


def orders_waiting_reply(queryset):
    """発注の queryset を、相手の答えを待つ発注 (waiting_reply) に狭める (読むだけ)。発注の一覧の絞り「返事を待っている物だけ」の when。

    副問い合わせで絞るので、同じ発注は重ならない (orders_with_lines_not_handed と同じ形)。
    """
    return queryset.filter(pk__in=waiting_reply().values("pk"))


def default_subcontractor(item, process, *, on_date=None):
    """部品と工程の、協力企業への出し先の既定の取引先 (読むだけ)。ちょうど 1 社ならその取引先、ほかは None。

    masters.services.item_prices の行のうち、売りか発注かが「協力企業への発注」・工程がその工程・取引先の行 (共通でない)・
    適用開始日が空か on_date (既定は今日) 以前の行を取り、その取引先が使用中で、種類が協力企業か両方の物を数える。
    訳: 同じ部品と工程を出す先が 1 社に決まっているときに、発注のたびに出し先を選ぶ手間を減らすため。2 社以上ならどこへ出すか分からないので入れない。
    出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M01。出し先の既定を部品の工程に持つ本は 4 本。seizou は部品・工程・取引先を持つ
    単価表の発注の行から決める)。
    変えてよい条件 = 部品の工程の行に出し先の欄を持つと本人が決めたとき。
    """
    if on_date is None:
        on_date = timezone.localdate()
    process_pk = getattr(process, "pk", process)
    partners = {}
    for price in item_prices(item):
        if price.kind != type(price).KIND_PURCHASE or price.process_id != process_pk or price.partner_item_id is None:
            continue
        if price.start_date is not None and price.start_date > on_date:
            continue
        partner = price.partner_item.partner
        if partner.active and partner.is_subcontractor:
            partners[partner.pk] = partner
    return next(iter(partners.values())) if len(partners) == 1 else None


class ReturnGap(NamedTuple):
    """指示書の工程を指す明細の、1 回の受入の戻りの数の食い違い (R33 が読む。読むだけ)。

    label = 「<発注番号>-<行番号>」・qty = 明細の数量・total = 今までの受入の合計 + 今回の数・not_handed = 渡した日が空・
    over = 数量を超える分 (超えなければ 0)・short = 「残りは受け入れずに完了にする」で戻らないまま完了にする分 (当たらなければ 0)。
    """

    label: str
    qty: Decimal
    total: Decimal
    not_handed: bool
    over: Decimal
    short: Decimal


def receipt_return_gap(line, qty, *, close_rest=False):
    """受入 1 回の戻りの数の食い違い (ReturnGap)。食い違いが無ければ None (読むだけ。検査の層の R33 が core.checks.tools から呼ぶ)。

    見るのは指示書の工程を指す明細だけ。取消の発注・今回の数が 0 以下・取り消した明細の「残りは受け入れずに完了にする」は None
    (services が先に赤で断るので、黄の知らせの後に赤を出さないため。R29 の completes_without_lump_amount と同じ考え)。
    not_handed = 渡した日が空。over = 今までの受入の合計 + 今回の数が数量を超える分。
    short = close_rest が真で、渡した日があり、完了の印が偽で、今までの受入の合計 + 今回の数が数量より少ない分
    (もう完了の明細は receive が完了の印を書かないので数えない)。
    訳: 協力企業に出した工程の戻りの数の食い違い (渡す前の戻り・多い戻り・目減りで完了) に気づかせるため (止めるか知らせるかは会社が選ぶ)。
    出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M01。目減りと多い戻りを知らせる本は 2 本、多い戻りを止める本は 2 本)・
    本人の決め 11-11 12:5x (ポカよけはまず分かるようにし、止めるかは会社が選ぶ)。
    変えてよい条件 = 会社の設定 subcontract_return で選ぶ。数え方を変える提案は Issue「決まりの提案」で。
    """
    if line.workorder_step_id is None or line.purchase_order.status == PurchaseOrder.STATUS_CANCELLED:
        return None
    adding = _dec(qty)
    if not adding > 0 or (close_rest and line.is_cancelled):
        return None
    total = received_qty(line) + adding
    handed = line.handed_on is not None
    over = total - line.qty if total > line.qty else _ZERO
    short = line.qty - total if (close_rest and handed and not line.completed and total < line.qty) else _ZERO
    if handed and over == 0 and short == 0:
        return None
    return ReturnGap(f"{line.purchase_order.po_no}-{line.line_no}", line.qty, total, not handed, over, short)


def short_return_qty(line):
    """発注の明細を受入を伴わずに完了にしたとき、渡した数のうち戻らないまま終える数 (読むだけ。検査の層の R33 が core.checks.tools から呼ぶ)。

    指示書の工程を指し、渡した日があり、完了の印が偽で、受入の合計が数量より少ない明細の 数量 − 受入の合計。ほかは 0
    (取消の発注と取り消した明細は mark_completed が先に赤で断るので 0。黄の知らせの後に赤を出さないため)。
    訳: 協力企業に渡した数より少ない戻りで明細を終える (目減りで完了) 間違いに気づかせるため (止めるか知らせるかは会社が選ぶ)。
    出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M01。目減りを知らせる本は 2 本)・本人の決め 11-11 12:5x。
    変えてよい条件 = 会社の設定 subcontract_return で選ぶ。数え方を変える提案は Issue「決まりの提案」で。
    """
    if line.workorder_step_id is None or line.handed_on is None or line.completed or line.is_cancelled:
        return _ZERO
    if line.purchase_order.status == PurchaseOrder.STATUS_CANCELLED:
        return _ZERO
    return max(line.qty - received_qty(line), _ZERO)


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


def acceptances_undelivered():
    """相手に届いていない検収の受入の queryset (読むだけ)。

    受入のうち、送った日時が空で、返事の欄が空でなく、送る物でない字 (connect.services の MESSAGE_ACCEPTANCE_NOT_SENT_ORDER) でない物。
    今日やることの「相手に届いていない検収」の数 (core.dashboard) と、受入の一覧の絞り「検収が届いていない物だけ」(purchasing.views) が、
    この 1 つの関数を使う (数と一覧が食い違わないため。本体の決め 11-12 13:4x)。字の定数は connect.services から関数の中で読むので、
    connect の部品が無い置き場では呼ばない (呼ぶ側の core.dashboard と一覧の絞りが確かめる)。

    訳: 相手に届いていない注文回答・出荷の知らせ・検収を、詳細を開かなくても今日やることで気づけるようにするため (届かなかった物は自動では送り直さず、人が「送り直す」を押す)。作り物の種の読み込みと、相手が seizou でない物 (送る物でない) は数えない。相手に断られた物 (「断られた: …」) も、届いていない物として数える。出所 = 本体の決め 11-11 14:2x (精査 Q3 の T8・M4)。変えてよい条件 = 届いていない物の数え方を本人が決め直したとき。
    """
    # connect と purchasing の循環を避けるため関数の中で import する。
    from seizou.connect.services import MESSAGE_ACCEPTANCE_NOT_SENT_ORDER

    return (
        Receipt.objects.filter(acceptance_sent_at__isnull=True)
        .exclude(acceptance_response="")
        .exclude(acceptance_response=MESSAGE_ACCEPTANCE_NOT_SENT_ORDER)
    )


def received_qty(line):
    """発注の明細の受入の数 (受入から集計。列に持たない。orders.services.shipped_qty と同じ形)。"""
    return line.receipts.aggregate(total=Coalesce(Sum("qty"), Value(_ZERO), output_field=_QTY_FIELD))["total"]


def remaining_qty(line):
    """発注の明細の受入の残 (RULES.md R25)。完了の明細は min(数量 − 受入の数, 0)、ほかは 数量 − 受入の数。マイナスも返す (現物優先)。

    完了の明細は足りない分を残に数えず、多く受けた分はマイナスのまま見せる。明細が終わっている = この残が 0 以下
    (完了の明細か、受入の合計が数量以上)。完了の印は渡された明細の値を読む (印を書いた後は取り直した明細を渡す)。
    訳: 人が完了にした明細の足りない分を、受入の候補・「受入の残」・出荷の知らせのある明細の数に数えないため。
    完了の印と受入の数は結ばず、どちらかを満たせば終わっている。受入の数と金額は変えない。
    出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M03) と本人の決め 11-11 08:4x・09:3x。
    変えてよい条件 = 本人が完了の印の意味を決め直したとき。
    """
    rest = line.qty - received_qty(line)
    if line.completed:
        return min(rest, _ZERO)
    return rest


def unfilled_qty(line):
    """完了の明細の受け入れずに終えた数 = max(数量 − 受入の数, 0)。完了でなければ 0 (読むだけ)。

    完了にした時の知らせの字と、明細の完了の印の画面が読む。
    訳: 人が完了にした明細の足りない分を、受入の候補・「受入の残」・出荷の知らせのある明細の数に数えないため。
    完了の印と受入の数は結ばず、どちらかを満たせば終わっている。受入の数と金額は変えない。
    出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M03) と本人の決め 11-11 08:4x・09:3x。
    変えてよい条件 = 本人が完了の印の意味を決め直したとき。
    """
    if not line.completed:
        return _ZERO
    return max(line.qty - received_qty(line), _ZERO)


def annotate_received(queryset):
    """発注の明細の queryset に、受入の数 (received_total) と受入の残 (remaining_total) を足して返す。

    received_qty・remaining_qty と同じ式 (受入の数の合計、受入が無ければ 0。残は RULES.md R25 = 完了の明細は
    min(数量 − 受入の数, 0)、ほかは 数量 − 受入の数)。join は受入の 1 つだけ。候補の口と詳細が使う。
    訳: 人が完了にした明細の足りない分を、受入の候補・「受入の残」・出荷の知らせのある明細の数に数えないため。
    完了の印と受入の数は結ばず、どちらかを満たせば終わっている。受入の数と金額は変えない。
    出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M03) と本人の決め 11-11 08:4x・09:3x。
    変えてよい条件 = 本人が完了の印の意味を決め直したとき。
    """
    received = Coalesce(Sum("receipts__qty"), Value(_ZERO), output_field=_QTY_FIELD)
    rest = ExpressionWrapper(F("qty") - F("received_total"), output_field=_QTY_FIELD)
    return queryset.annotate(received_total=received).annotate(
        remaining_total=Case(
            # 完了の明細: 多く受けた分はマイナスのまま、足りない分は 0
            When(completed=True, received_total__gt=F("qty"), then=rest),
            When(completed=True, then=Value(_ZERO)),
            default=rest,
            output_field=_QTY_FIELD,
        )
    )


def annotate_order_received(queryset):
    """発注の queryset に、明細の数量の合計 (ordered_total)・受入の数の合計 (received_total)・差 (receive_remaining) を足す。

    明細と受入の合計は別の Subquery で足す (lines と receipts の 2 つの join を 1 つの問い合わせに置くと、行が重なって数えられる)。
    取り消した発注の差は 0 (受け入れない発注に残を出さない。作者の決め)。
    差からは、取り消していない完了の明細の受け入れずに終えた数 (unfilled_qty と同じ式) の合計も引く (RULES.md R25)。これも別の Subquery
    (明細ごとの受入の数は、その中の Subquery で数える。join を重ねない)。ordered_total と received_total の 2 列は変えない。
    発注の一覧が使う。読むだけ。
    訳: 人が完了にした明細の足りない分を、発注の一覧の「受入の残」に数えないため。取り消した明細の受入の数え方は変えない。
    出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M03) と本人の決め 11-11 08:4x・09:3x。
    変えてよい条件 = 本人が完了の印の意味を決め直したとき。
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
    # 取り消していない完了の明細ごとの max(数量 − その明細の受入の数, 0) の合計 (unfilled_qty と同じ式)。
    line_received = (
        Receipt.objects.filter(line=OuterRef("pk")).order_by().values("line").annotate(total=Sum("qty")).values("total")[:1]
    )
    unfilled = (
        PurchaseOrderLine.objects.filter(purchase_order=OuterRef("pk"), completed=True)
        .exclude(line_status_code=edi_codes.LINE_DELETED)
        .annotate(
            line_received=Coalesce(Subquery(line_received, output_field=_QTY_FIELD), Value(_ZERO), output_field=_QTY_FIELD)
        )
        .annotate(
            short=Case(
                When(qty__gt=F("line_received"), then=ExpressionWrapper(F("qty") - F("line_received"), output_field=_QTY_FIELD)),
                default=Value(_ZERO),
                output_field=_QTY_FIELD,
            )
        )
        .order_by()
        .values("purchase_order")
        .annotate(total=Sum("short"))
        .values("total")[:1]
    )
    unfilled_total = Coalesce(Subquery(unfilled, output_field=_QTY_FIELD), Value(_ZERO), output_field=_QTY_FIELD)
    return queryset.annotate(
        ordered_total=Coalesce(Subquery(ordered, output_field=_QTY_FIELD), Value(_ZERO), output_field=_QTY_FIELD),
        received_total=Coalesce(Subquery(received, output_field=_QTY_FIELD), Value(_ZERO), output_field=_QTY_FIELD),
    ).annotate(
        receive_remaining=Case(
            When(status=PurchaseOrder.STATUS_CANCELLED, then=Value(_ZERO)),
            default=ExpressionWrapper(F("ordered_total") - F("received_total") - unfilled_total, output_field=_QTY_FIELD),
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
    rest の合計は 知らせの合計 − 受入の合計 (受入の方が多ければ 0)。読むだけ。発注の詳細の「受入を記録する」の数量に使う (作者の決め)。
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


#: 支払の見込みの金額の型 (受入と仕入明細の明細の金額と同じ桁)
_MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)


def annotate_payable(receipts):
    """受入の queryset に、支払の見込みで数える金額の列 2 つを足して返す (読むだけ)。

    payable_amount = 相手が仕入明細で承諾した明細 (承諾と、直した内容の承諾) は仕入明細の明細の金額、まだの明細と仕入明細に入っていない受入は
    受入の金額 (作者の決め)。取り消した仕入明細の明細は見ない。checking_amount = 取消でない仕入明細に入っていて、まだ承諾されていない明細の
    受入の金額 (確かめ中の金額)。ほかは 0。保存した金額を選ぶだけで、DB で掛け算と丸めをしない (R03)。
    中小企業共通 EDI の請求レス方式は、売り手が確認した仕入明細の金額で支払う (インボイス仕様編 6.1.1)。
    """
    accepted = (edi_codes.SI_REPLY_ACCEPTED, edi_codes.SI_REPLY_ACKNOWLEDGED)
    live = SelfInvoiceLine.objects.filter(receipt=OuterRef("pk")).exclude(self_invoice__status=SelfInvoice.STATUS_CANCELLED)
    confirmed = live.filter(reply_type__in=accepted).values("amount")[:1]
    return receipts.annotate(
        payable_amount=Coalesce(Subquery(confirmed, output_field=_MONEY_FIELD), F("amount"), output_field=_MONEY_FIELD),
        checking_amount=Case(
            When(Exists(live.exclude(reply_type__in=accepted)), then=F("amount")),
            default=Value(_ZERO),
            output_field=_MONEY_FIELD,
        ),
    )


def payable_by_partner(today=None):
    """支払の見込み (先月と今月)。支払の見込みの金額 (annotate_payable) を取引先ごと・月ごと (受入日の月) に足す。読むだけ。

    保存した金額の合計 (Sum) で、DB で掛け算をしない。today の既定は今日。行は取引先コードの順で、
    2 か月とも 0 の取引先は入れない。返す物 = {"months": [{"key", "label"} × 2], "rows": [{"partner",
    "last_month", "this_month"}, …], "totals": {"last_month", "this_month"}, "checking": {"last_month", "this_month"}}。
    checking は仕入明細で確かめ中の金額 (annotate_payable の checking_amount) の月ごとの合計 (取引先ごとには分けない)。
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
    checking = {"last_month": _ZERO, "this_month": _ZERO}
    for slot, start, end in (("last_month", last_start, this_start), ("this_month", this_start, next_start)):
        rows = (
            annotate_payable(Receipt.objects.filter(received_on__gte=start, received_on__lt=end))
            .order_by()
            .values("line__purchase_order__partner")
            .annotate(total=Sum("payable_amount"), checking=Sum("checking_amount"))
        )
        for row in rows:
            slots = sums.setdefault(row["line__purchase_order__partner"], {"last_month": _ZERO, "this_month": _ZERO})
            slots[slot] = _dec(row["total"] or _ZERO)
            checking[slot] += _dec(row["checking"] or _ZERO)
    shown = [pk for pk, slots in sums.items() if slots["last_month"] or slots["this_month"]]
    partner_model = PurchaseOrder._meta.get_field("partner").related_model
    partners = partner_model._default_manager.filter(pk__in=shown).order_by("code", "pk") if shown else []
    result_rows = [{"partner": partner, **sums[partner.pk]} for partner in partners]
    totals = {
        "last_month": sum((row["last_month"] for row in result_rows), _ZERO),
        "this_month": sum((row["this_month"] for row in result_rows), _ZERO),
    }
    return {"months": months, "rows": result_rows, "totals": totals, "checking": checking}


# --- 仕入明細 (受入をまとめて相手へ送る書類。中小企業共通 EDI の仕入明細の項目を元にした。作者の決め) ---


def _sent_order_exists():
    """受入の発注に送った注文 (connect の Outbox) の行があるかの式 (Exists)。connect の無い会社では None。"""
    if not apps.is_installed("seizou.connect"):
        return None
    outbox = apps.get_model("connect", "Outbox")
    return Exists(outbox.objects.filter(purchase_order=OuterRef("line__purchase_order")))


def _in_live_self_invoice():
    """受入が取消でない仕入明細に入っているかの式 (Exists)。取り消した仕入明細だけに入っていた受入は偽。"""
    return Exists(
        SelfInvoiceLine.objects.filter(receipt=OuterRef("pk")).exclude(self_invoice__status=SelfInvoice.STATUS_CANCELLED)
    )


def self_invoice_receipts(partner=None):
    """仕入明細に入れられる受入 (読むだけ)。取消でない仕入明細に入っていない受入のうち、seizou どうしで送った発注 (送った注文の行がある) の物。

    取り消した仕入明細だけに入っていた受入は並ぶ (取り消した後に直して送り直すため = 作者の決め)。partner を渡せばその取引先の発注の受入だけ。
    並びは受入日・発注番号・行番号。connect の無い会社では空 (相手へ送れないため)。
    """
    sent = _sent_order_exists()
    if sent is None:
        return Receipt.objects.none()
    rows = Receipt.objects.filter(sent).exclude(_in_live_self_invoice())
    if partner is not None:
        rows = rows.filter(line__purchase_order__partner=partner)
    return rows.order_by("received_on", "line__purchase_order__po_no", "line__line_no", "pk")


def _self_invoice_refusal(receipt, partner):
    """受入を仕入明細に入れられない訳の文 (「発行できません。」から)。入れられれば空の字。create_self_invoice が行をロックした後に呼ぶ。"""
    purchase_order = receipt.line.purchase_order
    if purchase_order.partner_id != partner.pk:
        return f"発行できません。受入 {receipt} は、取引先 {partner.name} の発注の受入ではありません。外してください。"
    live = (
        SelfInvoiceLine.objects.filter(receipt=receipt)
        .exclude(self_invoice__status=SelfInvoice.STATUS_CANCELLED)
        .select_related("self_invoice")
        .first()
    )
    if live is not None:
        return f"発行できません。受入 {receipt} は、既に仕入明細 {live.self_invoice.self_invoice_no} に入っています。外してください。"
    if not _is_sent_order(purchase_order):
        return (
            f"発行できません。受入 {receipt} は、seizou どうしで送った発注の受入ではないので、仕入明細を相手へ送れません。外してください。"
        )
    return ""


def create_self_invoice(partner, receipts, user, *, issued_on):
    """取引先の受入をまとめて仕入明細を作る (まだ送らない)。仕入明細番号は core の連番 (SI) が付ける。請求書の create_invoice と同じ形。

    明細は受入 1 件に 1 行 (並びは受入日・発注番号・行番号。1 から番号を付ける)。受領日・数量・単位・数量単位コード・単価の単位・単価 (発注の明細) と
    金額 (受入の金額) を写す。小計 = 金額の合計、消費税 = billing.services.consumption_tax (合計に 1 回)、税率 = consumption_tax_rate、
    期間 = billing.services.closing_period(取引先, 一番遅い受領日)、支払期日 = payment_due_date(取引先, 期間の終わり) (空なら空。作者の決め)、
    相手の登録番号 = 取引先の登録番号の写し、状態 = 未送信、目的 = 新規 "9"。billing の関数は関数の中で import する (頭の import の並びを変えない)。
    断るのは (1) 受入が 1 つも無い (2) 受入でない物 (3) ほかの取引先の受入 (4) 既に取消でない仕入明細に入っている受入
    (5) seizou どうしで送った発注の受入でない、のどれか (日本語の ValueError。頭は「発行できません。」)。受入の行をロックしてから見る。
    作った仕入明細と明細は変更の履歴に「作った」で残す。atomic を抜けた後に connect.services.send_self_invoice で相手へ送り (R17)、
    届かなければ返す物に saved_note (「仕入明細は相手に届いていません。…」) と saved_level = "warning" を付ける (仕入明細は成立する)。
    インボイス制度: 仕入明細書の記載事項は適格請求書と同じ。消費税は税率ごとの合計に 1 回 (作者の決め)。
    取適法: 受領日 = 支払期日の起算の日。支払期日は取引先の締日・支払の月・支払日から請求書と同じ計算で作る (作者の決め)。
    確かめた資料 = 中小企業共通 EDI インボイス仕様編 6.1.1 と Ⅰ.Ⅲ。
    訳: 同じ受入を 2 つの取消でない仕入明細に入れて二重に払う記録を作らないため、相手へ送れない受入 (seizou どうしで送っていない発注) を入れないため。出所 = AI の案 (作者の決め。請求書の create_invoice と同じ形)。
    変えてよい条件 = 1 つの受入を 2 つの仕入明細に分けて入れる決まりを作者が決めたとき。
    """
    from seizou.billing.services import closing_period, consumption_tax, consumption_tax_rate, payment_due_date

    receipts = list(receipts)
    if not receipts:
        raise ValueError("発行できません。受入がありません。仕入明細に入れる受入を 1 つ以上選んでください。")
    for obj in receipts:
        if not isinstance(obj, Receipt):
            raise ValueError(f"発行できません。受入でない物 ({obj}) が入っているので、外してください。")
    with transaction.atomic():
        pks = sorted({receipt.pk for receipt in receipts})
        locked = list(
            Receipt.objects.select_for_update()
            .filter(pk__in=pks)
            .select_related("line__purchase_order__partner", "line__item")
            .order_by("pk")
        )
        for receipt in locked:
            refusal = _self_invoice_refusal(receipt, partner)
            if refusal:
                raise ValueError(refusal)
        locked.sort(key=lambda row: (row.received_on, row.line.purchase_order.po_no, row.line.line_no, row.pk))
        amounts = [receipt.amount for receipt in locked]
        subtotal = sum(amounts, _ZERO)
        tax = consumption_tax(partner, amounts)
        period_from, period_to = closing_period(partner, max(receipt.received_on for receipt in locked))
        self_invoice = SelfInvoice.objects.create(
            self_invoice_no=next_number("self_invoice"),
            partner=partner,
            issued_on=issued_on,
            period_from=period_from,
            period_to=period_to,
            due_on=payment_due_date(partner, period_to),
            purpose_code=edi_codes.PURPOSE_NEW,
            subtotal=subtotal,
            tax=tax,
            total=subtotal + tax,
            tax_rate=consumption_tax_rate(),
            supplier_registration_no=partner.registration_no,
            status=SelfInvoice.STATUS_DRAFT,
        )
        record_history(self_invoice, user)
        for number, receipt in enumerate(locked, start=1):
            line = receipt.line
            row = SelfInvoiceLine.objects.create(
                self_invoice=self_invoice,
                line_no=number,
                receipt=receipt,
                received_on=receipt.received_on,
                qty=receipt.qty,
                unit=line.unit,
                unit_code=line.unit_code,
                price_basis=line.price_basis,
                unit_price=line.unit_price,
                amount=receipt.amount,
            )
            record_history(row, user)
    # 相手へ送る。HTTP は atomic の外 (R17)。connect と purchasing の循環を避けるため関数の中で import する。
    return _with_self_invoice_notice(_send_self_invoice(self_invoice, user), SELF_INVOICE_UNDELIVERED)


#: 仕入明細が相手に届かなかったときの知らせの字 (保存は済んだが人の手が要る物 = 黄。段 3)
SELF_INVOICE_UNDELIVERED = "仕入明細は相手に届いていません。{response} 直したら「送り直す」を押してください。"


def _send_self_invoice(self_invoice, user):
    """connect.services.send_self_invoice を呼ぶ (関数の中で import する)。返す物は取り直した SelfInvoice。"""
    from seizou.connect import services as connect_services

    return connect_services.send_self_invoice(self_invoice, user)


def _with_self_invoice_notice(self_invoice, template):
    """送った後の仕入明細に、届かなかったときの知らせの字 (saved_note) と黄 (saved_level = "warning") を付けて返す。届いたら何も付けない。"""
    if self_invoice.sent_at is None:
        self_invoice.saved_note = template.format(response=self_invoice.response)
        self_invoice.saved_level = "warning"
    return self_invoice


def assign_self_invoice_number(self_invoice, user):
    """仕入明細の今の版に送る番号を付けて返す。既にあればそのまま返す (送り直しは同じ番号を使い回す = R11)。

    connect.services.send_self_invoice が送る前に呼ぶ (届かなくても番号が残る)。返す物は uuid.UUID。
    """
    with transaction.atomic():
        locked = SelfInvoice.objects.select_for_update().get(pk=self_invoice.pk)
        if locked.message_id is None:
            locked.message_id = uuid.uuid4()
            locked.save(update_fields=["message_id"])
        return locked.message_id


def record_self_invoice_delivery(self_invoice, response, user, *, sent=False):
    """仕入明細を送った相手の返事を書く (500 字で切る)。sent が真なら送った日時を書き、状態が未送信なら答え待ちにする。

    sent が偽なら送った日時は今の値のまま (消さない)。connect.services.send_self_invoice が呼ぶ。返す物は取り直した SelfInvoice。
    """
    limit = SelfInvoice._meta.get_field("response").max_length
    with transaction.atomic():
        locked = SelfInvoice.objects.select_for_update().get(pk=self_invoice.pk)
        locked.response = response[:limit]
        fields = ["response"]
        if sent:
            locked.sent_at = timezone.now()
            fields.append("sent_at")
            if locked.status == SelfInvoice.STATUS_DRAFT:
                before = history.saved_state(locked)
                locked.status = SelfInvoice.STATUS_SENT
                fields.append("status")
                locked.save(update_fields=fields)
                record_history(locked, user, before=before)
                return locked
        locked.save(update_fields=fields)
        return locked


#: 取消が相手に届かなかったときの知らせの字 (保存は済んだが人の手が要る物 = 黄)
SELF_INVOICE_CANCEL_UNDELIVERED = "取消は相手に届いていません。{response} 直したら「送り直す」を押してください。"


def cancel_self_invoice_refusal(self_invoice) -> str:
    """仕入明細を取り消せない訳の文 (読むだけ)。取り消せるなら空の字。取消の確かめのカード (purchasing_tags) と cancel_self_invoice が同じ字を使う。

    取消済みなら「取り消し済みです」、相手の答えが届いていれば (has_reply) 「相手が答えた明細があります」の文。
    訳: 相手が答えた後に取り消して、相手と合意した明細と金額を後から動かさないため。出所 = 作者の決め。
    変えてよい条件 = 作者が答えた後の取消を決めたとき。
    """
    if self_invoice.status == SelfInvoice.STATUS_CANCELLED:
        return "取り消せません。この仕入明細は取り消し済みです。仕入明細の詳細を開き直してください。"
    if self_invoice.has_reply():
        return "取り消せません。相手が答えた明細があります。相手と会話で話し、相手の提案を受け入れて直してください。"
    return ""


def cancel_self_invoice(self_invoice, user):
    """相手がどの明細にも答える前の仕入明細を取り消し、取消を相手へ送る。

    atomic の中で仕入明細の行をロックし、cancel_self_invoice_refusal の字があれば何も変えずに ValueError。無ければ状態を取消にし、
    版を 1 上げ、目的を取消 "1" にし、新しい番号を付け、送った日時と返事を空にする。明細は消さず、値も変えない (入っていた受入は、
    取消でない仕入明細に入っていない受入として、また self_invoice_receipts に並ぶ)。返す物に saved_message = "取り消しました。"。
    atomic の外で connect.services.send_self_invoice (文書の種類 1C・目的 1)。届かなければ saved_note と saved_level = "warning"。
    訳: 相手が答えた後に取り消して、相手と合意した明細と金額を後から動かさないため。出所 = 作者の決め。
    変えてよい条件 = 作者が答えた後の取消を決めたとき。

    中小企業共通 EDI の取消仕入明細書 (文書タイプ 384・インボイス文書目的コード 1) の形で相手へ送る。電子帳簿保存法: 取り消した仕入明細と明細は
    消さずに状態「取消」で残し、送った中身は送り合った記録に残る。
    """
    with transaction.atomic():
        locked = SelfInvoice.objects.select_for_update().get(pk=self_invoice.pk)
        refusal = cancel_self_invoice_refusal(locked)
        if refusal:
            raise ValueError(refusal)
        before = history.saved_state(locked)
        locked.status = SelfInvoice.STATUS_CANCELLED
        locked.revision_no += 1
        locked.purpose_code = edi_codes.PURPOSE_CANCEL
        locked.message_id = uuid.uuid4()
        locked.sent_at = None
        locked.response = ""
        locked.save(update_fields=["status", "revision_no", "purpose_code", "message_id", "sent_at", "response"])
        record_history(locked, user, before=before)
    saved = _with_self_invoice_notice(_send_self_invoice(locked, user), SELF_INVOICE_CANCEL_UNDELIVERED)
    # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
    saved.saved_message = "取り消しました。"
    return saved


def self_invoice_line_amount(self_invoice, line, qty, unit_price):
    """仕入明細の明細を数と単価で数え直した金額。受入と同じ 1 か所 (masters.services.delivery_amount) を通す (式を写さない)。

    一式の明細は、全額を載せた受入 (受入の金額が 0 より大きい) の明細だけを一式の全額で数え直し、ほかは 0 (受入と同じ決まり = 作者の決め)。
    """
    return delivery_amount(self_invoice.partner, qty, unit_price, line.price_basis, completes=line.receipt.amount > 0)


def accept_self_invoice_proposals(self_invoice, line_nos, user):
    """相手の提案 (UV) を受け入れて仕入明細を直し、修正仕入明細を相手へ送る。

    選んだ明細は答えが提案の物だけ (ほかは「送れません。明細 n は提案の答えではありません。」)。取消の仕入明細は受けない。
    atomic の中で版を 1 上げ、目的を変更・修正 "4" にし、選んだ明細の数と単価を提案の値に、金額を受入と同じ 1 か所 (self_invoice_line_amount) で
    数え直し、直した版を新しい版にし、答えの列を空にする。小計と消費税 (billing の consumption_tax) と合計を計算し直す (税率・締め期間・支払期日は
    作った時の写しのまま)。新しい番号を付け、送った日時と返事を空にし、状態を未送信にする。受入と受入の金額は変えない (支払の見込みは、
    相手が直した内容を承諾した後に仕入明細の金額で数える = 作者の決め)。atomic の外で connect.services.send_self_invoice (文書の種類 1C)。
    届かなければ saved_note と saved_level = "warning"。
    中小企業共通 EDI の修正仕入明細の流れ: 発注者は提案回答の内容が適切と判断したら、修正入力して修正仕入明細を送る (インボイス仕様編 6.1.3 の ⑨)。
    取適法: 単価を下げる明細があれば、保存の前に黄色の確かめを出す (purchasing.checks の LawSelfInvoicePriceDown。止めない)。
    訳: 相手が提案と答えた明細だけを、提案の値のまま直すため (発注元が金額を手で打たない。取消の仕入明細は動かさない)。出所 = AI の案 (作者の決め。中小企業共通 EDI の修正仕入明細の流れ)。
    変えてよい条件 = 発注元が仕入明細の金額を直す道を作者が決めたとき。
    """
    from seizou.billing.services import consumption_tax

    chosen = sorted(set(line_nos or ()))
    if not chosen:
        raise ValueError("送れません。受け入れる提案を選んでください。")
    with transaction.atomic():
        locked = SelfInvoice.objects.select_for_update().select_related("partner").get(pk=self_invoice.pk)
        if locked.status == SelfInvoice.STATUS_CANCELLED:
            raise ValueError("送れません。この仕入明細は取り消してあります。")
        lines = {line.line_no: line for line in locked.lines.select_related("receipt")}
        for number in chosen:
            line = lines.get(number)
            if line is None or line.reply_type != edi_codes.SI_REPLY_PROPOSED:
                raise ValueError(f"送れません。明細 {number} は提案の答えではありません。")
        before = history.saved_state(locked)
        locked.revision_no += 1
        for number in chosen:
            line = lines[number]
            line_before = history.saved_state(line)
            line.qty = line.reply_qty
            line.unit_price = line.reply_unit_price
            line.amount = self_invoice_line_amount(locked, line, line.qty, line.unit_price)
            line.revised_in = locked.revision_no
            line.reply_type = ""
            line.reply_reason_code = ""
            line.reply_qty = None
            line.reply_unit_price = None
            line.reply_note = ""
            line.save(update_fields=[
                "qty", "unit_price", "amount", "revised_in", "reply_type", "reply_reason_code", "reply_qty", "reply_unit_price", "reply_note",
            ])
            record_history(line, user, before=line_before)
        amounts = [line.amount for line in lines.values()]
        locked.subtotal = sum(amounts, _ZERO)
        locked.tax = consumption_tax(locked.partner, amounts)
        locked.total = locked.subtotal + locked.tax
        locked.purpose_code = edi_codes.PURPOSE_CHANGE
        locked.message_id = uuid.uuid4()
        locked.sent_at = None
        locked.response = ""
        locked.status = SelfInvoice.STATUS_DRAFT
        locked.save(update_fields=[
            "revision_no", "subtotal", "tax", "total", "purpose_code", "message_id", "sent_at", "response", "status",
        ])
        record_history(locked, user, before=before)
    return _with_self_invoice_notice(_send_self_invoice(locked, user), SELF_INVOICE_REVISION_UNDELIVERED)


#: 修正仕入明細が相手に届かなかったときの知らせの字 (保存は済んだが人の手が要る物 = 黄)
SELF_INVOICE_REVISION_UNDELIVERED = "修正仕入明細は相手に届いていません。{response} 直したら「送り直す」を押してください。"


def _self_invoice_status(self_invoice, lines):
    """仕入明細の状態を明細の答えから決める。提案か確認できないが 1 つでもあれば確かめ中、全部が承諾 (AP・AC) なら確認済、ほかは答え待ち。"""
    codes = [line.reply_type for line in lines]
    if any(code in (edi_codes.SI_REPLY_PROPOSED, edi_codes.SI_REPLY_REJECTED) for code in codes):
        return SelfInvoice.STATUS_PARTIAL
    if codes and all(code in (edi_codes.SI_REPLY_ACCEPTED, edi_codes.SI_REPLY_ACKNOWLEDGED) for code in codes):
        return SelfInvoice.STATUS_CONFIRMED
    return SelfInvoice.STATUS_SENT


def record_self_invoice_reply(self_invoice, user, *, revision_no, reply_type, lines, reply_message_id, by=""):
    """相手の仕入明細回答を仕入明細に書く (connect の受け口が呼ぶ。user は None)。

    lines = [{line_no・reply_type・reply_reason_code・reply_qty・reply_unit_price・reply_note}]。載っていた明細の答えの列を書き替え、
    鑑ヘッダの答え (reply_type)・届いた日時・答えの番号を書き、状態を決め直す (提案か確認できないが 1 つでもあれば確かめ中、
    全部が承諾なら確認済、ほかは答え待ち)。受入と受入の金額は変えない。revision_no は受け口が今の版と比べた後の値。
    変更の履歴の人の字は by (「<取引先の名前> の seizou」)。返す物は取り直した SelfInvoice。
    """
    with transaction.atomic():
        locked = SelfInvoice.objects.select_for_update().get(pk=self_invoice.pk)
        current = {line.line_no: line for line in locked.lines.all()}
        for row in lines:
            line = current[row["line_no"]]
            before = history.saved_state(line)
            for key in ("reply_type", "reply_reason_code", "reply_qty", "reply_unit_price", "reply_note"):
                setattr(line, key, row[key])
            line.save(update_fields=["reply_type", "reply_reason_code", "reply_qty", "reply_unit_price", "reply_note"])
            record_history(line, user, before=before, by=by)
        before = history.saved_state(locked)
        locked.reply_type = reply_type
        locked.replied_at = timezone.now()
        locked.reply_message_id = reply_message_id
        fields = ["reply_type", "replied_at", "reply_message_id"]
        if locked.status != SelfInvoice.STATUS_CANCELLED:
            locked.status = _self_invoice_status(locked, list(current.values()))
            fields.append("status")
        locked.save(update_fields=fields)
        record_history(locked, user, before=before, by=by)
        return locked


def due_date_note(self_invoice):
    """仕入明細の詳細の上の黄色の 1 行の字 (読むだけ)。支払期日が一番早い受領日から 2 か月 (60 日) を超えるときだけ字を返し、ほかは空の字。

    取消の仕入明細と支払期日の空の仕入明細と明細の無い仕入明細には出さない。
    数え方は 2 か月 (一番早い受領日の 2 か月後の日の前日まで。billing.services.months_later。式を写さない)。
    例: 受領 7-01 なら 8-31 まで出さず、9-01 から出す (月末締め・翌月末払いでは出ず、翌々月払いのような条件で出る)。
    取適法 (2026-01-01 施行。旧下請法): 代金の支払期日は物を受け取った日から 60 日以内で定める。超えるときに知らせ、止めない (作者の決め)。
    確かめた資料 = 中小企業共通 EDI インボイス仕様編 Ⅰ.Ⅲ「インボイス以外の改定」。
    訳: 取適法の支払期日の 60 日は 2 か月で数える。よくある月末締め・翌月末払いで知らせが出続けると、本当に危ない条件の知らせが読まれなくなるおそれがあるため。
    出所 = 本人の決め (11-07 08:42) と、公正取引委員会の下請法の時代の講習会テキスト (令和 4 年 11 月) 44 ページの「受領後２か月以内」の運用・
    支払告示のリーフレットの「60日目まで（２か月以内）」。取適法でも同じ運用かは未確認 (取適法のよくある質問 Q68 と講習会の資料 1 に 2 か月の記載が見つからない)。
    変えてよい条件 = 公正取引委員会が取適法で別の数え方を示したとき、または本人が決め直したとき。
    画面の字では法令の当てはめを言い切らない (「…のおそれがあります」「確かめてください」の形)。
    """
    from seizou.billing.services import months_later

    if self_invoice.status == SelfInvoice.STATUS_CANCELLED or self_invoice.due_on is None:
        return ""
    days = [line.received_on for line in self_invoice.lines.all()]
    if not days:
        return ""
    earliest = min(days)
    limit = months_later(earliest, 2) - timedelta(days=1)
    if self_invoice.due_on <= limit:
        return ""
    return (
        f"支払期日 ({self_invoice.due_on:%Y-%m-%d}) が受領日 ({earliest:%Y-%m-%d}) から 2 か月 (60 日) を超えます。"
        "取適法の対象の取引では、支払期日の決まりに反するおそれがあります。取引先の支払の条件を確かめてください。"
    )
