"""purchasing のテンプレートの tag (受入の支払の見込み)。読むだけで、表に書かない。

- payable_table: 受入の一覧の上の表 (取引先ごとの先月と今月の支払の見込み)。金額を見られない人には hidden が真。
金額の閲覧の判定は core の access.can_see_money (関数の中で import する)。
ダッシュボードのカード「今月の支払の見込み」は core の dashboard_stats() が数字のカードの並びに出す (11-03 の本体の直し)。
"""

from django import template

register = template.Library()


def _user(context):
    request = context.get("request")
    return getattr(request, "user", None)


@register.simple_tag(takes_context=True)
def payable_table(context):
    """{% payable_table as payable %}。purchasing.services.payable_by_partner() の辞書に hidden (金額を隠すか) を足した物。"""
    from seizou.core import access

    from .. import services

    return {**services.payable_by_partner(), "hidden": not access.can_see_money(_user(context))}

