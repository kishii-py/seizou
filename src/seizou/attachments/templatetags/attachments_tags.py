"""添付ファイルの枠 (attachments/_panel.html) が使う tag。読むだけ。

{% attachments_for object as rows %}   記録の添付ファイルの並び (新しい順)
{% attachments_for object request.user as rows %}   そのうち利用者が開ける物だけ (services.openable_by。詳細の枠が使う)
{% content_type_id object as ct %}     記録の種類の番号 (「添付ファイルを付ける」の link に渡す)
{% target_text_for form request.user as text %}  付ける入力の頭の「付ける先」の字 (開けない記録は空)
"""

from django import template
from django.contrib.contenttypes.models import ContentType

from seizou.attachments import services

register = template.Library()


@register.simple_tag
def attachments_for(obj, user=None):
    """記録の添付ファイルの並び。user を渡したときは、その人が開ける物だけ (金額が主な記録の添付を金額を見られない人に出さない。N367)。

    添付が 1 つも無い記録では、絞り (services.openable_by) を呼ばない。外す行が無いので答えは同じで、全部の詳細の画面に
    絞りの問い合わせ (付けた先の種類を引く 2 回) を足さないため (tests/test_core_screen_queries.py の上限)。
    """
    rows = services.attachments_for(obj)
    listed = list(rows)
    if user is None or not listed:
        return listed
    return list(services.openable_by(rows, user))


@register.simple_tag
def content_type_id(obj):
    return ContentType.objects.get_for_model(obj).pk


@register.simple_tag
def target_text_for(form, user):
    """付ける入力の頭の「付ける先」の字 (AttachmentForm.target_text_for。form は利用者を持たないので、ここで渡す)。"""
    return form.target_text_for(user)
