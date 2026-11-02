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
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.http import HttpResponse
from django.template.loader import render_to_string

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
