"""connect のテンプレートタグ。ほかの部品の画面に、届いた注文の入口を出すために使う (読むだけ)。

受注の画面 (orders/order_list.html) は connect の無い会社でも開くので、このタグを読むのは
connect/_inbox_entrance.html の中だけ (order_list.html には {% load %} を書かない)。
届いた注文の詳細 (connect/inbox_detail.html) の「差出人を選ぶ」の form と、その form を出すか (承認できる人か) の判定も置く。
届いた注文の一覧の行と詳細の「同じ注文番号の注文が既に届いています」の知らせの中身も置く (本体の追記 11)。
"""

from django import template
from django.db import DatabaseError
from django.urls import reverse

from seizou.core import access

from .. import services
from ..forms import SenderForm
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


@register.simple_tag
def sender_form():
    """届いた注文の詳細の「差出人を選ぶ」の form ({% sender_form as sender %})。欄は得意先か両方の取引先の選択欄 1 つ。"""
    return SenderForm()


@register.simple_tag(takes_context=True)
def can_accept_inbox(context):
    """今の利用者が届いた注文を承認できるか ({% can_accept_inbox as can_accept %})。

    差出人を選ぶ form を出すかの判定 (権限の無い人に入口を出さない = 決め 11-04 00:0x)。編集の判定が on のときだけ偽になりうる。
    """
    return access.write_allowed(context["request"].user, access.action_name(services.accept))


@register.simple_tag
def same_order_notice(inbox):
    """同じ取引先から同じ注文番号で前に届いた行 (一番古い 1 つ) の知らせの中身 ({% same_order_notice row.obj as same %})。無ければ None。

    返す物は {"pk": 前の行の番号, "status": 前の行の状態の字, "url": 前の行の詳細の URL}。知らせるだけで、断らない・状態を変えない。
    一覧の行は get_queryset の注釈 (same_order_pk・same_order_status) を読み、問い合わせを足さない。詳細は注釈が無いので 1 回だけ引く。
    取引先の空の行 (差出人の確認待ち) と、注文番号の空の行は比べない。
    """
    if hasattr(inbox, "same_order_pk"):
        pk, status = inbox.same_order_pk, inbox.same_order_status
    else:
        order_no = inbox.payload.get("order_no") if isinstance(inbox.payload, dict) else None
        if inbox.partner_id is None or not isinstance(order_no, str) or not order_no:
            return None
        earlier = (
            Inbox.objects.filter(partner_id=inbox.partner_id, pk__lt=inbox.pk, payload__order_no=order_no)
            .order_by("pk")
            .values("pk", "status")
            .first()
        )
        pk, status = (earlier["pk"], earlier["status"]) if earlier else (None, None)
    if pk is None:
        return None
    return {
        "pk": pk,
        "status": dict(Inbox.STATUS_CHOICES).get(status, status),
        "url": reverse("connect:inbox_detail", args=[pk]),
    }
