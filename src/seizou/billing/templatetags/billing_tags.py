"""billing のテンプレートの tag。読むだけで、表に書かない。

- invoice_cancel_refusal: 請求書を取り消せない理由の文 (services.cancel_refusal と同じ文。取消の確かめの画面が黄の知らせで出す)。
- payment_badge: 請求書の入金の状態の印 (core/_status_badge.html。色は core.status_colors の PAYMENT_TONES。N164)。
"""

from django import template

from seizou.core.status_colors import PAYMENT_TONES

register = template.Library()


@register.simple_tag
def invoice_cancel_refusal(invoice, hide_money=False):
    """{% invoice_cancel_refusal object seizou.hide_money as refusal %}。取り消せない理由の文 (取り消せるなら空の字)。

    hide_money は seizou.hide_money (金額の閲覧の無い人で真。log の会社では読んだ時に記録に 1 行)。
    """
    from .. import services

    return services.cancel_refusal(invoice, hide_money=bool(hide_money))


@register.inclusion_tag("core/_status_badge.html")
def payment_badge(invoice, dot=False, small=False):
    """{% payment_badge invoice %}。請求書の入金の状態の印 (決め 11-05 16:2x。N164)。発行の請求書だけに使う (取消は呼ぶ側が status_badge)。

    字は入金の状態の字 (未入金・一部入金・入金済み)、色は core.status_colors の PAYMENT_TONES (未入金と一部入金 = 橙、入金済み = 緑)。
    入金の状態は Invoice.payment_status (取消でない入金の合計。一覧の「残のある物だけ」= views._unpaid_only と同じ数え方)。
    """
    state = invoice.payment_status()
    return {"tone": PAYMENT_TONES[state], "label": invoice.PAYMENT_LABELS[state], "dot": dot, "small": small}
