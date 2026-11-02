"""QR コードの template tag。中身は seizou.core.qr.qr_svg が作る inline の SVG。

使い方: {% load seizou_qr %}{% qr_svg qr_text %}
表を読まない。表に書かない。
"""

from django import template
from django.utils.safestring import mark_safe

from seizou.core import qr

register = template.Library()


@register.simple_tag
def qr_svg(text):
    return mark_safe(qr.qr_svg(str(text)))  # noqa: S308 - qrcode が作った SVG の字だけ。中身の字は SVG に入らない
