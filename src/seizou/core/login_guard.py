"""ログインの締め出し (総当たりの止め)。

同じ ID と同じ接続元 (REMOTE_ADDR) の組で 5 回続けて失敗すると、15 分の間はその組のログインを受け付けない
(正しいパスワードでも)。成功で数え直す。数は Django の既定の cache に組の sha256 を鍵にして持つ
(打たれた ID の字を鍵にしない)。締め出したときに記録 (seizou.login) に 1 行出す (パスワードは出さない)。
回数と分は固定 (設定の鍵を作らない)。proxy が付ける接続元の見出しは読まない (偽れるため。nginx の後ろでは
接続元がいつも nginx になり、ID ごとの締め出しになる)。
ログインの view (core.views.RememberLoginView) は変えず、core.urls が as_view(authentication_form=…) で渡す。
"""

from __future__ import annotations

import hashlib
import logging

from django.contrib.auth.forms import AuthenticationForm
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.views.decorators.debug import sensitive_variables

#: 続けて失敗してよい回数 (この回数目の失敗で締め出す)
FAIL_LIMIT = 5
#: 締め出す分
LOCK_MINUTES = 15
LOCK_SECONDS = LOCK_MINUTES * 60
#: cache の鍵の頭
CACHE_PREFIX = "seizou:login-fail:"
#: ID かパスワードが違うときの文 (ログインの画面の今の字と同じ)
INVALID_MESSAGE = "ID かパスワードが違います。確かめてもう一度入れてください。"
#: 締め出しの間の文
LOCKED_MESSAGE = f"ログインに続けて失敗したので、{LOCK_MINUTES} 分の間は受け付けません。"

logger = logging.getLogger("seizou.login")


def client_address(request) -> str:
    """接続元の IP (REMOTE_ADDR)。無ければ空の字。"""
    return request.META.get("REMOTE_ADDR", "")


def failure_key(username: str, address: str) -> str:
    """ID (大小をそろえる) と接続元の組の cache の鍵。打たれた ID の字は鍵に入れない。"""
    return CACHE_PREFIX + hashlib.sha256(f"{username.casefold()}\n{address}".encode("utf-8")).hexdigest()


def is_locked(username: str, address: str) -> bool:
    return cache.get(failure_key(username, address), 0) >= FAIL_LIMIT


def record_failure(username: str, address: str) -> int:
    """失敗を 1 回数え、今の回数を返す。数は最後の失敗から 15 分で消える。締め出したときだけ記録に 1 行出す。"""
    key = failure_key(username, address)
    count = cache.get(key, 0) + 1
    cache.set(key, count, LOCK_SECONDS)
    if count == FAIL_LIMIT:
        logger.warning(
            "ログインに %d 回続けて失敗したので、%d 分止めます (ID: %r・接続元: %s)。",
            FAIL_LIMIT,
            LOCK_MINUTES,
            username,
            address,
        )
    return count


def clear_failures(username: str, address: str) -> None:
    cache.delete(failure_key(username, address))


class LockoutAuthenticationForm(AuthenticationForm):
    """ログインの form。締め出しの間は認証せずに断り、失敗を数え、成功で数え直す。"""

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": INVALID_MESSAGE,
        "inactive": INVALID_MESSAGE,
        "locked": LOCKED_MESSAGE,
    }

    @sensitive_variables()
    def clean(self):
        username = self.cleaned_data.get("username") or ""
        address = client_address(self.request) if self.request is not None else ""
        if username and is_locked(username, address):
            raise ValidationError(self.error_messages["locked"], code="locked")
        try:
            cleaned = super().clean()
        except ValidationError as error:
            if username and getattr(error, "code", None) == "invalid_login":
                if record_failure(username, address) >= FAIL_LIMIT:
                    raise ValidationError(self.error_messages["locked"], code="locked") from None
            raise
        if username and self.get_user() is not None:
            clear_failures(username, address)
        return cleaned
