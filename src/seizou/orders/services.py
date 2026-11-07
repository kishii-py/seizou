"""orders の公開する関数 (01 の 10.4)。画面とほかの部品は、この関数だけを通して受注を書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は記録のために受ける。記録の列がまだ無いので、今は受けるだけで書かない。
- ext (会社ごとの項目) は受けるが、None 以外が来たら NotImplementedError を投げる。
  黙って捨てると会社の項目が消えるため。保存は core の fields ができてから足す。
- 中心の決まりに当たったら RuleViolation(code, message)。message は RULES.md の番号の文と、次にすること。
- 決まりの番号の無い入力の誤り (取引先の種類が得意先でない・明細が無い・知らない鍵) は日本語の ValueError。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- ポカよけは検査の層 (seizou.core.checks) が services の前に見る。ここでは中心の決まりだけを見る。
- 明細の辞書の鍵は item・qty・unit・unit_price・price_basis・due_date・note・customer_part_no・quote_no・process_name・ext。
  price_basis は単価の単位 (unit か lump。無ければ unit = 今と同じ)。
  quote_no は単価表から写した見積の番号、process_name は受信した注文の明細の工程名 (どちらも無ければ空の字。C100)。
  update_order では、既にある明細を指す id か order_line (この受注の OrderLine) も受ける (同じ意味)。
- 受注の状態 (受注・一部出荷・出荷済) は refresh_status が出荷済みの数から決め直す。
  shipping.services.ship と update_order の終わりに呼ぶ。止めない (現物優先)。
- 請求しない明細の印は mark_no_bill が付ける。外すのは clear_no_bill。この 2 つのほかは印を書かない。
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import FieldDoesNotExist
from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, ProtectedError, Sum, Value
from django.db.models import Count, Max, Min
from django.db.models.functions import Coalesce

from seizou.core.exceptions import RuleViolation
from seizou.core.services import next_number
from seizou.masters.services import PRICE_BASIS_UNIT, line_amount, refuse_inactive, refuse_unknown_price_basis

from .models import Order, OrderLine

_ORDER_KEYS = frozenset({"order_date", "customer_order_no", "note", "customer"})
_LINE_KEYS = frozenset(
    {"item", "qty", "unit", "unit_price", "price_basis", "due_date", "note", "customer_part_no", "ext", "quote_no", "process_name"}
)
_LINE_KEYS_ON_UPDATE = _LINE_KEYS | {"id", "order_line", "line_no"}
_LINE_FIELDS = ("item", "customer_part_no", "unit", "due_date", "note", "quote_no", "process_name")
_SOURCES = frozenset(value for value, _label in Order.SOURCE_CHOICES)

_R02_MESSAGE = "保存できません。数量と単価は 0 より大きい。0 より大きい数を入れてください。"
_SHIPPED_LINE_MESSAGE = "保存できません。消した明細に出荷があります。数量を直すか、明細を残してください。"

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


def _add_line(order, line, line_no):
    item = line["item"]
    qty = _dec(line["qty"])
    unit_price = _dec(line["unit_price"])
    price_basis = line.get("price_basis", PRICE_BASIS_UNIT)
    _check_positive(qty, unit_price)
    return OrderLine.objects.create(
        order=order,
        line_no=line_no,
        item=item,
        customer_part_no=line.get("customer_part_no", ""),
        qty=qty,
        unit=line["unit"] if "unit" in line else item.unit,
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
    for key in _LINE_FIELDS:
        if key in line:
            setattr(row, key, line[key])
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
        for line_no, line in enumerate(lines, start=1):
            _add_line(order, line, line_no)
        if source != Order.SOURCE_MANUAL:
            order.saved_note = f"明細 {len(lines)} 行を手入力せずに受注にしました。"
        return order


def update_order(order, data, lines, user, *, ext=None):
    """受注の項目と明細を直す。

    data は order_date・customer_order_no・note・customer の鍵だけ。data にある鍵だけを書き換える。
    lines は直した後の明細の全部。id か order_line のある明細はその明細を直し (鍵のある列だけ)、
    どちらも無い明細は足す (行番号は今の最大 + 1 から。足す明細に line_no があればその番号で足し、次の番号はその番号の次より小さくしない)。
    lines に無い既存の明細は消す。
    出荷のある明細は消せない (DB の PROTECT が止める物を、日本語の ValueError にして返す)。
    残る明細は全部の金額を計算し直し、終わりに refresh_status で受注の状態を決め直す。
    訳: 取消した受注を直させないため (直すときは新しく受注を作る)。出所 = RULES.md R05。
    変えてよい条件 = 変えない (R は廃止で残す)。
    訳: 受信した注文の変更で足した明細の行番号を、相手 (発注元) の行番号とそろえるため (出荷の知らせと検収は行番号で明細を指す)。出所 = 作者の決め (11-07。発注の変更の受け入れ)。変えてよい条件 = 送り合いが行番号でなく明細の識別で明細を指すようになったとき。
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

        for key, value in data.items():
            setattr(locked, key, value)
        locked.save()

        try:
            locked.lines.exclude(pk__in=keep).delete()
        except ProtectedError as exc:
            # 出荷 (shipping の PROTECT) が指す明細は DB が消させない。消す前に止まるので、表はそのまま。
            raise ValueError(_SHIPPED_LINE_MESSAGE) from exc
        for line, ref in zip(lines, refs, strict=True):
            if ref is not _NEW_LINE:
                _change_line(existing[ref], line, locked.customer)
            else:
                line_no = line.get("line_no") or next_line_no
                _add_line(locked, line, line_no)
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
            locked.no_bill = True
            locked.save(update_fields=["no_bill"])
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
            locked.no_bill = False
            locked.save(update_fields=["no_bill"])
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
            locked.status = status
            locked.save(update_fields=["status"])
        return locked
