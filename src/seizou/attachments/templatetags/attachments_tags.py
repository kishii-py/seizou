"""添付ファイルの枠 (attachments/_panel.html) が使う tag。読むだけ。

{% attachments_for object as rows %}   記録の添付ファイルの並び (新しい順)
{% content_type_id object as ct %}     記録の種類の番号 (「添付ファイルを付ける」の link に渡す)
"""

from django import template
from django.contrib.contenttypes.models import ContentType

from seizou.attachments import services

register = template.Library()


@register.simple_tag
def attachments_for(obj):
    return list(services.attachments_for(obj))


@register.simple_tag
def content_type_id(obj):
    return ContentType.objects.get_for_model(obj).pk
