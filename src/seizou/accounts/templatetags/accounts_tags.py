"""accounts のテンプレートの tag。読むだけで、表に書かない。

- department_delete_refusal: 部署を消せない理由の文 (services.delete_refusal と同じ文。削除の確かめの画面が黄の知らせで出す)。
"""

from django import template

register = template.Library()


@register.simple_tag
def department_delete_refusal(department):
    """{% department_delete_refusal object as refusal %}。部署を消せない理由の文 (消せるなら空の字)。

    view の context で渡さないのは、画面の class が上書きしてよい物 (R15) に get_context_data が無いため (請求書の取消と同じ形)。
    """
    from .. import services

    return services.delete_refusal(department)
