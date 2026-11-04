"""受注の詳細の「流れ」の枠のテンプレートの tag (N131)。読むだけで、表に書かない。

- order_timeline: 受注の流れの出来事の並び (orders.timeline.events_for)。土台の詳細の画面は文脈を足す口を持たない (R15) ため、
  枠のテンプレートが {% order_timeline object as events %} の形で読む (notes の {% note_for object as note %} と同じ形)。
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
