"""billing のテンプレートの tag。読むだけで、表に書かない。

- invoice_cancel_refusal: 請求書を取り消せない理由の文 (services.cancel_refusal と同じ文。取消の確かめの画面が黄の知らせで出す)。
"""

from django import template

register = template.Library()


@register.simple_tag
def invoice_cancel_refusal(invoice, hide_money=False):
    """{% invoice_cancel_refusal object seizou.hide_money as refusal %}。取り消せない理由の文 (取り消せるなら空の字)。

    hide_money は seizou.hide_money (金額の閲覧の無い人で真。log の会社では読んだ時に記録に 1 行)。
    """
    from .. import services

    return services.cancel_refusal(invoice, hide_money=bool(hide_money))
