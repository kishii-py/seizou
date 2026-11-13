"""feedback の signal の受け手。apps.py の ready() が読み込む。表に書くのは services の関数だけ (R13)。

書いた本人がログインしたとき (django.contrib.auth.login が user_logged_in を送る)、まだ知らせていない「直した」と「見送った」を
右上の toast (messages の info) で 1 回ずつ知らせ、知らせた日時を services で書く (両方あれば toast は 2 つ。直したが先)。
- 直したと見送ったは別の受け手 (notify_fixed・notify_dismissed)。直したが無い人にも見送りを知らせるため (開発の記録 docs/nights/20261107/N322.md。決め 11-07 09:0x〜09:4x)。
- Issue の返事 (開発の記録 docs/nights/20261107/N324.md。決め 11-07 09:1x〜09:4x) も別の受け手 (notify_replies)。toast は直した・見送った・返事の順。
- 知らせを出せない要求 (MessageMiddleware を通らない要求。試験の force_login など) では、知らせた印を付けない
  (次に画面からログインしたときに出す)。
- 開発機の自動ログイン (core の DevAutoLoginMiddleware) は login を呼ばないので、この知らせは出ない
  (自動ログインの人は superuser = アカウント管理の担当者。書いた人として確かめるときは、ログインの画面からその人でログインする)。
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


@receiver(user_logged_in, dispatch_uid="seizou.feedback.notify_dismissed")
def notify_dismissed(sender, request=None, user=None, **kwargs):
    """ログインした人が書いたフィードバックに、まだ知らせていない「見送った」があれば 1 回知らせる (notify_fixed と同じ形)。"""
    if request is None:
        return
    rows = services.unseen_dismissed(user)
    if not rows:
        return
    try:
        messages.info(request, services.dismissed_notice(rows))
    except messages.MessageFailure:
        return
    services.mark_dismissed_seen(rows, user)


@receiver(user_logged_in, dispatch_uid="seizou.feedback.notify_replies")
def notify_replies(sender, request=None, user=None, **kwargs):
    """ログインした人が書いたフィードバックの Issue に、まだ知らせていない返事があれば 1 回知らせる (notify_fixed と同じ形)。

    会社の口座が書いた返事と、会社の中で書いた返事は知らせない (services.unseen_replies が外す。開発の記録 docs/nights/20261107/N324.md)。
    """
    if request is None:
        return
    rows = services.unseen_replies(user)
    if not rows:
        return
    try:
        messages.info(request, services.reply_notice(rows))
    except messages.MessageFailure:
        return
    services.mark_replies_seen(rows, user)
