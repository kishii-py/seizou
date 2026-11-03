"""共通の template tag。

master_link: 品目か取引先を渡すと名前を出し、押すと小窓で開く link になる (01 の 10.3。決め 11-02 12:5x)。
小窓の URL は <app>:<model>_modal の約束 (例 masters:item_modal)。無ければ名前だけを出す。
comma: 数を 3 桁ごとの区切りつきで出す (Decimal は小数の末尾の 0 を落とす。None は空)。{{ x|comma:0 }} で小数の桁を決める (見るだけの丸め)。
can_manage_accounts: {% can_manage_accounts as is_manager %} で、アカウント管理を使える人か (管理者にだけ出す文を分ける)。
has_required: {% if form|has_required %} で、form の見える欄に必須の欄があるか (入力の土台の案内の文を出すか)。
"""

from decimal import ROUND_HALF_UP, Decimal

from django import template
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html

register = template.Library()


@register.filter
def comma(value, digits=None):
    """数を 3 桁ごとの区切りで出す。1234.50 → 1,234.5、10.00 → 10、1200 → 1,200。数でない物はそのまま。

    Decimal は小数の末尾の 0 を落とす (字の末尾を削る。指数の形にしない)。CSV と保存の値は変えない。

    digits を渡すと小数の桁をその数にそろえる (四捨五入。画面に出すだけで、値は変えない)。
    """
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return value
    if digits not in (None, ""):
        if not isinstance(value, (int, Decimal, float)):
            return value
        places = int(digits)
        rounded = Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        return f"{rounded:,f}"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, Decimal):
        text = f"{value:,f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return "0" if text == "-0" else text
    if isinstance(value, float):
        return f"{value:,f}"
    return value


@register.simple_tag(takes_context=True)
def can_manage_accounts(context):
    """{% can_manage_accounts as is_manager %}。アカウント管理を使える人か (core.access.can_manage_accounts と同じ判定)。"""
    from seizou.core import access

    request = context.get("request")
    return access.can_manage_accounts(getattr(request, "user", None))


@register.filter
def has_required(form):
    """{% if form|has_required %}。form の見える欄に必須の欄が 1 つでもあるか。"""
    return any(field.field.required for field in form.visible_fields())


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
