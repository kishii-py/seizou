"""受注の詳細の「流れ」の枠のテンプレートの tag (N131)。読むだけで、表に書かない。

- order_timeline: 受注の流れの出来事の並び (orders.timeline.events_for)。土台の詳細の画面は文脈を足す口を持たない (R15) ため、
  枠のテンプレートが {% order_timeline object as events %} の形で読む (notes の {% note_for object as note %} と同じ形)。
- order_cancel_linked: 受注の取消の確かめのカードの「この受注に付いている物」(orders.timeline.linked_for。初見テスト R5 の候補 C59)。
"""

from django import template

from seizou.orders import timeline

register = template.Library()


@register.simple_tag
def order_timeline(order):
    return timeline.events_for(order)


@register.simple_tag
def order_stages(order):
    """受注の段の帯 (案 4-A。N148)。取消の受注は None (帯を出さない)。"""
    return timeline.stage_band(order)


@register.simple_tag(takes_context=True)
def order_cancel_linked(context, order):
    """{% order_cancel_linked object as linked %}。受注の取消の確かめに並べる、この受注から作った物の群 (読むだけ)。

    user は context の request から取る (core の tag can_write と同じ取り方)。部署で開けない部品の番号は link にしない。
    """
    request = context.get("request")
    return timeline.linked_for(order, getattr(request, "user", None))
