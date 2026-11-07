"""ログインの締め出し (総当たりの止め)。

同じ ID と同じ接続元 (REMOTE_ADDR) の組で 5 回続けて失敗すると、15 分の間はその組のログインを受け付けない
(正しいパスワードでも)。成功で数え直す。数は置き場 (settings.BASE_DIR) の cache/login/ のファイルに、組の sha256 を鍵にして持つ
(打たれた ID の字を鍵にしない)。ファイルなので、同じ置き場で動く gunicorn の worker と process の全部で 1 か所に数える
(process の記憶に持つと worker ごとに数え、締め出しまでの回数が揺れる)。置き場は settings.py の SEIZOU_LOGIN_CACHE_DIR で変えられ、
BASE_DIR が無いときだけ process の記憶に持つ。締め出したときに記録 (seizou.login) に 1 行出す (パスワードは出さない)。
回数と分は固定 (設定の鍵を作らない)。接続元は既定では REMOTE_ADDR だけを見る (proxy が付ける見出しは偽れるため)。
nginx の後ろでは REMOTE_ADDR がいつも nginx (127.0.0.1) になり ID ごとの締め出しになるので、手前の proxy が接続元を
見出しに入れる置き場だけ、置き場の .env (か settings.py) に SEIZOU_CLIENT_IP_HEADER=X-Real-IP のように見出しの名前を書く。
書いた見出しの最初の値を接続元にし、見出しが無い要求は REMOTE_ADDR に戻る (nginx が外から来た同じ名前の見出しを上書きする前提)。
ログインの view (core.views.RememberLoginView) は変えず、core.urls が as_view(authentication_form=…) で渡す。
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.core.cache.backends.filebased import FileBasedCache
from django.core.cache.backends.locmem import LocMemCache
from django.core.exceptions import ValidationError
from django.views.decorators.debug import sensitive_variables

#: 手前の proxy が接続元を入れる見出しの名前を書く設定 (settings.py の名前と環境変数の名前は同じ)。空なら REMOTE_ADDR だけを見る。
HEADER_SETTING = "SEIZOU_CLIENT_IP_HEADER"
#: 締め出しの数のファイルを置く場所を書く設定 (settings.py)。無ければ BASE_DIR / "cache" / "login"。
CACHE_DIR_SETTING = "SEIZOU_LOGIN_CACHE_DIR"

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
#: 記録 (seizou.login) に書く ID の長さの上限 (利用者の ID の欄の長さ = Django の AbstractUser の username と同じ)
USERNAME_LOG_LENGTH = 150

logger = logging.getLogger("seizou.login")


def _make_cache():
    """締め出しの数の置き場。置き場の cache/login/ のファイル (worker と process の全部で 1 か所)。BASE_DIR が無ければ process の記憶。"""
    location = getattr(settings, CACHE_DIR_SETTING, None)
    if not location:
        base = getattr(settings, "BASE_DIR", None)
        location = Path(base) / "cache" / "login" if base else None
    if location:
        return FileBasedCache(str(location), {"TIMEOUT": LOCK_SECONDS})
    return LocMemCache("seizou-login", {"TIMEOUT": LOCK_SECONDS})


cache = _make_cache()


def client_ip_header() -> str:
    """接続元を読む見出しの名前 (settings.py → 環境変数 → 空の字)。例 X-Real-IP。"""
    value = getattr(settings, HEADER_SETTING, None)
    if value is None:
        value = os.environ.get(HEADER_SETTING, "")
    return str(value or "").strip()


def client_address(request) -> str:
    """接続元の IP。既定は REMOTE_ADDR。見出しの名前が設定されていれば、その見出しの最初の値 (無ければ REMOTE_ADDR)。どちらも無ければ空の字。"""
    header = client_ip_header()
    if header:
        meta_key = "HTTP_" + header.upper().replace("-", "_")
        first = str(request.META.get(meta_key, "")).split(",")[0].strip()
        if first:
            return first
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
        """締め出しの間は認証せずに断り (その試しも記録に 1 行書く)、失敗を数え、成功で数え直す。

        訳: 総当たりを止め、締め出しの間に断った試しも記録に残すため (締め出しの間は認証の前に断るので user_login_failed が飛ばず、login_log の失敗の行が出ない)。出所 = ID と接続元の組の 5 回と 15 分は未確認 (この module の頭のとおり)・締め出しの間の行はエンタープライズ化の調べ 第一弾の候補 23 の直し方の直し (a) (11-07)。変えてよい条件 = 本人が決め直したとき。
        """
        username = self.cleaned_data.get("username") or ""
        address = client_address(self.request) if self.request is not None else ""
        if username and is_locked(username, address):
            logger.warning(
                "締め出しの間のログインを断りました (ID: %r・接続元: %r)。", username[:USERNAME_LOG_LENGTH], address
            )
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
