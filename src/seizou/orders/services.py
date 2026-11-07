"""orders の公開する関数 (01 の 10.4)。画面とほかの部品は、この関数だけを通して受注を書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は記録のために受け、変更の履歴 (core.services.record_history。決め HIST-1 の伝票の「受注」) に書く。書く前に core.history.saved_state で前を取り、
  書いた後に同じ transaction.atomic の中で残す (止まって戻ったら履歴も残らない)。明細の行は受注の詳細の「履歴」に出る (OrderLine.history_owner)。
- ext (会社ごとの項目) は受けるが、None 以外が来たら NotImplementedError を投げる。
  黙って捨てると会社の項目が消えるため。保存は core の fields ができてから足す。
- 中心の決まりに当たったら RuleViolation(code, message)。message は RULES.md の番号の文と、次にすること。
- 決まりの番号の無い入力の誤り (取引先の種類が得意先でない・明細が無い・知らない鍵) は日本語の ValueError。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- ポカよけは検査の層 (seizou.core.checks) が services の前に見る。ここでは中心の決まりだけを見る。
- 明細の辞書の鍵は item・qty・unit・unit_code・unit_price・price_basis・due_date・note・customer_part_no・quote_no・process_name・ext。
  price_basis は単価の単位 (unit か lump。無ければ unit = 今と同じ)。
  unit_code は会社どうしの注文の受け入れが相手の送ったコードを渡す鍵 (無くてよい。画面の form は渡さず、一覧の名前の字を unit で渡す)。
  明細の数量単位コード (決め UNIT-1) を表に書くのは _line_unit の決まりだけ。
  quote_no は単価表から写した見積の番号、process_name は受信した注文の明細の工程名 (どちらも無ければ空の字。C100)。
  update_order では、既にある明細を指す id か order_line (この受注の OrderLine) も受ける (同じ意味)。
- 受注の状態 (受注・一部出荷・出荷済) は refresh_status が出荷済みの数から決め直す。
  shipping.services.ship と update_order の終わりに呼ぶ。止めない (現物優先)。
- 請求しない明細の印は mark_no_bill が付ける。外すのは clear_no_bill。この 2 つのほかは印を書かない。
"""

from __future__ import annotations

import json
from decimal import Decimal

from django.core.exceptions import FieldDoesNotExist
from django.db import transaction
from django.utils.crypto import constant_time_compare, salted_hmac
from django.db.models import DecimalField, ExpressionWrapper, F, ProtectedError, Sum, Value
from django.db.models import Count, Max, Min
from django.db.models.functions import Coalesce

from seizou.core import history
from seizou.core.exceptions import RuleViolation
from seizou.core.services import next_number, record_history, record_history_deleted
from seizou.masters.services import PRICE_BASIS_UNIT, line_amount, refuse_inactive, refuse_unknown_price_basis
from seizou.masters.services import unit_code_for, unit_name_for

from .models import Order, OrderLine

_ORDER_KEYS = frozenset({"order_date", "customer_order_no", "note", "customer"})
_LINE_KEYS = frozenset(
    {"item", "qty", "unit", "unit_code", "unit_price", "price_basis", "due_date", "note", "customer_part_no", "ext", "quote_no", "process_name"}
)
_LINE_KEYS_ON_UPDATE = _LINE_KEYS | {"id", "order_line", "line_no"}
_LINE_FIELDS = ("item", "customer_part_no", "unit", "due_date", "note", "quote_no", "process_name")
_SOURCES = frozenset(value for value, _label in Order.SOURCE_CHOICES)

_R02_MESSAGE = "保存できません。数量と単価は 0 より大きい。0 より大きい数を入れてください。"
_SHIPPED_LINE_MESSAGE = "保存できません。消した明細に出荷があります。数量を直すか、明細を残してください。"
#: 受注の編集を開いた後に、ほかの人か取引先の変更で受注が直っていたときの断り (update_order の expected_state。N350)
STALE_ORDER_MESSAGE = "保存できません。この受注は、画面を開いた後にほかの人か取引先の変更で直っています。開き直してから直してください。"

# order_state_token が比べる列 (受注の編集の画面が書き戻す列だけ。状態・請求しない印・金額・日時は入れない)
_STATE_ORDER_FIELDS = ("customer_id", "order_date", "customer_order_no", "note")
_STATE_LINE_FIELDS = (
    "pk", "line_no", "item_id", "customer_part_no", "qty", "unit", "unit_code",
    "price_basis", "unit_price", "quote_no", "due_date", "note",
)
_STATE_SALT = "seizou.orders.order_state_token"

