"""決まりの検査の画面 (05 の 4 節。02 の 2 節の「決まりの検査の画面」)。

開発の設定 (DEBUG が真) のとき、起動のときの決まりの検査の結果に違反があれば、
どの要求にも「決まりに沿っていません」の全面の画面を返す。同じ文を会社の置き場の
.seizou/violations.md に書く。直す命令 (/seizou:fix) が、写した文かこのファイルを読む。

検査は起動のときの結果 (seizou.core.rules.last_result) を使い回し、要求のたびには走らせない。
runserver はファイルの変更で起動し直すので、直して保存すると結果も新しくなる。
本番の形 (DEBUG が偽) では middleware の鎖から外れ、検査の module を読まない。
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth import SESSION_KEY
from django.contrib.auth import logout
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import MiddlewareNotUsed
from django.http import HttpResponse
from django.http import QueryDict
from django.shortcuts import resolve_url
from django.template.loader import render_to_string

from seizou.core import conf

logger = logging.getLogger(__name__)

#: 全面の画面のテンプレート
TEMPLATE = "core/rule_violations.html"

#: 画面で名指しする違反の数の上限 (写す文と .seizou/violations.md には全部を入れる)
NAMED_LIMIT = 20

#: 開発の道具の道 (素通しする)
DEBUG_TOOL_PREFIX = "/__debug__/"


def _violation_line(violation) -> str:
    """seizou_check の 1 行と同じ形。"""
    return f"{violation.code} {violation.path}:{violation.line} {violation.message} 直し方: {violation.fix}"


def rule_notices() -> dict | None:
    """起動のときの決まりの検査の結果を、画面と写す文の中身にする。違反が無ければ None。

    返す辞書: count (違反の数)・silenced (黙らせた数)・scanned (走査した本数)・
    items (先頭の 20 件。1 件は code・message・path・line・fix)・more (21 件目からの数)・text (写す文)。
    写す文の 2 行目から下は seizou_check の出力と同じ。誤りで画面を止めない (例外は None)。
    """
    # 本番の形で検査の module を読まないため、判定を import の前に置く (apps.py と同じ形)。
    if not settings.DEBUG:
        return None
    try:
        from seizou.core.rules import last_result

        result = last_result()
        if result is None or not result.violations:
            return None
        violations = result.violations
        count = len(violations)
        lines = [f"決まりの検査で違反が {count} 件あります。/seizou:fix で直してください。"]
        lines.extend(_violation_line(v) for v in violations)
        lines.append(f"合計: 違反 {count} 件、黙らせた {result.silenced} 件、走査 {result.scanned} 本")
        return {
            "count": count,
            "silenced": result.silenced,
            "scanned": result.scanned,
            "items": [
                {"code": v.code, "message": v.message, "path": v.path, "line": v.line, "fix": v.fix}
                for v in violations[:NAMED_LIMIT]
            ],
            "more": max(count - NAMED_LIMIT, 0),
            "text": "\n".join(lines) + "\n",
        }
    except Exception:
        logger.exception("決まりの検査の画面の中身を作れませんでした。画面は出さずに続けます。")
        return None


def violations_file() -> Path | None:
    """会社の置き場の .seizou/violations.md の道。settings に BASE_DIR が無ければ None。"""
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir is None:
        return None
    return Path(base_dir) / ".seizou" / "violations.md"


def sync_violations_file() -> None:
    """.seizou/violations.md を起動のときの結果に合わせる。

    違反があれば写す文と同じ中身を書き (同じなら書き直さない)、違反が 0 件ならファイルを消す。
    結果が None (検査が誤りで止まった・まだ走っていない) なら触らない。書けなければログに 1 行出して続ける。
    """
    path = violations_file()
    if path is None:
        return
    try:
        from seizou.core.rules import last_result

        result = last_result()
        if result is None:
            return
        if not result.violations:
            path.unlink(missing_ok=True)
            return
        notices = rule_notices()
        if notices is None:
            return
        data = notices["text"].encode("utf-8")
        if path.is_file() and path.read_bytes() == data:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError as error:
        logger.warning("決まりの検査の違反のファイル %s を書けませんでした: %s", path, error)
    except Exception:
        logger.exception("決まりの検査の違反のファイルを合わせられませんでした。起動は続けます。")


def _static_prefix() -> str | None:
    """STATIC_URL の道の部分 (先頭の / をそろえる)。無ければ None。"""
    path = urlsplit(getattr(settings, "STATIC_URL", None) or "").path
    if not path:
        return None
    return "/" + path.lstrip("/")


def _passes(path: str) -> bool:
    """静的なファイルと開発の道具の道は、全面の画面にしない。"""
    static = _static_prefix()
    if static and static != "/" and path.startswith(static):
        return True
    return path.startswith(DEBUG_TOOL_PREFIX)


class RuleNoticeMiddleware:
    """開発の設定で決まりの検査に違反があれば、どの要求にも決まりの検査の画面を返す。

    settings の MIDDLEWARE の最後に置く (画面を描くときに request.user と会社の名前が要るため)。
    """

    def __init__(self, get_response):
        if not settings.DEBUG:
            raise MiddlewareNotUsed("決まりの検査の画面は開発の設定 (DEBUG) のときだけ使う。")
        self.get_response = get_response
        # 起動のとき (runserver の自動の再読み込みのたび) に 1 回だけ合わせる。要求のたびには書かない。
        sync_violations_file()

    def __call__(self, request):
        notices = rule_notices()
        if notices is None or _passes(request.path):
            return self.get_response(request)
        if request.headers.get("HX-Request"):
            # htmx の部分の要求には、ページ全体を読み直させて全面の画面を出す。
            response = HttpResponse(status=200)
            response["HX-Redirect"] = request.get_full_path()
            return response
        html = render_to_string(TEMPLATE, {"rule_notices": notices}, request=request)
        return HttpResponse(html, status=500)


# ---------------------------------------------------------------- 開発機の自動ログイン (11-03 の昼。本人の求め)

#: 会社の settings.py の設定の名前 (環境変数の名前も同じ。.env の SEIZOU_DEV_AUTO_LOGIN=1)。
DEV_AUTO_LOGIN_SETTING = "SEIZOU_DEV_AUTO_LOGIN"


def dev_auto_login_enabled() -> bool:
    """開発機の自動ログインを使うか。DEBUG が真、起動の引数に runserver がある、会社の設定が真、の 3 つが全部そろったときだけ。"""
    if not settings.DEBUG or not getattr(settings, DEV_AUTO_LOGIN_SETTING, False):
        return False
    return "runserver" in sys.argv


def first_superuser():
    """自動ログインに使う人: 有効な superuser のうち、番号 (pk) のいちばん小さい人。いなければ None (新しくは作らない)。"""
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(is_superuser=True, is_active=True).order_by("pk").first()


class DevAutoLoginMiddleware:
    """開発機で、ログインの画面を飛ばして画面を開けるようにする。

    ログインしていない要求を、first_superuser() の要求として扱う (request.user を入れ替えるだけ)。
    - 動く条件は dev_auto_login_enabled() の 3 つ。本番の形 (gunicorn・DEBUG=0) では鎖から外れる (MiddlewareNotUsed)。
    - ログインの画面からログインした人の要求には何もしない (権限ごとの見え方は、その人でログインして確かめる)。
    - session には書かず、last_login も更新しない。ログアウトすると、次の要求からまた superuser として扱う。
    - AuthenticationMiddleware の後ろに置く (request.user が要る)。
    - 自動ログインの人は superuser なので、権限で変わる画面の確かめはこれでは済ませない (N36 の権限管理)。
    """

    def __init__(self, get_response):
        if not dev_auto_login_enabled():
            raise MiddlewareNotUsed(
                f"開発機の自動ログインは、DEBUG が真で、runserver で、{DEV_AUTO_LOGIN_SETTING} が真のときだけ使う。"
            )
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            admin = first_superuser()
            if admin is not None:
                request.user = admin
        return self.get_response(request)


# ---------------------------------------------------------------- htmx の要求のログインの切れ (N69)

#: htmx が画面の今の URL (利用者が見ていた画面) を入れる見出し
HX_CURRENT_URL = "HX-Current-URL"
#: 401 の本文 (htmx は 401 の本文を差し替えない。htmx の外の道具が読む)
LOGIN_GONE_TEXT = "ログインが切れました。ログインの画面へ移ります。"


def _login_url() -> str:
    return resolve_url(settings.LOGIN_URL)


def _back_to(request, location: str) -> str:
    """ログインの後に戻る先 (next) を付けたログインの URL。

    戻る先は HX-Current-URL の道と query (利用者が見ていた画面)。見出しが無い・道が / で始まらない・
    // で始まる・外の host のときは、302 の Location のまま返す。戻る先の確かめはログインの view がする。
    """
    current = urlsplit(request.headers.get(HX_CURRENT_URL, ""))
    if not current.path.startswith("/") or current.path.startswith("//"):
        return location
    if current.netloc and current.netloc != request.get_host():
        return location
    page = current.path + (f"?{current.query}" if current.query else "")
    query = QueryDict(mutable=True)
    query[REDIRECT_FIELD_NAME] = page
    # Django の redirect_to_login と同じ形 (/ は符号にしない)。
    return f"{_login_url()}?{query.urlencode(safe='/')}"


class HtmxLoginMiddleware:
    """htmx の部分の要求でログインが要るとき、302 の代わりに 401 と HX-Redirect を返し、画面の全体をログインの画面へ移す。

    部分の差し替えの中にログインの画面がはまり込まない。戻る先は利用者が見ていた画面 (HX-Current-URL)。
    - settings の MIDDLEWARE の AuthenticationMiddleware より後ろに置く。
    - 応答だけを見て、利用者と session を読まない (ログインしているかを見ず、ログインの道へ移す 302 だけを替える)。
    - ほかの 302 と、htmx でない要求の応答は触らない。開発でも本番でも鎖に残る。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.headers.get("HX-Request") or response.status_code != 302:
            return response
        location = response.get("Location", "")
        if urlsplit(location).path != urlsplit(_login_url()).path:
            return response
        replaced = HttpResponse(LOGIN_GONE_TEXT, status=401, content_type="text/plain; charset=utf-8")
        replaced["HX-Redirect"] = _back_to(request, location)
        return replaced


