"""上げたファイル (MEDIA_ROOT の下。図面・添付・マニュアルの画像など) を、ログインした人にだけ返す口 (README の「本番の決まり」)。

開発機でも本番でも同じ view (/media/<path>) を通る。違うのは配り方だけで、会社の settings.py の
SEIZOU_FILE_DELIVERY (環境変数から読む) で選ぶ。

- django (既定): Django がファイルを送る。nginx が無くても動く (開発機・審査員の手元・小さな本番)。
- nginx: Django はログインと確かめを済ませ、本文の無い応答に X-Accel-Redirect を付けて、nginx の内部の
  location (INTERNAL_PREFIX。置き場の deploy/nginx-files.conf) にファイルを渡す。大きなファイルを送る間、
  Django の worker がふさがらない。nginx を通った印 (PROXY_HEADER) が要求に無ければ、空の応答を返さずに
  503 の画面で止まる (nginx の設定の漏れに、その場で気づくため)。

どの配り方でも同じ事:
- ログインしていなければログインの画面へ。例外は会社の情報のロゴ (ログインの画面に出す物) だけ (is_public)。
- 断るときは全部 404 (ファイルが「無い」と「見せない」を外から見分けさせない)。MEDIA_ROOT の外は読まない。
- HTML・SVG・XML・JS のような、ブラウザがスクリプトとして動かしうる型は、必ずダウンロードにする (上げたファイルの
  スクリプトが、同じ origin で動くのを防ぐ)。
- X-Content-Type-Options: nosniff と、保存させないヘッダー (ログアウトの後に残ったファイルを見せない。図面の口と同じ) を付ける。
  nginx は X-Accel-Redirect のとき Cache-Control と Content-Disposition は引き継ぐが nosniff は落とすので、
  deploy/nginx-files.conf の側でも nosniff を付ける。
- Django から見えない漏れ (内部の location の internal の付け忘れ、alias の誤り) を外から確かめる命令は会期後。
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import render
from django.utils.cache import add_never_cache_headers
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_http_methods

logger = logging.getLogger("seizou.media")

#: 会社の settings.py の設定の名前 (環境変数の名前も同じ)。
SETTING = "SEIZOU_FILE_DELIVERY"
DELIVERY_DJANGO = "django"
DELIVERY_NGINX = "nginx"
DELIVERIES = (DELIVERY_DJANGO, DELIVERY_NGINX)

#: nginx の内部の location の名前 (deploy/nginx-files.conf と同じ)。X-Accel-Redirect の値の頭に付く。
INTERNAL_PREFIX = "/seizou-files/"
#: nginx が Django へ渡す要求に付ける印 (proxy_set_header X-Seizou-Proxy nginx)。
PROXY_HEADER = "X-Seizou-Proxy"
PROXY_HEADER_META = "HTTP_X_SEIZOU_PROXY"
PROXY_HEADER_VALUE = "nginx"
#: 置き場に seizou new が書く nginx の設定の断片 (503 の画面と README が指す)。
NGINX_SNIPPET = "deploy/nginx-files.conf"

#: ブラウザがスクリプトとして動かしうる型。inline で返すと同じ origin で動くので、必ずダウンロードにする。
DOWNLOAD_ONLY_EXTENSIONS = frozenset({".html", ".htm", ".xhtml", ".svg", ".svgz", ".xml", ".js", ".mjs"})
DOWNLOAD_ONLY_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "image/svg+xml",
        "text/xml",
        "application/xml",
        "text/javascript",
        "application/javascript",
        "application/ecmascript",
    }
)

ADVICE_NO_PROXY = (
    f"このシステムは、上げたファイルを nginx が送る設定 ({SETTING}=nginx) です。システムを預かる人は、"
    f"置き場の {NGINX_SNIPPET} を nginx の設定に読み込み、Django へ渡す location に"
    f"「proxy_set_header {PROXY_HEADER} {PROXY_HEADER_VALUE};」の 1 行を足してください。"
)
ADVICE_BAD_VALUE = f"システムを預かる人は、.env か settings.py の {SETTING} を django か nginx のどちらかにしてください。"


def delivery() -> str:
    """配り方の設定の値 (小文字)。無ければ django。"""
    value = getattr(settings, SETTING, DELIVERY_DJANGO) or DELIVERY_DJANGO
    return str(value).strip().lower()


def relative_path(name: str) -> PurePosixPath | None:
    """URL の <path> を、MEDIA_ROOT からの相対の道にする。危ない形 (空・絶対・..・\\・NUL) は None。"""
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        return None
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in ("..", ".") for part in path.parts):
        return None
    return path


def local_file(relative: PurePosixPath) -> Path | None:
    """MEDIA_ROOT の下の実際のファイル。MEDIA_ROOT が無い・外を指す・無い・フォルダ、のどれかなら None。"""
    root = getattr(settings, "MEDIA_ROOT", "") or ""
    if not root:
        return None
    base = Path(root).resolve()
    target = base.joinpath(*relative.parts).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        return None
    return target


def is_public(relative: PurePosixPath) -> bool:
    """ログインしていなくても返すファイルか = 会社の情報のロゴだけ (ログインの画面と左上に出すので、ログインの前にも要る)。

    今の会社の行の logo と同じ道の物だけ。company/ の下のほかのファイルや、外した古いロゴは含めない。
    """
    from seizou.core.models import Company

    row = Company.objects.order_by("pk").only("logo").first()
    name = row.logo.name if row is not None and row.logo else ""
    return bool(name) and PurePosixPath(name) == relative


def content_type_of(name: str) -> str:
    """拡張子から推した型。推せなければ application/octet-stream。"""
    guessed, _encoding = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def must_download(name: str, content_type: str) -> bool:
    """必ずダウンロードにする型か (DOWNLOAD_ONLY_EXTENSIONS と DOWNLOAD_ONLY_TYPES)。"""
    if PurePosixPath(name).suffix.lower() in DOWNLOAD_ONLY_EXTENSIONS:
        return True
    return content_type.split(";", 1)[0].strip().lower() in DOWNLOAD_ONLY_TYPES


def _protect(response):
    response["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(response)
    return response


def _unavailable(request, reason: str, advice: str):
    logger.error("ファイルを返せません: %s (%s=%s)", reason, SETTING, delivery())
    return render(request, "core/file_delivery_error.html", {"reason": reason, "advice": advice}, status=503)


@require_http_methods(["GET", "HEAD"])
def protected_file(request, name: str):
    """上げたファイルを、ログインした人にだけ返す (画面でない口)。URL の <path> は MEDIA_ROOT からの相対の道。

    会社の情報のロゴ (is_public) だけは、ログインの画面に出すので、ログインしていなくても返す。
    """
    relative = relative_path(name)
    if not request.user.is_authenticated and not (relative is not None and is_public(relative)):
        return redirect_to_login(request.get_full_path())
    mode = delivery()
    if mode not in DELIVERIES:
        return _unavailable(request, f"{SETTING} の値「{mode}」は、django か nginx のどちらかにしてください。", ADVICE_BAD_VALUE)
    if mode == DELIVERY_NGINX and request.META.get(PROXY_HEADER_META) != PROXY_HEADER_VALUE:
        return _unavailable(
            request, f"要求に、nginx を通った印 ({PROXY_HEADER}: {PROXY_HEADER_VALUE}) がありません。", ADVICE_NO_PROXY
        )
    target = local_file(relative) if relative is not None else None
    if target is None:
        raise Http404("ファイルがありません。")
    content_type = content_type_of(target.name)
    download = must_download(target.name, content_type)
    if mode == DELIVERY_NGINX:
        response = HttpResponse(status=200, content_type=content_type)
        response["Content-Disposition"] = content_disposition_header(download, target.name)
        response["X-Accel-Redirect"] = INTERNAL_PREFIX + quote(relative.as_posix(), safe="/")
        return _protect(response)
    response = FileResponse(target.open("rb"), content_type=content_type, as_attachment=download, filename=target.name)
    return _protect(response)