# 出荷済みの数と残の数の型 (OrderLine.qty と同じ桁)
_QTY_FIELD = DecimalField(max_digits=12, decimal_places=2)


def _refuse_ext(ext):
    if ext is not None:
        raise NotImplementedError("会社ごとの項目の保存は core の fields ができてから")


def _refuse_unknown_keys(data, allowed, table_name):
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(
            f"保存できません。知らない項目 ({'・'.join(unknown)}) があります。"
            f"{table_name}の列の名前だけを渡してください。"
        )


def _refuse_non_customer(partner):
    if not partner.is_customer:
        raise ValueError(
            f"保存できません。取引先 {partner} は得意先ではありません。取引先の種類が得意先か両方の取引先を選んでください。"
        )


def _refuse_bad_lines(lines, allowed):
    for line in lines:
        _refuse_unknown_keys(line, allowed, "受注の明細")
        _refuse_ext(line.get("ext"))
        if "price_basis" in line:
            refuse_unknown_price_basis(line["price_basis"])


def _dec(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _check_positive(qty, unit_price):
    """R02: 数量と単価は 0 より大きい。

    訳: 0 以下の数量と単価を受注の明細に入れないため。出所 = RULES.md R02。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    if not (qty > 0 and unit_price > 0):
        raise RuleViolation("R02", _R02_MESSAGE)


def _amount(customer, qty, unit_price, price_basis):
    """R03: 金額は数量 × 単価 (単価の単位が式なら単価のまま) で、端数は取引先 (空なら会社) の小計の計算方法で丸める。

    訳: 一式の明細は数量を掛けずに単価を金額にするため (式は masters.services.line_amount の 1 か所)。出所 = RULES.md R03 と作者の決め PRICE-1b。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    return line_amount(customer, qty, unit_price, price_basis)


def _line_unit(line, item):
    """明細の単位の字と数量単位コード (表の 2 列) を返す (決め UNIT-1 = a)。

    - 鍵 unit_code が一覧のコードなら、それを使う (会社どうしの注文で相手が送ったコード)。字が空なら一覧の名前。
    - 鍵 unit が無ければ、部品の単位とコードを 2 つとも写す (「部品から写すときにコードごと写す」)。
    - unit の字が部品の単位と同じなら部品のコード、違えば名前から引く (一覧に無い字はコードを空にし、字はそのまま)。
    訳: 送り合いの数量に、中小企業共通 EDI の数量単位コード (付表 1 の注文数量単位コード。共通必須) を付け、渡された字は変えずに残すため (取引の記録の字を変えない。画面の入力は一覧の名前だけを渡し、読み取りと受け入れは相手の字を渡す)。出所 = 作者の決め・中小企業共通 EDI 付表 1。
    変えてよい条件 = 読み取りと受け入れの道でも、一覧に無い字を受けないと決めたとき。
    """
    code = line.get("unit_code") or ""
    if code and unit_name_for(code):
        return line.get("unit") or unit_name_for(code), code
    if "unit" not in line:
        return item.unit, item.unit_code
    unit = line["unit"]
    return unit, item.unit_code if unit == item.unit else unit_code_for(unit)


def _add_line(order, line, line_no):
    item = line["item"]
    qty = _dec(line["qty"])
    unit_price = _dec(line["unit_price"])
    price_basis = line.get("price_basis", PRICE_BASIS_UNIT)
    _check_positive(qty, unit_price)
    unit, unit_code = _line_unit(line, item)
    return OrderLine.objects.create(
        order=order,
        line_no=line_no,
        item=item,
        customer_part_no=line.get("customer_part_no", ""),
        qty=qty,
        unit=unit,
        unit_code=unit_code,
        unit_price=unit_price,
        price_basis=price_basis,
        amount=_amount(order.customer, qty, unit_price, price_basis),
        due_date=line.get("due_date"),
        note=line.get("note", ""),
        quote_no=line.get("quote_no", ""),
        process_name=line.get("process_name", ""),
    )


_NEW_LINE = object()  # update_order の明細のうち、既存の明細を指さない (足す) 物の印


def _line_ref(line):
    """update_order の明細が指す既存の明細の pk。id と order_line は同じ意味。どちらの鍵も無ければ _NEW_LINE。"""
    if "id" not in line and "order_line" not in line:
        return _NEW_LINE
    ref = line.get("id")
    if "order_line" in line:
        target = line["order_line"]
        target_pk = getattr(target, "pk", target)
        if "id" in line and ref != target_pk:
            raise ValueError(
                f"保存できません。明細の id ({ref}) と order_line ({target_pk}) が違う明細を指しています。"
                "どちらか 1 つにするか、同じ明細を指してください。"
            )
        ref = target_pk
    return ref


def _change_line(row, line, customer):
    """明細の直し。単位のコードは、単位の字が変わった時か unit_code を渡した時だけ引き直す (受注の編集の画面は同じ字を送り直すので、相手が送ったコードを消さない)。"""
    unit_changed = "unit_code" in line or ("unit" in line and line["unit"] != row.unit)
    for key in _LINE_FIELDS:
        if key in line:
            setattr(row, key, line[key])
    if unit_changed:
        # row.unit は上の for が新しい字に直した後の字。鍵 unit が無くコードだけ渡したときも今の字を使う
        row.unit, row.unit_code = _line_unit({**line, "unit": row.unit}, row.item)
    if "qty" in line:
        row.qty = _dec(line["qty"])
    if "unit_price" in line:
        row.unit_price = _dec(line["unit_price"])
    if "price_basis" in line:
        row.price_basis = line["price_basis"]
    _check_positive(row.qty, row.unit_price)
    row.amount = _amount(customer, row.qty, row.unit_price, row.price_basis)
    row.save()
    return row


def create_order(customer, lines, user, *, order_date, customer_order_no="", source="manual", ext=None):
    """受注を作る。明細は lines の並びの順に行番号を 1 から付ける。"""
    _refuse_ext(ext)
    _refuse_non_customer(customer)
    if source not in _SOURCES:
        raise ValueError(
            f"保存できません。入口 {source!r} は使えません。"
            "手入力 (manual)・読み取り (intake)・受信した注文 (inbox) のどれかにしてください。"
        )
    if not lines:
        raise ValueError("保存できません。明細がありません。明細を 1 行以上入れてください。")
    _refuse_bad_lines(lines, _LINE_KEYS)
    refuse_inactive(customer, [line.get("item") for line in lines])
    with transaction.atomic():
        order = Order.objects.create(
            order_no=next_number("order"),
            customer=customer,
            order_date=order_date,
            customer_order_no=customer_order_no,
            source=source,
        )
        record_history(order, user)
        for line_no, line in enumerate(lines, start=1):
            record_history(_add_line(order, line, line_no), user)
        if source != Order.SOURCE_MANUAL:
            order.saved_note = f"明細 {len(lines)} 行を手入力せずに受注にしました。"
        return order


def _state_text(value) -> str:
    """order_state_token に入れる 1 つの値の字。None は空、日付は isoformat、Decimal と数は str。"""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def order_state_token(order, lines=None) -> str:
    """受注と明細の今の値の字 (読むだけ)。受注の編集の画面が開いた時に隠しの欄に持ち、update_order が保存の時に今の字と比べる。

    比べる列は、受注の編集の画面が書き戻す列だけ。受注は customer_id・order_date・customer_order_no・note、
    明細は pk・line_no・item_id・customer_part_no・qty・unit・unit_code・price_basis・unit_price・quote_no・due_date・note。
    明細は line_no と pk の順に並べる (渡された並びに依らない)。
    状態 (status)・請求しない印 (no_bill)・金額 (amount)・日時の列は入れない (出荷で状態が変わっただけ・請求しない印を付けただけでは断らない)。
    返す字は素の sha256 でなく salted_hmac (sha256 の 16 進 64 字)。金額を伏せた人が、隠しの欄の値から単価を割り出せないようにするため。
    lines が無ければ order.lines を 1 回読む。画面の form は既に読んだ明細の並びを渡し、問い合わせを増やさない。
    """
    if lines is None:
        lines = order.lines.order_by("line_no", "pk")
    rows = sorted(lines, key=lambda row: (row.line_no, row.pk))
    state = [
        [_state_text(getattr(order, name)) for name in _STATE_ORDER_FIELDS],
        [[_state_text(getattr(row, name)) for name in _STATE_LINE_FIELDS] for row in rows],
    ]
    text = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    return salted_hmac(_STATE_SALT, text, algorithm="sha256").hexdigest()


def update_order(order, data, lines, user, *, ext=None, expected_state=None):
    """受注の項目と明細を直す。

    data は order_date・customer_order_no・note・customer の鍵だけ。data にある鍵だけを書き換える。
    lines は直した後の明細の全部。id か order_line のある明細はその明細を直し (鍵のある列だけ)、
    どちらも無い明細は足す (行番号は今の最大 + 1 から。足す明細に line_no があればその番号で足し、次の番号はその番号の次より小さくしない)。
    lines に無い既存の明細は消す。
    出荷のある明細は消せない (DB の PROTECT が止める物を、日本語の ValueError にして返す)。
    残る明細は全部の金額を計算し直し、終わりに refresh_status で受注の状態を決め直す。
    expected_state は、受注の編集の画面を開いた時の order_state_token の字。空でなければ、受注を押さえた後に今の字と比べ、
    違えば何も書かずに ValueError (STALE_ORDER_MESSAGE)。None か空の字なら比べない。
    訳: 取消した受注を直させないため (直すときは新しく受注を作る)。出所 = RULES.md R05。
    変えてよい条件 = 変えない (R は廃止で残す)。
    訳: 受信した注文の変更で足した明細の行番号を、相手 (発注元) の行番号とそろえるため (出荷の知らせと検収は行番号で明細を指す)。出所 = 作者の決め (11-07。発注の変更の受け入れ)。変えてよい条件 = 送り合いが行番号でなく明細の識別で明細を指すようになったとき。
    訳: 画面を開いた後にほかの人が直した値と、取引先から受け入れた変更 (受信した注文の第 2 版以降) を、古い画面の保存で黙って元に戻さないため。比べる字は画面が書き戻す列だけの salted_hmac (状態・請求しない印・金額は入れない。金額を伏せた人に単価を割り出させない)。expected_state が空なら比べない (受け入れの道と、隠しの欄の無い古い画面)。出所 = エンタープライズ化の調べ 第一弾の指摘 money-1 (11-07。確かめ直し 416 回目の直し方の直し (a)(b)(c))。
    変えてよい条件 = 受注に版の列を足し、版の番号で比べるようにしたとき。
    """
    _refuse_ext(ext)
    _refuse_unknown_keys(data, _ORDER_KEYS, "受注")
    if "customer" in data:
        _refuse_non_customer(data["customer"])
    _refuse_bad_lines(lines, _LINE_KEYS_ON_UPDATE)
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.status == Order.STATUS_CANCELLED:
            raise RuleViolation("R05", "保存できません。取消した受注です。直すときは、新しく受注を作ってください。")
        if expected_state:
            # 受注を押さえた後に読むので、ほかの保存と受け入れ (同じ受注を押さえる) が済んだ後の値と比べる。
            current = order_state_token(locked, list(locked.lines.order_by("line_no", "pk")))
            if not constant_time_compare(expected_state, current):
                raise ValueError(STALE_ORDER_MESSAGE)

        existing = {row.pk: row for row in locked.lines.all()}
        refs = [_line_ref(line) for line in lines]
        keep = set()
        for ref in refs:
            if ref is _NEW_LINE:
                continue
            if ref not in existing:
                raise ValueError(
                    f"保存できません。明細 {ref} はこの受注の明細ではありません。この受注の明細を選び直してください。"
                )
            keep.add(ref)
        next_line_no = max((row.line_no for row in existing.values()), default=0) + 1

        # 変更の履歴の前 (DB から取り直した値)。明細は残す物も消す物も、書く前に取る
        before = history.saved_state(locked)
        line_before = {pk: history.saved_state(row) for pk, row in existing.items()}
        for key, value in data.items():
            setattr(locked, key, value)
        locked.save()
        record_history(locked, user, before=before)

        # 消す明細は消す前に 1 行ずつ「消した」を残す (queryset の delete は行ごとの save を通らないため)。
        # 出荷が指していて消せなければ、同じ atomic が戻るので履歴も残らない。
        for pk, row in existing.items():
            if pk not in keep:
                record_history_deleted(row, user)
        try:
            locked.lines.exclude(pk__in=keep).delete()
        except ProtectedError as exc:
            # 出荷 (shipping の PROTECT) が指す明細は DB が消させない。消す前に止まるので、表はそのまま。
            raise ValueError(_SHIPPED_LINE_MESSAGE) from exc
        for line, ref in zip(lines, refs, strict=True):
            if ref is not _NEW_LINE:
                record_history(_change_line(existing[ref], line, locked.customer), user, before=line_before[ref])
            else:
                line_no = line.get("line_no") or next_line_no
                record_history(_add_line(locked, line, line_no), user)
                next_line_no = max(next_line_no, line_no + 1)
        return refresh_status(locked, user)


def cancel_order(order, user, *, reason=""):
    """受注を取消にする。reason が空でなければ、備考の末尾に改行して「取消の理由: …」を足す。

    出荷のある受注でも止めない (現物優先)。取消の後に出荷と指示書を作れないのは、作る側の関数が見る。
    訳: 取り消し済みの受注をもう一度取り消さないため。出所 = RULES.md R05。
    変えてよい条件 = 変えない (R は廃止で残す)。
    """
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.status == Order.STATUS_CANCELLED:
            raise RuleViolation("R05", "取り消せません。この受注は取り消し済みです。受注の詳細を開き直してください。")
        before = history.saved_state(locked)
        if reason:
            added = f"取消の理由: {reason}"
            note = f"{locked.note}\n{added}" if locked.note else added
            limit = Order._meta.get_field("note").max_length
            if len(note) > limit:
                raise ValueError(
                    f"取り消せません。取消の理由を足すと備考が {limit} 字を超えます。理由を短くしてください。"
                )
            locked.note = note
        locked.status = Order.STATUS_CANCELLED
        locked.save()
        record_history(locked, user, before=before)
        # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
        locked.saved_message = "取り消しました。"
        return locked


def mark_no_bill(order_line, user):
    """受注の明細に「請求しない」の印を付ける。印の付いた明細の出荷は、請求書の新規登録の候補 (billing.services.billable_shipments)・
    「請求書に入っていない出荷」の数 (ダッシュボードと左のメニュー)・出し切らずに終えた一式の黄の表に出ない。
    出荷の数と出荷の金額 (ダッシュボードの出荷の金額と売上の数え方) と、発行した請求書は変えない。
    印は明細ごとで、受注の状態と単価の単位を見ない (入口は請求書の新規登録の一式の表だけ。一式でない明細の出荷は billing.services.record_outside_payment で請求の候補から外す)。もう付いていれば何も書かずに同じ字を返す (断らない)。
    訳: 出し切らずに終えた一式を請求しないと決めた明細を、一式の表と請求の候補から外すため。出所 = 作者の決め C107-3・C107-3b・C107-3c。
    変えてよい条件 = 作者が請求しない明細の扱いを決め直したとき。
    """
    with transaction.atomic():
        locked = OrderLine.objects.select_for_update().get(pk=order_line.pk)
        if not locked.no_bill:
            before = history.saved_state(locked)
            locked.no_bill = True
            locked.save(update_fields=["no_bill"])
            record_history(locked, user, before=before)
    # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
    locked.saved_message = "請求しないにしました。"
    return locked


def clear_no_bill(order_line, user):
    """受注の明細の「請求しない」の印を外す。外すと、出し切らずに終えた一式なら請求書の新規登録の一式の表にまた並び、
    まだ請求書に入っていない出荷も請求の候補 (billing.services.billable_shipments) に戻る (読む所は印の列を見るだけ)。
    出荷の数と出荷の金額と、発行した請求書は変えない。印が無ければ何も書かずに同じ字を返す (断らない)。
    訳: 押し間違いで請求から黙って落ちたままにしないため。出所 = 作者の決め C107-8。
    変えてよい条件 = 作者が請求しない明細の扱いを決め直したとき。
    """
    with transaction.atomic():
        locked = OrderLine.objects.select_for_update().get(pk=order_line.pk)
        if locked.no_bill:
            before = history.saved_state(locked)
            locked.no_bill = False
            locked.save(update_fields=["no_bill"])
            record_history(locked, user, before=before)
    # 画面の知らせの字 (入力の土台 core.views.with_saved_note が「保存しました。」の代わりに出す)。表に書かない。
    locked.saved_message = "請求しないを外しました。"
    return locked


def shipped_qty(order_line):
    """受注の明細に対する出荷済みの数 (出荷から集計。列に持たない)。

    出荷の表 (shipping) が related_name="shipments" で明細を指す前提。出荷の表が無い間は 0。
    """
    shipments = getattr(order_line, "shipments", None)
    if shipments is None:
        return Decimal("0")
    return shipments.aggregate(
        total=Coalesce(
            Sum("qty"),
            Value(Decimal("0")),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
    )["total"]


def acceptance_summary(order_line):
    """検収 (connect の Acceptance) の数の合計と最後の検収日。逆の関係 acceptances を読むだけで、orders は connect を import しない。

    返す物は {"count": 件数, "total": 検収の数の合計 (Decimal), "last_on": 最後の検収日か None}。
    今の版より前の版の注文への検収があるときだけ、"old_revision" (その検収の版の一番小さい値) と "current_revision" (受注が今合っている注文の版) を足す
    (前の版への検収も止めずに受けてあり、受注の詳細の検収の枡に「第 n 版の注文への検収 (今は第 m 版)」の札を出すのに使う。無いときは鍵を足さない)。
    今の版は受注の逆の関係 inbox_message (受信した注文の第 1 版の行) の current_revision_no()。受信した注文で作った受注でなければ見ない。
    connect の部品が無い会社 (逆の関係が無い) では 0 件。読むだけ。
    """
    rows = getattr(order_line, "acceptances", None)
    if rows is None:
        return {"count": 0, "total": Decimal("0"), "last_on": None}
    sums = rows.aggregate(
        count=Count("pk"),
        total=Coalesce(Sum("qty"), Value(Decimal("0")), output_field=_QTY_FIELD),
        last_on=Max("accepted_on"),
    )
    total = sums["total"]
    # 受信した注文は受注の 1 対 1 の逆の関係。無ければ RelatedObjectDoesNotExist (AttributeError の子) で None。
    inbox = getattr(order_line.order, "inbox_message", None) if sums["count"] else None
    current = inbox.current_revision_no() if inbox is not None else None
    old = None
    if current is not None and current > 1:
        old = rows.filter(order_revision_no__lt=current).aggregate(low=Min("order_revision_no"))["low"]
    # SQLite は集計の値を桁なしで返すことがあるので Decimal(str()) で受ける。
    summary = {
        "count": sums["count"],
        "total": total if isinstance(total, Decimal) else Decimal(str(total)),
        "last_on": sums["last_on"],
    }
    if old is not None:
        summary.update(old_revision=old, current_revision=current)
    return summary


def remaining_qty(order_line):
    """受注の明細の残 (受注の数 − 出荷済みの数)。マイナスも返す (現物優先)。"""
    return order_line.qty - shipped_qty(order_line)


def annotate_remaining(queryset):
    """受注の明細の queryset に、出荷済みの数 (shipped_total) と残の数 (remaining_total) を足して返す。

    shipped_qty・remaining_qty と同じ式 (出荷の数の合計、出荷が無ければ 0)。一覧と候補の口が使う。
    出荷の表 (shipping) が無いときは、出荷済みの数を 0 とする。
    """
    try:
        OrderLine._meta.get_field("shipments")
    except FieldDoesNotExist:
        shipped = Value(Decimal("0"), output_field=_QTY_FIELD)
    else:
        shipped = Coalesce(Sum("shipments__qty"), Value(Decimal("0")), output_field=_QTY_FIELD)
    return queryset.annotate(shipped_total=shipped).annotate(
        remaining_total=ExpressionWrapper(F("qty") - F("shipped_total"), output_field=_QTY_FIELD)
    )


def refresh_status(order, user):
    """受注の状態を、明細の出荷済みの数から決め直す。返す物は取り直した Order。

    取消はそのまま。全部の明細が出荷済み (出荷済みの数 >= 数量) なら出荷済、1 つでも出荷があれば一部出荷、
    ほかは受注。明細が 0 行なら受注。受注より多い出荷も出荷済にする (止めない。現物優先)。
    """
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.status == Order.STATUS_CANCELLED:
            return locked
        rows = list(annotate_remaining(OrderLine.objects.filter(order=locked)))
        if rows and all(row.shipped_total >= row.qty for row in rows):
            status = Order.STATUS_SHIPPED
        elif any(row.shipped_total > 0 for row in rows):
            status = Order.STATUS_PARTIAL
        else:
            status = Order.STATUS_OPEN
        if locked.status != status:
            before = history.saved_state(locked)
            locked.status = status
            locked.save(update_fields=["status"])
            record_history(locked, user, before=before)
        return locked
