"""purchasing のテンプレートの tag (受入の支払の見込み)。読むだけで、表に書かない。

- payable_table: 受入の一覧の上の表 (取引先ごとの先月と今月の支払の見込み)。金額を見られない人には hidden が真。
- payable_card: ダッシュボードの数字のカード 1 枚 (今月の支払の見込み)。受入の表が無ければ None (カードを出さない)。
金額の閲覧の判定は core の access.can_see_money、今月の合計は core の dashboard.payable_this_month (どちらも関数の中で import する)。
"""

from django import template
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

register = template.Library()


def _url_or_none(name):
    try:
        return reverse(name)
    except NoReverseMatch:
        return None


def _user(context):
    request = context.get("request")
    return getattr(request, "user", None)


@register.simple_tag(takes_context=True)
def payable_table(context):
    """{% payable_table as payable %}。purchasing.services.payable_by_partner() の辞書に hidden (金額を隠すか) を足した物。"""
    from seizou.core import access

    from .. import services

    return {**services.payable_by_partner(), "hidden": not access.can_see_money(_user(context))}


@register.simple_tag(takes_context=True)
def payable_card(context):
    """{% payable_card as card %}。{"value", "hidden", "url", "month"}。受入の表が無いか DB が読めなければ None。"""
    from seizou.core import access, dashboard

    value = dashboard.payable_this_month()
    if value is None:
        return None
    return {
        "value": value,
        "hidden": not access.can_see_money(_user(context)),
        "url": _url_or_none("purchasing:receipt_list"),
        "month": timezone.localdate().month,
    }
