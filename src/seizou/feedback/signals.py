"""feedback の signal の受け手。apps.py の ready() が読み込む。表に書くのは services の関数だけ (R13)。

書いた本人がログインしたとき (django.contrib.auth.login が user_logged_in を送る)、まだ知らせていない「直した」を
右下の toast (messages の info) で 1 回だけ知らせ、知らせた日時を services で書く。
- 知らせを出せない要求 (MessageMiddleware を通らない要求。試験の force_login など) では、知らせた印を付けない
  (次に画面からログインしたときに出す)。
- 開発機の自動ログイン (core の DevAutoLoginMiddleware) は login を呼ばないので、この知らせは出ない
  (自動ログインの人は superuser = 世話役。書いた人として確かめるときは、ログインの画面からその人でログインする)。
"""

from django.contrib import messages
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from . import services


@receiver(user_logged_in, dispatch_uid="seizou.feedback.notify_fixed")
def notify_fixed(sender, request=None, user=None, **kwargs):
    """ログインした人が書いたフィードバックに、まだ知らせていない「直した」があれば 1 回知らせる。"""
    if request is None:
        return
    rows = services.unseen_fixed(user)
    if not rows:
        return
    try:
        messages.info(request, services.fixed_notice(rows))
    except messages.MessageFailure:
        return
    services.mark_fixed_seen(rows, user)