# ---------------------------------------------------------------- ログインの時間切れ (N369。エンタープライズ化の調べ 第一弾の候補 24)

#: ログインの時刻 (秒の整数) を持つ session の鍵
LOGIN_AT_KEY = "seizou_login_at"
#: 最後の人の操作の時刻 (秒の整数) を持つ session の鍵
SEEN_AT_KEY = "seizou_seen_at"
#: 最後の操作の時刻を書き直す間隔 (秒。5 分)。要求のたびに書くと、session の保存と cookie の書き直しが要求の数だけ増えるため
TOUCH_SECONDS = 300
#: 自動の取り直しの要求に base.html が付ける見出し (値は "1")
POLL_HEADER = "X-Seizou-Poll"


def _now() -> int:
    """時間切れの時計 (秒の整数)。試験は monkeypatch でこの関数を差し替えて時計を進める。"""
    return int(time.time())


def _stamp(value) -> int | None:
    """session に持った時刻。秒の整数でなければ None (真偽は int の仲間なので外す)。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


class SessionTimeoutMiddleware:
    """ログインしてから、操作しない間の時間 (既定 2 時間) か、ログインからの最長 (既定 12 時間) を過ぎたら、ログアウトしてログインの画面へ移す。

    - 時間は conf.session_limits() で読む (会社の設定 session_idle_minutes・session_max_hours)。
    - session に認証の鍵 (django.contrib.auth.SESSION_KEY) が無い要求には何もしない (ログインしていない要求・
      開発機の自動ログイン・session を使わない口)。切れた後の要求も、logout が session を空にしたので何もしない。
    - ログインの時刻と最後の操作の時刻が session に無ければ、今の時刻を書く (ログインの直後の最初の要求と、この版より前の session)。
    - 切れたら logout し、LOGIN_URL へ 302 を返す (next は開いた道)。ログインの画面の要求は、logout だけして先へ通す (302 を重ねない)。
    - 切れていなければ、取り直しの要求 (見出し X-Seizou-Poll が 1) でなく、最後の操作の時刻から TOUCH_SECONDS 以上たっていれば、
      最後の操作の時刻を今にする (書き込みを 5 分に 1 回にする。操作しない間の時間は、最後の本当の操作から最大 5 分早く切れる)。
    - 同じ人がログアウトせずにもう 1 度ログインすると、Django の login は session の中身を残すので、ログインの時刻は前のまま (早く切れる側)。
    訳: 操作しない間の時間切れとログインからの最長を、session の時刻で決めるため。最長は cookie の長さ (SESSION_COOKIE_AGE や
    set_expiry の秒) では作れない (保存のたびに延びる) ので、ログインの時刻を session に持って比べる。自動の取り直し (受信箱の 2 秒ごとなど) は
    人の操作でないので時刻を進めない (見分けは base.html が付ける見出し X-Seizou-Poll)。開発機の自動ログインは session に書かないので切らない。
    settings の MIDDLEWARE の HtmxLoginMiddleware より後ろに置く (切れた時の 302 を、htmx の要求では 401 と HX-Redirect にしてもらうため)。
    出所 = 本人の決め 11-08 00:40 と、エンタープライズ化の調べ 第一弾の候補 24 の直し方の直し (a)(b)(c)(d)。
    変えてよい条件 = 本人が決め直したとき、または会社が設定 (session_idle_minutes・session_max_hours) で変えたとき。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        session = getattr(request, "session", None)
        # 認証の鍵を先に見る (ログインしていない要求では、会社の設定の表を読まない)。
        if session is None or SESSION_KEY not in session:
            return self.get_response(request)
        now = _now()
        login_at = _stamp(session.get(LOGIN_AT_KEY))
        seen_at = _stamp(session.get(SEEN_AT_KEY))
        if login_at is None:
            login_at = session[LOGIN_AT_KEY] = now
        if seen_at is None:
            seen_at = session[SEEN_AT_KEY] = now
        idle_seconds, max_seconds = conf.session_limits()
        if now - seen_at > idle_seconds or now - login_at > max_seconds:
            logout(request)
            if urlsplit(request.path).path == urlsplit(_login_url()).path:
                return self.get_response(request)
            return redirect_to_login(request.get_full_path())
        if request.headers.get(POLL_HEADER) != "1" and now - seen_at >= TOUCH_SECONDS:
            session[SEEN_AT_KEY] = now
        return self.get_response(request)
