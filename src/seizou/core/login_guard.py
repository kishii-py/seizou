"""ログインの締め出し (総当たりの止め) と、締め出しの記録。

締め出しは 2 つ。どちらで止めても、画面の字は同じ (LOCKED_MESSAGE。どちらで止めたかを外に見せない)。
(1) ID と接続元の組: 同じ ID と同じ接続元 (既定は REMOTE_ADDR) の組で 5 回続けて失敗すると、15 分の間はその組のログインを
受け付けない (正しいパスワードでも)。成功で数え直す。数は最後の失敗から 15 分で消える。
(2) 接続元だけ: 接続元の見出し (SEIZOU_CLIENT_IP_HEADER) を書いた置き場だけ、同じ接続元からのログインの失敗が、最初の失敗から
15 分の窓で 30 回になると、その時から 15 分はその接続元のログインを ID を問わず受け付けない。成功で数を消さない。
見出しが無い置き場では効かせない (全員が nginx の 127.0.0.1 に見え、外から会社の全員を止められるため)。
数は置き場 (settings.BASE_DIR) の cache/login/ のファイルに、組か接続元の sha256 を鍵にして持つ
(打たれた ID と接続元の字を鍵にしない)。ファイルなので、同じ置き場で動く gunicorn の worker と process の全部で 1 か所に数える
(process の記憶に持つと worker ごとに数え、締め出しまでの回数が揺れる)。置き場は settings.py の SEIZOU_LOGIN_CACHE_DIR で変えられ、
BASE_DIR が無いときだけ process の記憶に持つ。
記録 (seizou.login): ログインの成功・失敗・ログアウトは core.login_log が 1 行ずつ書く。締め出したとき ((1) と (2)) と、
締め出しの間に断った試しは、この module が 1 行書く。どの行にもパスワードは出さない。
回数と分は固定 (設定の鍵を作らない)。接続元は既定では REMOTE_ADDR だけを見る (proxy が付ける見出しは偽れるため)。
nginx の後ろでは REMOTE_ADDR がいつも nginx (127.0.0.1) になり ID ごとの締め出しになるので、手前の proxy が接続元を
見出しに入れる置き場だけ、置き場の .env (か settings.py) に SEIZOU_CLIENT_IP_HEADER=X-Real-IP のように見出しの名前を書く。
書いた見出しの最初の値を接続元にし、見出しが無い要求は REMOTE_ADDR に戻る (nginx が外から来た同じ名前の見出しを上書きする前提)。
ログインの view (core.views.RememberLoginView) は変えず、core.urls が as_view(authentication_form=…) で渡す。
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import time
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
#: 1 つの接続元からのログインの失敗の上限 (窓の中のこの回数目の失敗で、その接続元を ID を問わず締め出す。見出しのある置き場だけ)
ADDRESS_FAIL_LIMIT = 30
#: 接続元の失敗を数える窓の分 (最初の失敗から。失敗が続いても延びない)
ADDRESS_WINDOW_MINUTES = 15
ADDRESS_WINDOW_SECONDS = ADDRESS_WINDOW_MINUTES * 60
#: 接続元の数の cache の鍵の頭
ADDRESS_PREFIX = "seizou:login-fail-address:"
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


def _now() -> float:
    """今の時刻 (秒)。接続元の窓と締め出しの終わりを決める時計。試験で差し替える。"""
    return time.time()


def address_key(address: str) -> str:
    """接続元の数の cache の鍵。接続元の字は鍵に入れない (sha256)。"""
    return ADDRESS_PREFIX + hashlib.sha256(address.encode("utf-8")).hexdigest()


def _address_limit_applies(address: str) -> bool:
    """接続元の上限を効かせるか。接続元の見出しを書いた置き場で、接続元が空の字でないときだけ (決まりと訳は record_address_failure)。"""
    return bool(address) and bool(client_ip_header())


def _address_entry_locked(entry, now: float) -> bool:
    return bool(entry) and now < entry.get("locked_until", 0)


def is_address_locked(address: str) -> bool:
    """その接続元が締め出しの間か。見出しが無い置き場と、接続元が空の字のときはいつも偽 (決まりと訳は record_address_failure)。"""
    if not _address_limit_applies(address):
        return False
    return _address_entry_locked(cache.get(address_key(address)), _now())


def record_address_failure(address: str) -> int:
    """接続元の失敗を 1 回数え、今の窓の回数を返す (効かせない置き場では何もせず 0)。窓の中の 30 回目で、その時から 15 分締め出す。

    窓は最初の失敗の時刻から 15 分 (失敗が続いても延びない)。窓が過ぎたら数え直す。締め出した時に記録に 1 行出す。
    cache の値は {"started_at": 窓の始まり, "count": 回数, "locked_until": 締め出しの終わり (無ければ 0)}。
    寿命は窓の終わりと締め出しの終わりの遅い方 (寿命は片づけのためだけで、窓と締め出しは _now() と比べて決める)。

    訳: 1 つの接続元から ID を替えて試すログインを止めるため。15 分に 30 回は ID ごとの 5 回より高い (町工場は事務所の PC が 1 つの NAT から来るので低くしない)。接続元の見出し (SEIZOU_CLIENT_IP_HEADER) が無い置き場では全員が nginx (127.0.0.1) に見え、外から会社の全員を止められるので効かせない。数は最初の失敗から 15 分の窓で数え、成功で消さない (成功を挟んで試し続けさせない)。出所 = 本人の決め 11-08 00:38 (エンタープライズ化の調べを受けた作者の決め)。変えてよい条件 = 本人が決め直したとき。
    """
    if not _address_limit_applies(address):
        return 0
    key = address_key(address)
    now = _now()
    entry = cache.get(key)
    locked = _address_entry_locked(entry, now)
    if not entry or (not locked and now >= entry["started_at"] + ADDRESS_WINDOW_SECONDS):
        entry = {"started_at": now, "count": 0, "locked_until": 0}
    entry["count"] += 1
    if entry["count"] >= ADDRESS_FAIL_LIMIT and not locked:
        entry["locked_until"] = now + LOCK_SECONDS
        logger.warning(
            "接続元 %r からのログインの失敗が %d 分に %d 回になったので、この接続元を %d 分止めます。",
            address,
            ADDRESS_WINDOW_MINUTES,
            ADDRESS_FAIL_LIMIT,
            LOCK_MINUTES,
        )
    expires_at = max(entry["started_at"] + ADDRESS_WINDOW_SECONDS, entry["locked_until"])
    cache.set(key, entry, max(1, math.ceil(expires_at - now)))
    return entry["count"]


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

        締め出しの間 = ID と接続元の組が締め出しの間か、接続元が締め出しの間 (どちらでも同じ字で断る)。
        失敗は組 (record_failure) と接続元 (record_address_failure) の両方で数え、どちらかが上限に届いたら断る。
        成功で数え直すのは組だけ (接続元の数は成功で消さない)。

        訳: 総当たりを止め、締め出しの間に断った試しも記録に残すため (締め出しの間は認証の前に断るので user_login_failed が飛ばず、login_log の失敗の行が出ない)。出所 = ID と接続元の組の 5 回と 15 分は未確認 (この module の頭のとおり)・締め出しの間の行はエンタープライズ化の調べを受けた作者の決め (11-07)・接続元の上限は本人の決め 11-08 00:38。変えてよい条件 = 本人が決め直したとき。
        """
        username = self.cleaned_data.get("username") or ""
        address = client_address(self.request) if self.request is not None else ""
        if username and (is_locked(username, address) or is_address_locked(address)):
            logger.warning(
                "締め出しの間のログインを断りました (ID: %r・接続元: %r)。", username[:USERNAME_LOG_LENGTH], address
            )
            raise ValidationError(self.error_messages["locked"], code="locked")
        try:
            cleaned = super().clean()
        except ValidationError as error:
            if username and getattr(error, "code", None) == "invalid_login":
                pair_count = record_failure(username, address)
                address_count = record_address_failure(address)
                if pair_count >= FAIL_LIMIT or address_count >= ADDRESS_FAIL_LIMIT:
                    raise ValidationError(self.error_messages["locked"], code="locked") from None
            raise
        if username and self.get_user() is not None:
            clear_failures(username, address)
        return cleaned
