"""purchasing のテンプレートの tag (受入の支払の見込み)。読むだけで、表に書かない。

- payable_table: 受入の一覧の上の表 (取引先ごとの先月と今月の支払の見込み)。金額を見られない人には hidden が真。
金額の閲覧の判定は core の access.can_see_money (関数の中で import する)。
ダッシュボードのカード「今月の支払の見込み」は core の dashboard_stats() が数字のカードの並びに出す (11-03 の本体の直し)。
- live_state: 発注の一覧と詳細の 2 秒ごとの更新で、行の状態の字 (data-live) と、前の更新と字が違うか (seizou-changed を付けるか)。
"""

from decimal import Decimal

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


# 2 秒ごとの更新の要求の見出し (前の更新で見せた行の状態の字を、空白で区切って並べた物)
_LIVE_HEADER = "X-Seizou-Live"


def _num_text(value) -> str:
    """数の字。末尾の 0 を落とす (Decimal("200.00") と Decimal("200") を同じ "200" に、"0.00" を "0" に)。無ければ "0"。"""
    if value is None:
        return "0"
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(dec.normalize(), "f")


def _live_seen(request) -> dict:
    """見出しの字を {鍵: 値} にする。1 つの要求で 1 回だけ作り、request に持たせる (行ごとに作り直さない)。"""
    seen = getattr(request, "_seizou_live_seen", None)
    if seen is None:
        seen = {}
        for part in request.headers.get(_LIVE_HEADER, "").split():
            key, sep, value = part.partition(":")
            if sep and key:
                seen[key] = value
        request._seizou_live_seen = seen
    return seen


@register.simple_tag(takes_context=True)
def live_state(context, obj):
    """{% live_state obj as live %}。live.token = 状態の字 (data-live)、live.changed = 前の更新と字が違うか。

    字は「鍵:値」。発注 = po-<pk>:<状態の値>:<出荷の知らせの数>:<受入の数>、発注の明細 = line-<pk>:<出荷の知らせの数>:<受入の数>。
    前の更新の字は、2 秒ごとの更新の要求の見出し X-Seizou-Live (空白で区切った字の並び) から読む。見出しが無い
    (画面を開いた時) か、同じ鍵が無い行は changed が偽。読むだけで、問い合わせをしない (数は queryset の annotate と
    明細の shipping_notice の要約を読む)。
    """
    from ..models import PurchaseOrder, PurchaseOrderLine

    if isinstance(obj, PurchaseOrder):
        key = f"po-{obj.pk}"
        value = (
            f"{obj.status}:{_num_text(getattr(obj, 'notice_count', 0))}:{_num_text(getattr(obj, 'received_total', 0))}"
        )
    elif isinstance(obj, PurchaseOrderLine):
        key = f"line-{obj.pk}"
        value = f"{_num_text(obj.shipping_notice['count'])}:{_num_text(getattr(obj, 'received_total', 0))}"
    else:
        raise ValueError(f"live_state は発注か発注の明細だけを受けます ({type(obj).__name__} を受けた)。")
    request = context.get("request")
    seen = _live_seen(request) if request is not None else {}
    changed = key in seen and seen[key] != value
    return {"token": f"{key}:{value}", "changed": changed}
