"""発注の詳細の「流れ」の枠のテンプレートの tag (開発の記録 docs/nights/20261104/N139.md)。読むだけで、表に書かない。

- purchase_order_timeline: 発注の流れの出来事の並び (purchasing.timeline.events_for)。土台の詳細の画面は文脈を足す口を持たない (R15) ため、
  枠のテンプレートが {% purchase_order_timeline object as events %} の形で読む (受注の {% order_timeline object as events %} と同じ形)。
- 段の帯と流れの枠は、同じ画面で同じ発注を受ける。明細・出荷の知らせ・受入は 1 回だけ読み (timeline.lines_for)、2 つの tag で使う (_lines)。
"""

from django import template

from seizou.purchasing import timeline

register = template.Library()


def _lines(purchase_order):
    """段の帯と流れの枠が使う明細の並び (timeline.lines_for)。同じ発注の instance には 1 回だけ読む。

    控えは instance の属性に置く。詳細の画面は要求ごとに発注を読み直すので、次の要求には持ち越さない
    (発注の詳細は 2 秒ごとに取り直す。1 回の描画の中で、同じ明細・出荷の知らせ・受入を 2 度読まないための控え)。
    """
    lines = getattr(purchase_order, "_timeline_lines", None)
    if lines is None:
        lines = timeline.lines_for(purchase_order)
        purchase_order._timeline_lines = lines
    return lines


@register.simple_tag
def purchase_order_timeline(purchase_order):
    return timeline.events_for(purchase_order, _lines(purchase_order))


@register.simple_tag
def purchase_order_stages(purchase_order):
    """発注の段の帯 (作者の決めの発注。開発の記録 docs/nights/20261105/N151.md)。取消の発注は None (帯を出さない)。"""
    return timeline.stage_band(purchase_order, _lines(purchase_order))
