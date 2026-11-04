"""メモの枠 (notes/_note.html) と記入の形 (notes/note_form.html) が使う tag。読むだけ。

{% note_for object as note %}            記録のメモ (無ければ保存していない空のメモ)
{% note_edit_url object as url %}        「書く」の URL (付ける先の種類と番号を GET で渡す)
{% note_target_url target as url %}      付けた先の記録の詳細の URL (「やめる」の戻り先。無ければ空の字)
{{ html|note_html }}                     掃除した本文 (services.clean_html を描く前にもう 1 回通す)
"""

from django import template
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.safestring import mark_safe

from seizou.core.views import model_url
from seizou.notes import services

register = template.Library()


@register.simple_tag
def note_for(obj):
    return services.note_for(obj)


@register.simple_tag
def note_edit_url(obj):
    query = urlencode({"content_type": ContentType.objects.get_for_model(obj).pk, "object_id": obj.pk})
    return f"{reverse('notes:note_edit')}?{query}"


@register.simple_tag
def note_target_url(obj):
    if obj is None:
        return ""
    return model_url(obj.__class__, "detail", obj.pk) or ""


@register.filter
def note_html(value):
    # 掃除した HTML だけを描く (保存の時と同じ services.clean_html をもう 1 回通す)
    return mark_safe(services.clean_html(value))
