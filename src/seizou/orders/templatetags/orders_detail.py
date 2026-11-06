"""受注の詳細の右の列 (案 3-D・8-B。N150) のテンプレートの tag。読むだけで、表に書かない。

- order_aside: 右の列のカードの並び (得意先・日付・金額・基本情報)。土台の詳細の画面は文脈を足す口を持たない (R15) ため、
  受注の詳細の block content が {% order_aside object fields seizou.hide_money as aside %} の形で置いてから block.super を呼び、
  土台の core/detail.html が aside を読んで 2 列にする (流れの枠の {% order_timeline object as events %} と同じ tag の形)。
  aside = {"cards": [カード]}。カード = {"title": 題, "rows": [行]} と、基本情報のカードだけ "help" (枠の「?」の part) と
  "help_label" (「?」の名前)。行は土台の詳細の項目と同じ形 {"label", "value", "master", "file"}。
- 金額の閲覧は seizou.hide_money を読むだけ (access.can_see_money を呼ばない = 金額の閲覧の記録を増やさない)。
- order_head_chips: 受注の詳細の頭の要点の札 (N163)。受注の詳細の block heading が {% order_head_chips object seizou.hide_money as head_chips %}
  の形で置いてから block.super を呼び、土台の core/detail.html が head_chips を読んで題の横に 3 つまで並べる。
- order_line_allocations: 明細の表の行と、行ごとの引当の合計の組 (「預かり」の列。決め 11-06 18:3x。N184)。
"""

from decimal import Decimal

from django import template
from django.apps import apps

from seizou.core.templatetags.seizou_tags import comma

from ..models import OrderLine

register = template.Library()

_ZERO = Decimal("0")


def _row(label, value=None, master=None) -> dict:
    """カードの 1 行 (土台の詳細の項目と同じ形)。master があれば小窓を開く字になる。"""
    return {"label": label, "value": value, "master": master, "file": None}


def _label(order, name) -> str:
    """項目の名 (基本情報の字と同じ verbose_name)。"""
    return str(order._meta.get_field(name).verbose_name)


@register.simple_tag
def order_aside(order, fields, hide_money=False) -> dict:
    """受注の詳細の右の列。

    得意先 (名前は小窓を開く字・取引先コード)・日付 (受注日・明細の一番早い納期)・金額 (明細の金額の合計。明細があり、
    金額を見られる人 = hide_money が偽のときだけ)・基本情報 (土台の項目 fields から得意先と受注日を除いた物)。
    """
    customer = order.customer
    lines = list(order.lines.all())
    dues = sorted(line.due_date for line in lines if line.due_date)
    moved = (_label(order, "customer"), _label(order, "order_date"))
    cards = [
        {"title": _label(order, "customer"), "rows": [_row("名前", master=customer), _row("取引先コード", customer.code)]},
        {
            "title": "日付",
            "rows": [_row(_label(order, "order_date"), order.order_date), _row("一番早い納期", dues[0] if dues else None)],
        },
    ]
    if lines and not hide_money:
        total = sum((line.amount for line in lines), Decimal("0"))
        cards.append({"title": "金額", "rows": [_row("明細の合計", total)]})
    cards.append(
        {
            "title": "基本情報",
            "rows": [field for field in fields if field["label"] not in moved],
            "help": "basic",
            "help_label": "基本情報の説明",
        }
    )
    return {"cards": cards}


@register.simple_tag
def order_head_chips(order, hide_money=False) -> list[dict]:
    """受注の詳細の頭の要点の札 (N163)。札 = {"label", "value"} (値は字)。土台の core/detail.html が head_chips を読んで題の横に並べる。

    得意先 (名前)・一番早い納期 (明細の納期の一番早い日。無ければ札を出さない)・合計 (明細の金額の合計と「円」。明細があり、
    金額を見られる人 = hide_money が偽のときだけ)。右の列のカード (order_aside) は残す (札はその要約)。
    """
    lines = list(order.lines.all())
    dues = sorted(line.due_date for line in lines if line.due_date)
    chips = [{"label": _label(order, "customer"), "value": order.customer.name}]
    if dues:
        chips.append({"label": "一番早い納期", "value": f"{dues[0]:%Y-%m-%d}"})
    if lines and not hide_money:
        chips.append({"label": "合計", "value": f"{comma(sum((line.amount for line in lines), Decimal('0')))} 円"})
    return chips


def _allocation_model():
    """引当の表 (Allocation)。receiving の部品が無ければ None (預かりの値は 0。orders/board.py の _shipment_model の形)。"""
    try:
        return apps.get_model("receiving", "Allocation")
    except LookupError:
        return None


@register.simple_tag
def order_line_allocations(order) -> list[tuple]:
    """受注の詳細の明細の行と、行ごとの引当の合計の組の並び [(明細, 引当の合計)] (「預かり」の列。決め 11-06 18:3x。N184)。

    明細は order.lines.all() (画面の prefetch の写し) の順。引当の合計は receiving.services.annotate_allocated を
    明細の queryset に 1 回当てて読む (明細の数だけ SQL を出さない)。receiving の部品が無ければ全部 0 で、問い合わせをしない。
    読むだけで、表に書かない。
    """
    lines = list(order.lines.all())
    if not lines or _allocation_model() is None:
        return [(line, _ZERO) for line in lines]
    from seizou.receiving.services import annotate_allocated

    totals = dict(
        annotate_allocated(OrderLine.objects.filter(order=order).order_by()).values_list("pk", "allocated_total")
    )
    return [(line, totals.get(line.pk) or _ZERO) for line in lines]
