"""stock の公開する関数 (完成品の在庫)。画面とほかの部品は、この関数だけを通して在庫の動きを書く。

- 在庫数 = その部品の動きの数量の合計 − その部品の出荷の数量の合計。動きが 1 件も無い部品は在庫を数えていない (None)。
  ただし棚卸しで数え始めた部品 (動きの最初の行 = 入れた順で pk の一番小さい行が棚卸しの調整) は、その行の日付
  (数え始めの日) より前の出荷日の出荷を引かない (実地の数に入っている)。数え始めの日と同じ日と後の日の出荷は引く。
- 出荷は動きの表に書かず、集計で引く (shipping の Shipment を apps.get_model で読む。出荷の部品が無ければ 0)。
- マイナスの在庫は止めない (現物優先。一覧と盤で赤の数にして知らせるだけ)。
- 引数の順は 対象 → 値 → user → * の後のキーワード (services の書き方の設計の決め)。
- 書くのは transaction.atomic の中で、部品の行を select_for_update で取り直してから (部品の行は読むだけで書かない)。
- 番号の無い入力の誤りは日本語の ValueError。数量の符号は種類で決める (入庫は正、出庫は負、調整は渡された符号のまま)。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldDoesNotExist
from django.db import transaction
from django.db.models import (
    Case,
    DateField,
    DecimalField,
    Exists,
    ExpressionWrapper,
    OuterRef,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.db.models.lookups import Exact
from django.utils import timezone

from .models import KIND_ADJUSTED, KIND_FINISHED, KIND_ISSUED, KIND_RECEIVED, StockMove, _qty_text, kind_sign

# 数の合計の型 (StockMove.qty と同じ桁)
_QTY_FIELD = DecimalField(max_digits=12, decimal_places=2)
_ZERO = Decimal("0")

_POSITIVE_MESSAGE = "数量は 0 より大きい数にしてください。"

# 棚卸しの調整の後の知らせの字 (入力の土台 core.views.with_saved_note が、返した動きの saved_message を「保存しました。」の代わりに出す)。
# 動きのある部品は「在庫数を 190 から 185 にしました (差 -5)。」、数え始めは「5 から数え始めました。」(adjust が作る)。
# 在庫を数えていない部品に実地 0 を入れたときは、下の字のまま (作者の決め)。
COUNTED_FROM_ZERO_MESSAGE = "0 から数え始めました。"


def _dec(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _pk(item):
    """部品か部品の pk を pk にする。"""
    return getattr(item, "pk", item)


def _shipment_model():
    """出荷の表。shipping の部品が無ければ None (出荷の合計は 0)。"""
    try:
        return apps.get_model("shipping", "Shipment")
    except LookupError:
        return None


def _shipped_on_or_after(item, day):
    """その部品の出荷のうち、出荷日が day と同じ日か後の物の数量の合計 (Decimal。出荷の部品が無ければ 0)。読むだけ。"""
    shipment_model = _shipment_model()
    if shipment_model is None:
        return _ZERO
    total = (
        shipment_model._default_manager.filter(order_line__item=_pk(item), shipped_at__gte=day)
        .order_by()
        .aggregate(total=Sum("qty"))["total"]
    )
    return _ZERO if total is None else _dec(total)


def _user_or_none(user):
    return user if getattr(user, "pk", None) is not None else None


def _lock(item):
    """部品の行を select_for_update で取り直す (同じ部品の動きを 1 本ずつ入れるため。部品は書かない)。"""
    return type(item)._default_manager.select_for_update().get(pk=item.pk)


def _move(item, kind, qty, moved_on, user, *, source=None, note="", before_qty=None, counted_qty=None):
    """在庫の動きを 1 行作る。呼ぶ側の transaction.atomic の中で呼ぶ。符号は種類で合わせ、0 は ValueError。

    ただし棚卸しの調整の 0 は残す (在庫を数えていない部品に実地 0 を入れ、0 から数え始める行。表の制約も同じ)。
    before_qty と counted_qty は棚卸しの調整の控え (adjust だけが渡す。ほかの呼び手は渡さず空のまま)。
    """
    qty = _dec(qty)
    sign = kind_sign(kind)
    if sign:
        qty = abs(qty) * sign
    if qty == 0 and kind != KIND_ADJUSTED:
        raise ValueError("保存できません。数量が 0 です。0 でない数にしてください。")
    locked = _lock(item)
    return StockMove.objects.create(
        item=locked,
        kind=kind,
        qty=qty,
        before_qty=before_qty,
        counted_qty=counted_qty,
        moved_on=moved_on,
        content_type=ContentType.objects.get_for_model(source) if source is not None else None,
        object_id=source.pk if source is not None else None,
        note=note or "",
        moved_by=_user_or_none(user),
    )


# --- 読む (在庫数) ---


def balances(items) -> dict:
    """部品 (か pk) の並びに対する在庫数を {pk: Decimal か None} で返す。動きの無い部品は None。

    問い合わせは 2 回まで (動きの行を入れた順 (pk の順) に 1 回、出荷の合計を部品と出荷日ごとに 1 回)。
    部品ごとの動きの合計と最初の行 (棚卸しの調整なら、その日付が数え始めの日) と引き算は Python で行う。
    数え始めの日のある部品は、出荷日がその日より前の出荷を足さない (annotate_balance と同じ数え方)。
    """
    pks = [_pk(item) for item in items]
    if not pks:
        return {}
    moved = {}
    counted_from = {}
    for item_pk, kind, moved_on, qty in (
        StockMove.objects.filter(item__in=pks).order_by("pk").values_list("item", "kind", "moved_on", "qty")
    ):
        if item_pk not in moved:
            # 部品の最初の行 (入れた順)。棚卸しの調整なら、その日付から数え始めた部品
            moved[item_pk] = _ZERO
            counted_from[item_pk] = moved_on if kind == KIND_ADJUSTED else None
        moved[item_pk] += _dec(qty)
    shipped = {}
    shipment_model = _shipment_model()
    if shipment_model is not None and moved:
        rows = (
            shipment_model._default_manager.filter(order_line__item__in=list(moved))
            .order_by()
            .values("order_line__item", "shipped_at")
            .annotate(total=Sum("qty"))
        )
        for row in rows:
            item_pk = row["order_line__item"]
            start = counted_from.get(item_pk)
            if start is not None and row["shipped_at"] < start:
                continue  # 数え始めの日より前の出荷は、実地の数に入っている
            shipped[item_pk] = shipped.get(item_pk, _ZERO) + _dec(row["total"])
    return {pk: (moved[pk] - shipped.get(pk, _ZERO)) if pk in moved else None for pk in pks}


def balance(item):
    """その部品の在庫数。動きが無ければ None (在庫を数えていない)。全部の出荷を引く (取消の受注の出荷も数える = 起きた事)。

    ただし棚卸しで数え始めた部品は、数え始めの日より前の出荷日の出荷を引かない (実地の数に入っている)。
    """
    return balances([item])[_pk(item)]


def annotate_balance(queryset, item_ref):
    """部品を item_ref で指す queryset に、数え始めの日 (stock_counted_from)・在庫数 (stock_balance)・
    最後の動きの日 (last_moved_on) を足して返す。

    item_ref は外の行の部品の列の名前 (Item の queryset なら "pk"、受注の明細の queryset なら "item_id")。
    stock_counted_from は、棚卸しで数え始めた部品 (動きの最初の行 = pk の一番小さい行が棚卸しの調整) のその行の日付。
    ほかの部品 (最初の行が製造の完了・受入・払い出しの部品と、動きの無い部品) は None。
    stock_balance は balances と同じ数え方 (数え始めの日より前の出荷日の出荷を引かない)。
    動きと出荷の合計は Subquery で足す (多の join を足さない。annotate_remaining の後に当てても残が重ならない)。
    動きが無い部品は stock_balance も last_moved_on も None。読むだけ。
    """
    moves = StockMove.objects.filter(item=OuterRef(item_ref))
    first = moves.order_by("pk")
    counted_from = Case(
        When(
            Exact(Subquery(first.values("kind")[:1]), Value(KIND_ADJUSTED)),
            then=Subquery(first.values("moved_on")[:1]),
        ),
        default=Value(None, output_field=DateField()),
        output_field=DateField(),
    )
    # 出荷の Subquery が OuterRef で数え始めの日を読むので、先の annotate で足す (同じ annotate の中では読めない)
    queryset = queryset.annotate(stock_counted_from=counted_from)
    moved_total = Subquery(
        moves.order_by().values("item").annotate(total=Sum("qty")).values("total")[:1], output_field=_QTY_FIELD
    )
    shipment_model = _shipment_model()
    if shipment_model is None:
        shipped_total = Value(_ZERO, output_field=_QTY_FIELD)
    else:
        shipped = (
            shipment_model._default_manager.filter(
                order_line__item=OuterRef(item_ref),
                # 数え始めの日のある部品は、その日と後の出荷だけ (無い部品は全部 = date.min から)
                shipped_at__gte=Coalesce(
                    OuterRef("stock_counted_from"), Value(date.min, output_field=DateField()), output_field=DateField()
                ),
            )
            .order_by()
            .values("order_line__item")
            .annotate(total=Sum("qty"))
            .values("total")[:1]
        )
        shipped_total = Coalesce(Subquery(shipped, output_field=_QTY_FIELD), Value(_ZERO), output_field=_QTY_FIELD)
    stock_balance = Case(
        When(
            Exists(moves),
            then=ExpressionWrapper(
                Coalesce(moved_total, Value(_ZERO), output_field=_QTY_FIELD) - shipped_total,
                output_field=_QTY_FIELD,
            ),
        ),
        default=Value(None, output_field=_QTY_FIELD),
        output_field=_QTY_FIELD,
    )
    last_moved_on = Subquery(moves.order_by("-moved_on", "-pk").values("moved_on")[:1])
    return queryset.annotate(stock_balance=stock_balance, last_moved_on=last_moved_on)


def finished_qty(workorder):
    """指示書の完成の数 = その指示書の最後の工程 (順が最大) の実績の合計。工程か progress の部品が無ければ 0。読むだけ。

    注残の盤の completed_total と同じ数え方 (指示書の数量で切らない = 現物優先)。
    """
    steps = list(workorder.steps.all())
    if not steps:
        return _ZERO
    last = max(steps, key=lambda step: (step.seq, step.pk))
    try:
        type(last)._meta.get_field("progress_records")
    except FieldDoesNotExist:
        return _ZERO
    total = last.progress_records.order_by().aggregate(total=Sum("qty"))["total"]
    return _ZERO if total is None else _dec(total)


def stocked_qty(workorder):
    """その指示書を元の記録に持つ製造の完了の動きの合計 (既に入庫にした数)。読むだけ。"""
    total = (
        StockMove.objects.filter(
            kind=KIND_FINISHED,
            content_type=ContentType.objects.get_for_model(workorder),
            object_id=workorder.pk,
        )
        .order_by()
        .aggregate(total=Sum("qty"))["total"]
    )
    return _ZERO if total is None else _dec(total)


def stock_projection(item, days=56, *, start=None):
    """その部品の在庫の見込み (日ごと)。生産計画の盤の小窓 (計画の詳細) が図にする。読むだけ。

    start (既定は今日) から days 日の、その日の終わりの見込みの在庫数を返す。
    - 元の数 = 今の在庫数 (balance)。在庫を数えていない部品 (None) は 0 から数える (counted が偽)。
    - 足す = その部品の発行の指示書 (受注が取消でない物) の、まだ入庫にしていない数
      (数量 − その指示書の製造の完了の動きの合計。0 以下は足さない) を、最後の工程 (順が最大) の終了日に。
    - 引く = その部品の受注の明細 (受注が取消でない物) の出荷の残りを、納期に。
    - 日が start より前の物 (遅れている物) は start の日に数える。日が期間の後の物は数えない。
      日の無い物 (最後の工程が未計画・納期が空) は数えず、件数を undated に返す。
    問い合わせは在庫数の 2 回と、指示書と明細の 1 回ずつ。workorders か orders の部品が無ければ、その分を数えない。
    返す物 = {"start", "current" (今の在庫数か None), "counted", "days" (1 日 = {"date", "qty"}),
    "workorders"・"workorder_qty" (足した件数と数), "lines"・"line_qty" (引いた件数と数), "undated"}。
    """
    if start is None:
        start = timezone.localdate()
    current = balance(item)
    changes = {}
    result = {
        "start": start,
        "current": current,
        "counted": current is not None,
        "workorders": 0,
        "workorder_qty": _ZERO,
        "lines": 0,
        "line_qty": _ZERO,
        "undated": 0,
    }

    def offset_of(day):
        """day を期間の何日目 (0 から) にするか。日が無ければ None、期間の後なら -1。"""
        if day is None:
            return None
        offset = max((day - start).days, 0)
        return offset if offset < days else -1

    _projection_add_workorders(item, offset_of, changes, result)
    _projection_take_lines(item, offset_of, changes, result)
    result["days"] = _projection_days(start, days, current, changes)
    return result


def _projection_add_workorders(item, offset_of, changes, result):
    """在庫の見込みの「足す」(stock_projection の段)。発行の指示書のまだ入庫にしていない数を、最後の工程の終了日の changes に足す。

    offset_of は日を期間の何日目 (0 から) にする関数。足した件数と数と、日の無い件数を result に数える。workorders の部品が無ければ何もしない。
    """
    try:
        workorder_model = apps.get_model("workorders", "WorkOrder")
        step_model = apps.get_model("workorders", "WorkOrderStep")
    except LookupError:
        return
    last_end = step_model._default_manager.filter(workorder=OuterRef("pk")).order_by("-seq")
    finished = (
        StockMove.objects.filter(
            kind=KIND_FINISHED,
            content_type__app_label="workorders",
            content_type__model="workorder",
            object_id=OuterRef("pk"),
        )
        .order_by()
        .values("object_id")
        .annotate(total=Sum("qty"))
        .values("total")[:1]
    )
    rows = (
        workorder_model._default_manager.filter(
            order_line__item=_pk(item), status=workorder_model.STATUS_ISSUED
        )
        .exclude(order_line__order__status="cancelled")
        .annotate(
            finish_on=Subquery(last_end.values("planned_end")[:1]),
            stocked=Coalesce(Subquery(finished, output_field=_QTY_FIELD), Value(_ZERO), output_field=_QTY_FIELD),
        )
        .order_by()
        .values_list("qty", "stocked", "finish_on")
    )
    for qty, stocked, finish_on in rows:
        rest = _dec(qty) - _dec(stocked)
        if rest <= 0:
            continue
        offset = offset_of(finish_on)
        if offset is None:
            result["undated"] += 1
        elif offset >= 0:
            changes[offset] = changes.get(offset, _ZERO) + rest
            result["workorders"] += 1
            result["workorder_qty"] += rest


def _projection_take_lines(item, offset_of, changes, result):
    """在庫の見込みの「引く」(stock_projection の段)。受注の明細の出荷の残りを、納期の changes から引く。

    offset_of は日を期間の何日目 (0 から) にする関数。引いた件数と数と、日の無い件数を result に数える。orders の部品が無ければ何もしない。
    """
    try:
        line_model = apps.get_model("orders", "OrderLine")
    except LookupError:
        return
    from seizou.orders.services import annotate_remaining

    rows = (
        annotate_remaining(line_model._default_manager.filter(item=_pk(item)).exclude(order__status="cancelled"))
        .filter(remaining_total__gt=0)
        .order_by()
        .values_list("remaining_total", "due_date")
    )
    for remaining, due_date in rows:
        offset = offset_of(due_date)
        if offset is None:
            result["undated"] += 1
        elif offset >= 0:
            changes[offset] = changes.get(offset, _ZERO) - _dec(remaining)
            result["lines"] += 1
            result["line_qty"] += _dec(remaining)


def _projection_days(start, days, current, changes):
    """在庫の見込みの日ごとの数 (stock_projection の段)。元の数 (current。None は 0) に changes を日の順に足した、その日の終わりの数の並び。"""
    running = _ZERO if current is None else current
    series = []
    for offset in range(days):
        running += changes.get(offset, _ZERO)
        series.append({"date": start + timedelta(days=offset), "qty": running})
    return series


# --- 書く (在庫の動き) ---


def record_finished(workorder, qty, user, *, moved_on=None, note=""):
    """製造の完了を入庫にする (指示書を選んで手で押す)。完成の数より多くても止めない (現物優先)。

    取消の指示書と 0 以下の数量は ValueError。moved_on の既定は今日。
    """
    qty = _dec(qty)
    if moved_on is None:
        moved_on = timezone.localdate()
    with transaction.atomic():
        current = type(workorder)._default_manager.select_related("order_line__item").get(pk=workorder.pk)
        if current.status == type(current).STATUS_CANCELLED:
            raise ValueError("保存できません。取り消した指示書です。発行の指示書を選んでください。")
        if not qty > 0:
            raise ValueError("保存できません。" + _POSITIVE_MESSAGE)
        return _move(current.order_line.item, KIND_FINISHED, qty, moved_on, user, source=current, note=note)


def record_received(receipt, user):
    """受入を入庫にする。purchasing の receive が transaction.atomic の中で呼ぶ (日付と備考は受入の物)。"""
    with transaction.atomic():
        return _move(
            receipt.line.item,
            KIND_RECEIVED,
            receipt.qty,
            receipt.received_on,
            user,
            source=receipt,
            note=receipt.note,
        )


def record_issued(item, qty, moved_on, user, *, note=""):
    """払い出し (検査や社内の使用で在庫から出した数)。0 以下の数量は ValueError。在庫より多くても止めない。"""
    qty = _dec(qty)
    if not qty > 0:
        raise ValueError("保存できません。" + _POSITIVE_MESSAGE)
    with transaction.atomic():
        return _move(item, KIND_ISSUED, qty, moved_on, user, note=note)


def adjust(item, counted_qty, moved_on, user, *, note=""):
    """棚卸しの調整。実地の在庫数 (counted_qty) と今の在庫数の差を動きとして残す (符号はそのまま)。

    入れた直後の在庫数は、いつも実地の数になる。
    動きの無い部品 (今の在庫数 None) は、この 1 件で在庫を数え始める (moved_on が数え始めの日)。差は
    実地の数 + その部品の出荷日が moved_on と同じ日か後の出荷の合計 (入れた時に既にある物)。この行が最初の行になり、
    数え始めの日より前の出荷は在庫数から引かないので、在庫数 = 差 − その日と後の出荷 = 実地の数。
    その日と後の出荷が無ければ、実地の数がそのまま入る。
    動きのある部品は、差 = 実地の数 − 今の在庫数 (数え始めの日は変わらない)。
    動きの無い部品に実地 0 を入れたときは断らず、0 から数え始める (その日と後の出荷が無ければ数量 0 の行)。
    返す行の saved_message に、前と後の在庫数の字を入れる (入力の土台が「保存しました。」の代わりに出す。表に書かない)。
    動きのある部品は「在庫数を 190 から 185 にしました (差 -5)。」(正の差は「+」を付ける)。数え始めは
    「5 から数え始めました。」で、数は実地の数 (差はその日と後の出荷を足した数なので、実地の数と違うことがある)。
    実地 0 で数え始めたときは「0 から数え始めました。」。数の字は _qty_text で末尾の 0 を落とす。
    作る行の「調整の前の在庫数」(before_qty) に今の在庫数 (数え始めは空)、「実地の在庫数」(counted_qty) に実地の数を
    控えとして入れる (作者の決め)。在庫数の計算は 2 つの欄を読まず、今までどおり数量の合計から出す。
    実地の数が負と、動きのある部品で差が 0 (今の在庫数と同じ) は ValueError。
    """
    counted = _dec(counted_qty)
    if counted < 0:
        raise ValueError("保存できません。実地の在庫数は 0 以上の数にしてください。")
    with transaction.atomic():
        locked = _lock(item)
        current = balance(locked)
        if current is None:
            diff = counted + _shipped_on_or_after(locked, moved_on)
        else:
            diff = counted - current
        if diff == 0 and current is not None:
            raise ValueError("保存できません。今の在庫数と同じです。調整は要りません。")
        move = _move(
            locked, KIND_ADJUSTED, diff, moved_on, user, note=note, before_qty=current, counted_qty=counted
        )
        if current is not None:
            signed = ("+" if diff > 0 else "") + _qty_text(diff)
            move.saved_message = f"在庫数を {_qty_text(current)} から {_qty_text(counted)} にしました (差 {signed})。"
        elif counted == 0:
            move.saved_message = COUNTED_FROM_ZERO_MESSAGE
        else:
            move.saved_message = f"{_qty_text(counted)} から数え始めました。"
        return move
