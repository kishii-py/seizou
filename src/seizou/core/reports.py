"""帳票の部品 (render_html と render_pdf)。

WeasyPrint は render_pdf の中でだけ import する。WeasyPrint (か OS の pango) が無い環境では
render_pdf が PdfUnavailable を投げ、画面はそれを捕まえて印刷用の HTML に落とす。
PDF を出すには extras を入れる (`pip install "seizou[pdf]"`。手元の開発は `uv sync --extra pdf`) と、OS に pango を入れる。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.exceptions import SuspiciousFileOperation
from django.template.loader import render_to_string

# WeasyPrint が帳票の中の URL (/static/... など) を解くときの仮の起点。
# .invalid は使われないと決まった名前で、この起点の URL はネットに出ず、fetch_local が手元のファイルから返す。
BASE_URL = "https://seizou.invalid/"

PDF_VERSION = "1.4"

STATIC_PREFIX = "/static/"
MEDIA_PREFIX = "/media/"

# 帳票が読む物の型。<link rel="stylesheet"> は text/css でないと WeasyPrint が読まない。
CONTENT_TYPES = {
    ".css": "text/css",
    ".ttf": "font/ttf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}


class PdfUnavailable(Exception):
    """PDF を作れない (WeasyPrint か OS の pango が無い)。画面は印刷用の HTML に落とす。"""


def render_html(template, context):
    """Django のテンプレートを描いた HTML の文字列を返す。WeasyPrint に触れない。"""
    return render_to_string(template, context)


def render_pdf(template, context):
    """render_html の結果を WeasyPrint で PDF にし、その bytes を返す。

    PDF の版は 1.4 にする。1.5 以上では WeasyPrint がフォントの辞書を圧縮した物の流れに入れるので、
    埋め込んだフォントの名前 (NotoSansJP) を PDF の bytes の検索で確かめられなくなる。
    """
    return _render_document(template, context).write_pdf(pdf_version=PDF_VERSION)


def _render_document(template, context):
    """WeasyPrint の document (ページに割り付けた物) を返す。"""
    try:
        from weasyprint import HTML
        from weasyprint.urls import URLFetcher, URLFetcherResponse
    except (ImportError, OSError) as exc:
        # pango が無いと、WeasyPrint の import は OSError になる。
        raise PdfUnavailable(
            "PDF の部品 (WeasyPrint) が使えません。印刷用の画面をお使いください。"
        ) from exc

    class LocalFetcher(URLFetcher):
        """fetch_local だけを使う取り口。ネットとほかのファイルには触れない。"""

        def fetch(self, url, headers=None):
            body, content_type = fetch_local(url)
            return URLFetcherResponse(url, body, {"Content-Type": content_type})

    html = render_html(template, context)
    return HTML(string=html, base_url=BASE_URL, url_fetcher=LocalFetcher()).render()


def fetch_local(url):
    """帳票が読むファイルの中身と型を返す。static と media の下だけを読む。

    BASE_URL の起点の /static/ の下は staticfiles の finders で引き (collectstatic に頼らない)、
    /media/ の下は MEDIA_ROOT の下だけを読む。ほかの URL (http・https・file・data など) と、
    根の外に出るパスは ValueError で断る。WeasyPrint は断られた物を「取れない」として飛ばす。
    鍵・環境変数・ネットワークには触れない。
    """
    parts = urlsplit(url)
    base = urlsplit(BASE_URL)
    if (parts.scheme, parts.netloc) != (base.scheme, base.netloc):
        raise ValueError(f"帳票の外の URL は読みません: {url[:200]}")
    path = unquote(parts.path)

    if path.startswith(STATIC_PREFIX):
        relative = _checked_relative(path[len(STATIC_PREFIX) :])
        try:
            found = finders.find(relative)
        except SuspiciousFileOperation as exc:
            raise ValueError(f"static の外のパスは読みません: {path}") from exc
        if not found:
            raise FileNotFoundError(f"static にありません: {path}")
        file_path = Path(found)
    elif path.startswith(MEDIA_PREFIX):
        if not settings.MEDIA_ROOT:
            raise ValueError("MEDIA_ROOT が無いので media は読みません")
        relative = _checked_relative(path[len(MEDIA_PREFIX) :])
        root = Path(settings.MEDIA_ROOT).resolve()
        file_path = (root / relative).resolve()
        if not file_path.is_relative_to(root):
            raise ValueError(f"media の外のパスは読みません: {path}")
    else:
        raise ValueError(f"static と media の外は読みません: {path}")

    if not file_path.is_file():
        raise FileNotFoundError(f"ファイルがありません: {path}")
    content_type = CONTENT_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
    return file_path.read_bytes(), content_type


def _checked_relative(relative):
    """根からの相対のパスを確かめて返す。根の外に出る形 (.. や絶対のパス) は断る。"""
    if not relative or relative.startswith("/") or any(c in relative for c in "\\:\x00"):
        raise ValueError(f"読めないパスの形です: {relative}")
    if any(segment in ("", ".", "..") for segment in relative.split("/")):
        raise ValueError(f"読めないパスの形です: {relative}")
    return relative
