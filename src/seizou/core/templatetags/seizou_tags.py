"""共通の template tag。

master_link: 品目か取引先を渡すと名前を出し、押すと小窓で開く link になる (01 の 10.3。決め 11-02 12:5x)。
小窓の URL は <app>:<model>_modal の約束 (例 masters:item_modal)。無ければ名前だけを出す。
"""

from django import template
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html

register = template.Library()


def modal_url(obj) -> str | None:
    meta = getattr(obj, "_meta", None)
    if meta is None or getattr(obj, "pk", None) is None:
        return None
    try:
        return reverse(f"{meta.app_label}:{meta.model_name}_modal", args=[obj.pk])
    except NoReverseMatch:
        return None


@register.simple_tag
def master_link(obj, label=None):
    """obj の名前 (label があればその字) を、小窓を開く link にして出す。"""
    if obj is None:
        return ""
    text = str(obj) if label is None or label == "" else str(label)
    url = modal_url(obj)
    if url is None:
        return format_html('<span class="master-link">{}</span>', text)
    return format_html(
        '<a class="link master-link" href="{0}" hx-get="{0}" hx-target="#seizou-modal-box" hx-swap="innerHTML" '
        'hx-on::after-request="document.getElementById(\'seizou-modal\').showModal()" '
        'onclick="event.preventDefault()">{1}</a>',
        url,
        text,
    )
