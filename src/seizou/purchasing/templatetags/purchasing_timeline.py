"""発注の詳細の「流れ」の枠のテンプレートの tag (N139)。読むだけで、表に書かない。

- purchase_order_timeline: 発注の流れの出来事の並び (purchasing.timeline.events_for)。土台の詳細の画面は文脈を足す口を持たない (R15) ため、
  枠のテンプレートが {% purchase_order_timeline object as events %} の形で読む (受注の {% order_timeline object as events %} と同じ形)。
"""

from django import template

from seizou.purchasing import timeline

register = template.Library()


@register.simple_tag
def purchase_order_timeline(purchase_order):
    return timeline.events_for(purchase_order)
