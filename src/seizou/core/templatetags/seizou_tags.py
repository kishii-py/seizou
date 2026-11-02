"""共通の template tag。

master_link: 品目か取引先を渡すと名前を出し、押すと小窓を開く link になる (01 の 10.3)。
この版は名前を出すだけ。小窓を開く hx-get は小窓の土台と一緒に後の commit で足す。
"""

from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def master_link(obj):
    if obj is None:
        return ""
    return format_html('<span class="master-link">{}</span>', str(obj))
