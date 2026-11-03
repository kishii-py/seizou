"""connect のテンプレートタグ。ほかの部品の画面に、届いた注文の入口を出すために使う (読むだけ)。

受注の画面 (orders/order_list.html) は connect の無い会社でも開くので、このタグを読むのは
connect/_inbox_entrance.html の中だけ (order_list.html には {% load %} を書かない)。
"""

from django import template
from django.db import DatabaseError

from ..models import Inbox

register = template.Library()


@register.simple_tag
def pending_order_count():
    """未承認 (状態が「届いた」) の届いた注文の件数。ダッシュボードの「届いた注文 (未処理)」と同じ数え方。

    表がまだ無い (migrate の前) などで読めなければ 0。
    """
    try:
        return Inbox.objects.filter(status=Inbox.STATUS_RECEIVED).count()
    except DatabaseError:
        return 0
