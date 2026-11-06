"""receiving の公開する関数 (入荷と引当)。画面とほかの部品は、この関数だけを通して入荷と引当を書く (R13)。

- 入荷 (Receiving) は得意先から受け入れた現物。出所は無償支給・有償支給・預かり。注文とは切り離す (決め 11-06 18:3x)。
- 入荷の先は引当 (Allocation)。引当は入荷と受注の明細を数量で結ぶ。1 つの入荷の引当の合計は、入荷の数量を超えない。
- 入荷を受注の明細から入れたとき (order_line を渡したとき) は、同じ atomic の中で引当を 1 行作る (1 対 1 の自動の引当)。
- 預かり残 = その明細の引当の合計 − その明細の出荷済みの合計。出荷は shipping の Shipment を apps.get_model で読む
  (出荷の部品が無ければ 0。orders.board の形)。
- 金額は持たない。在庫の動き (stock) とはつながない (会期後)。
- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。番号の無い入力の誤りは日本語の ValueError。
"""

from __future__ import annotations

from decimal import Decimal

from django.apps import apps
from django.db import transaction
from django.db.models import DecimalField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from .models import SOURCE_CHOICES, Allocation, Receiving, _qty_text

__all__ = [
    "STATE_NONE",
    "STATE_PARTIAL",
    "STATE_FULL",
    "create_receiving",
    "allocate",
    "allocated_total",
    "remaining",
    "receiving_state",
    "annotate_allocated",
]

#: 受注の明細の入荷の状態 (receiving_state が返す字)
STATE_NONE = "none"
STATE_PARTIAL = "partial"
STATE_FULL = "full"

# 数の合計の型 (Receiving.qty・Allocation.qty と同じ桁)
_QTY_FIELD = DecimalField(max_digits=12, decimal_places=2)
_ZERO = Decimal("0")
_SOURCES = tuple(value for value, _label in SOURCE_CHOICES)


def _dec(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _user_or_none(user):
    return user if getattr(user, "pk", None) is not None else None


def _shipment_model():
    """出荷の表 (Shipment)。shipping の部品が無ければ None (出荷の合計は 0)。"""
    try:
        return apps.get_model("shipping", "Shipment")
    except LookupError:
        return None


def _shipped_total(order_line):
    """その明細の出荷済みの数の合計 (Decimal。出荷の部品が無ければ 0)。読むだけ。"""
    shipment_model = _shipment_model()
    if shipment_model is None:
        return _ZERO
    total = shipment_model._default_manager.filter(order_line=order_line).order_by().aggregate(total=Sum("qty"))["total"]
    return _ZERO if total is None else _dec(total)


def _allocated_of_receiving(receiving):
    """その入荷の引当の合計 (Decimal)。読むだけ。"""
    total = Allocation.objects.filter(receiving=receiving).order_by().aggregate(total=Sum("qty"))["total"]
    return _ZERO if total is None else _dec(total)


def _add_allocation(receiving, order_line, qty):
    """引当を 1 行作る。呼ぶ側の transaction.atomic の中で、入荷の行を select_for_update で取り直してから呼ぶ。

    数量が 0 以下・同じ入荷と明細の引当が既にある・引当の合計が入荷の数量を超える、は日本語の ValueError。
    """
    qty = _dec(qty)
    if not qty > 0:
        raise ValueError("保存できません。引当の数量は 0 より大きい数にしてください。")
    if Allocation.objects.filter(receiving=receiving, order_line=order_line).exists():
        raise ValueError("保存できません。この入荷は、この受注の明細に既に引き当てています。")
    already = _allocated_of_receiving(receiving)
    if already + qty > _dec(receiving.qty):
        rest = _dec(receiving.qty) - already
        raise ValueError(
            f"保存できません。引当の合計 ({_qty_text(already + qty)}) が入荷の数量 ({_qty_text(receiving.qty)}) を超えます。"
            f"引き当てられる残りは {_qty_text(rest)} です。"
        )
    return Allocation.objects.create(receiving=receiving, order_line=order_line, qty=qty)


# --- 読む ---


def allocated_total(order_line) -> Decimal:
    """その受注の明細の引当の合計 (Decimal。引当が無ければ 0)。読むだけ。"""
    total = Allocation.objects.filter(order_line=order_line).order_by().aggregate(total=Sum("qty"))["total"]
    return _ZERO if total is None else _dec(total)


def remaining(order_line) -> Decimal:
    """預かり残 = その明細の引当の合計 − その明細の出荷済みの合計 (マイナスも返す)。読むだけ。"""
    return allocated_total(order_line) - _shipped_total(order_line)


def receiving_state(order_line) -> str:
    """その明細の入荷の状態。引当が 0 なら "none"、受注の数より少なければ "partial"、受注の数に届いていれば "full"。"""
    total = allocated_total(order_line)
    if total <= 0:
        return STATE_NONE
    if total < _dec(order_line.qty):
        return STATE_PARTIAL
    return STATE_FULL


def annotate_allocated(order_lines):
    """受注の明細の queryset に、引当の合計 (allocated_total。引当が無ければ 0) を足して返す。一覧と候補の口が使う。

    合計は Subquery で足す (多の join を足さないので、orders の annotate_remaining の後に当てても数が重ならない)。読むだけ。
    """
    totals = (
        Allocation.objects.filter(order_line=OuterRef("pk"))
        .order_by()
        .values("order_line")
        .annotate(total=Sum("qty"))
        .values("total")[:1]
    )
    return order_lines.annotate(
        allocated_total=Coalesce(Subquery(totals, output_field=_QTY_FIELD), Value(_ZERO), output_field=_QTY_FIELD)
    )


# --- 書く ---


def create_receiving(data, user, *, order_line=None, allocate_qty=None) -> Receiving:
    """入荷を 1 件入れる。data は item・partner・source・qty・received_on・lot・note。

    数量が 0 以下と、出所が無償支給・有償支給・預かりの外は日本語の ValueError。
    order_line (受注の明細) があれば、同じ atomic の中で引当を 1 行作る。引当の数量は allocate_qty か、無ければ
    受け入れた数と (その明細の受注の数 − 既にある引当の合計) の小さい方。0 以下なら引当を作らない。
    """
    qty = _dec(data.get("qty"))
    if not qty > 0:
        raise ValueError("保存できません。入荷の数量は 0 より大きい数にしてください。")
    source = data.get("source")
    if source not in _SOURCES:
        raise ValueError("保存できません。出所は「無償支給」「有償支給」「預かり」のどれかにしてください。")
    with transaction.atomic():
        receiving = Receiving.objects.create(
            item=data["item"],
            partner=data.get("partner"),
            source=source,
            qty=qty,
            received_on=data["received_on"],
            lot=data.get("lot") or "",
            note=data.get("note") or "",
            created_by=_user_or_none(user),
        )
        if order_line is not None:
            if allocate_qty is None:
                share = min(qty, _dec(order_line.qty) - allocated_total(order_line))
            else:
                share = _dec(allocate_qty)
            if share > 0:
                _add_allocation(receiving, order_line, share)
        return receiving


def allocate(receiving, order_line, qty, user) -> Allocation:
    """手の引当 (入荷の qty だけを受注の明細に充てる)。引当の合計が入荷の数量を超えれば日本語の ValueError。

    数量が 0 以下と、同じ入荷と明細の引当が既にあるときも ValueError。画面は会期後 (今は試験だけが呼ぶ)。
    """
    with transaction.atomic():
        locked = Receiving.objects.select_for_update().get(pk=receiving.pk)
        return _add_allocation(locked, order_line, qty)
