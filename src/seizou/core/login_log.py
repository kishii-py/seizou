"""ログインの記録 (seizou.login)。ログインの成功・失敗・ログアウトを 1 行ずつ書く signal の受け手。

core の apps.py の ready() が読み込む (django.contrib.auth.signals の 3 つにつなぐ)。
- 成功 (user_logged_in) とログアウト (user_logged_out) は INFO、失敗 (user_login_failed) は WARNING で書く。
- 行に書くのは、成功・失敗・ログアウトの別と、ID と接続元 (login_guard.client_address) だけ。日時は記録の側 (journald) が付ける。
- パスワードと、打たれた字の全部 (credentials のほかの値) は書かない。ID は 150 字まで (利用者の ID の欄の長さ)。
- ID と接続元は %r で書く (打たれた ID の改行などで、記録の行を偽らせない)。
- 締め出し (ID と接続元の組の 5 回目・接続元の上限) と、締め出しの間に断った試しの行は login_guard が書く
  (締め出しの間は認証の前に断るので、user_login_failed が飛ばない)。
- 開発機の自動ログイン (core の DevAutoLoginMiddleware) は login を呼ばないので、この行は出ない。
- 成功とログアウトの INFO は、置き場の settings.py の LOGGING が seizou.login を INFO で受けないと捨てられる (LOGGING は別の依頼で足す)。

訳: ログインの成功・失敗・ログアウトを、日時・接続元・ID・成否の分かる 1 行ずつで残すため (取引先の点検票で問われることがある)。表は作らず、記録 (seizou.login) に書く (本番は LOGGING で journald に残る)。パスワードは書かず、ID は 150 字まで、ID と接続元は %r で書く (改行などで記録の行を偽らせない)。出所 = エンタープライズ化の調べを受けた作者の決め (11-07)・本人の決め 11-08 00:38。変えてよい条件 = ログインの記録を表 (LoginEvent) と一覧の画面に持つと決めたとき (次の版で)。
"""

from __future__ import annotations

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .login_guard import USERNAME_LOG_LENGTH, client_address, logger


def _address(request) -> str:
    return client_address(request) if request is not None else ""


def _username(user) -> str:
    return str(user.get_username())[:USERNAME_LOG_LENGTH] if user is not None else ""


@receiver(user_logged_in, dispatch_uid="seizou.core.login_log.log_login")
def log_login(sender, request=None, user=None, **kwargs):
    """ログインに成功したときに 1 行 (INFO)。"""
    logger.info("ログインしました (ID: %r・接続元: %r)。", _username(user), _address(request))


@receiver(user_login_failed, dispatch_uid="seizou.core.login_log.log_login_failed")
def log_login_failed(sender, credentials=None, request=None, **kwargs):
    """ログインに失敗したときに 1 行 (WARNING)。credentials からは ID だけを読む (パスワードは伏せた字でも書かない)。"""
    username = str((credentials or {}).get("username", "") or "")[:USERNAME_LOG_LENGTH]
    logger.warning("ログインに失敗しました (ID: %r・接続元: %r)。", username, _address(request))


@receiver(user_logged_out, dispatch_uid="seizou.core.login_log.log_logout")
def log_logout(sender, request=None, user=None, **kwargs):
    """ログアウトしたときに 1 行 (INFO)。ログインしていない人のログアウトでは user が None なので ID は空の字。"""
    logger.info("ログアウトしました (ID: %r・接続元: %r)。", _username(user), _address(request))
