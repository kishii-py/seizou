"""orders の公開する関数 (01 の 10.4)。画面とほかの部品は、この関数だけを通して受注を書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は記録のために受ける。記録の列がまだ無いので、今は受けるだけで書かない。
- ext (会社ごとの項目) は受けるが、None 以外が来たら NotImplementedError を投げる。
  黙って捨てると会社の項目が消えるため。保存は core の fields ができてから足す。
- 中心の決まりに当たったら RuleViolation(code, message)。message は RULES.md の番号の文と、次にすること。
- 決まりの番号の無い入力の誤り (区分が得意先でない・明細が無い・知らない鍵) は日本語の ValueError。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- ポカよけは検査の層 (seizou.core.checks) が services の前に見る。ここでは中心の決まりだけを見る。
- 明細の辞書の鍵は item・qty・unit・unit_price・due_date・note・customer_part_no・ext。
  update_order では、既にある明細を指す id も受ける。
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from seizou.core.exceptions import RuleViolation
from seizou.core.services import next_number
from seizou.masters.services import round_amount

from .models import Order, OrderLine

_ORDER_KEYS = frozenset({"order_date", "customer_order_no", "note", "customer"})
_LINE_KEYS = frozenset(
    {"item", "qty", "unit", "unit_price", "due_date", "note", "customer_part_no", "ext"}
)
_LINE_KEYS_ON_UPDATE = _LINE_KEYS | {"id"}
_LINE_FIELDS = ("item", "customer_part_no", "unit", "due_date", "note")
_SOURCES = frozenset(value for value, _label in Order.SOURCE_CHOICES)

_R02_MESSAGE = "数量と単価は 0 より大きい。0 より大きい数を入れてください。"
_R05_RULE = "取消した受注には、出荷も指示書も作れない。"


def _refuse_ext(ext):
    if ext is not None:
        raise NotImplementedError("会社ごとの項目の保存は core の fields ができてから")


def _refuse_unknown_keys(data, allowed, table_name):
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(
            f"知らない項目 ({'・'.join(unknown)}) があります。"
            f"{table_name}の列の名前だけを渡してください。"
        )


def _refuse_non_customer(partner):
    if not partner.is_customer:
        raise ValueError(
            f"取引先 {partner} は得意先ではありません。区分が得意先か両方の取引先を選んでください。"
        )


def _refuse_bad_lines(lines, allowed):
    for line in lines:
        _refuse_unknown_keys(line, allowed, "受注の明細")
        _refuse_ext(line.get("ext"))


def _dec(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _check_positive(qty, unit_price):
    """R02: 数量と単価は 0 より大きい。"""
    if not (qty > 0 and unit_price > 0):
        raise RuleViolation("R02", _R02_MESSAGE)


def _amount(customer, qty, unit_price):
    """R03: 金額は数量 × 単価で、端数は取引先 (空なら会社) の小計の計算方法で丸める。"""
    return round_amount(customer, qty * unit_price)


def _add_line(order, line, line_no):
    item = line["item"]
    qty = _dec(line["qty"])
    unit_price = _dec(line["unit_price"])
    _check_positive(qty, unit_price)
    return OrderLine.objects.create(
        order=order,
        line_no=line_no,
        item=item,
        customer_part_no=line.get("customer_part_no", ""),
        qty=qty,
        unit=line["unit"] if "unit" in line else item.unit,
        unit_price=unit_price,
        amount=_amount(order.customer, qty, unit_price),
        due_date=line.get("due_date"),
        note=line.get("note", ""),
    )


def _change_line(row, line, customer):
    for key in _LINE_FIELDS:
        if key in line:
            setattr(row, key, line[key])
    if "qty" in line:
        row.qty = _dec(line["qty"])
    if "unit_price" in line:
        row.unit_price = _dec(line["unit_price"])
    _check_positive(row.qty, row.unit_price)
    row.amount = _amount(customer, row.qty, row.unit_price)
    row.save()
    return row


def create_order(customer, lines, user, *, order_date, customer_order_no="", source="manual", ext=None):
    """受注を作る。明細は lines の並びの順に行番号を 1 から付ける。"""
    _refuse_ext(ext)
    _refuse_non_customer(customer)
    if source not in _SOURCES:
        raise ValueError(
            f"入口 {source!r} は使えません。"
            "手入力 (manual)・読み取り (intake)・受信箱 (inbox) のどれかにしてください。"
        )
    if not lines:
        raise ValueError("明細がありません。明細を 1 行以上入れてください。")
    _refuse_bad_lines(lines, _LINE_KEYS)
    with transaction.atomic():
        order = Order.objects.create(
            order_no=next_number("order"),
            customer=customer,
            order_date=order_date,
            customer_order_no=customer_order_no,
            source=source,
        )
        for line_no, line in enumerate(lines, start=1):
            _add_line(order, line, line_no)
        return order


def update_order(order, data, lines, user, *, ext=None):
    """受注の項目と明細を直す。

    data は order_date・customer_order_no・note・customer の鍵だけ。data にある鍵だけを書き換える。
    lines は直した後の明細の全部。id のある明細はその明細を直し (鍵のある列だけ)、id の無い明細は足す
    (行番号は今の最大 + 1 から)。lines に無い既存の明細は消す。残る明細は全部の金額を計算し直す。
    """
    _refuse_ext(ext)
    _refuse_unknown_keys(data, _ORDER_KEYS, "受注")
    if "customer" in data:
        _refuse_non_customer(data["customer"])
    _refuse_bad_lines(lines, _LINE_KEYS_ON_UPDATE)
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.status == Order.STATUS_CANCELLED:
            raise RuleViolation("R05", f"{_R05_RULE}直すときは、新しく受注を作ってください。")

        existing = {row.pk: row for row in locked.lines.all()}
        keep = set()
        for line in lines:
            if "id" not in line:
                continue
            if line["id"] not in existing:
                raise ValueError(
                    f"明細 {line['id']} はこの受注の明細ではありません。この受注の明細を選び直してください。"
                )
            keep.add(line["id"])
        next_line_no = max((row.line_no for row in existing.values()), default=0) + 1

        for key, value in data.items():
            setattr(locked, key, value)
        locked.save()

        locked.lines.exclude(pk__in=keep).delete()
        for line in lines:
            if "id" in line:
                _change_line(existing[line["id"]], line, locked.customer)
            else:
                _add_line(locked, line, next_line_no)
                next_line_no += 1
        return locked


def cancel_order(order, user, *, reason=""):
    """受注を取消にする。reason が空でなければ、備考の末尾に改行して「取消の理由: …」を足す。

    出荷のある受注でも止めない (現物優先)。取消の後に出荷と指示書を作れないのは、作る側の関数が見る。
    """
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.status == Order.STATUS_CANCELLED:
            raise RuleViolation("R05", f"{_R05_RULE}取消は済んでいるので、このままにしてください。")
        if reason:
            added = f"取消の理由: {reason}"
            note = f"{locked.note}\n{added}" if locked.note else added
            limit = Order._meta.get_field("note").max_length
            if len(note) > limit:
                raise ValueError(
                    f"取消の理由を足すと備考が {limit} 字を超えます。理由を短くしてください。"
                )
            locked.note = note
        locked.status = Order.STATUS_CANCELLED
        locked.save()
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


def remaining_qty(order_line):
    """受注の明細の残 (受注の数 − 出荷済みの数)。マイナスも返す (現物優先)。"""
    return order_line.qty - shipped_qty(order_line)
