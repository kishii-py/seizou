"""帳票の部品 (render_html と render_pdf と、描いた後の HTML の字から PDF を作る render_html_pdf)。

WeasyPrint は PDF を作る関数 (render_pdf と render_html_pdf) の中でだけ import する。WeasyPrint (か OS の pango) が無い環境では
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
# 説明書の絵の道。core:manual_image の道と、冊子の <base href="/manual/"> + 断片の images/<名前> が解ける道の
# 2 つと同じ字 (試験が突き合わせる)。
MANUAL_IMAGES_PREFIX = "/manual/images/"

# 帳票が読む物の型。<link rel="stylesheet"> は text/css でないと WeasyPrint が読まない。
CONTENT_TYPES = {
    ".css": "text/css",
    ".ttf": "font/ttf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
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


def render_html_pdf(html):
    """描いた後の HTML の字 (帳票の写しの html など) を WeasyPrint で PDF にし、その bytes を返す。

    取り口と起点と PDF の版は render_pdf と同じ (fetch_local だけを使う取り口・BASE_URL・PDF_VERSION)。
    HTML の中の CSS とフォントとロゴの URL は、PDF を作る時のファイルを読む。WeasyPrint が無ければ PdfUnavailable。
    """
    return _document_maker()(html).write_pdf(pdf_version=PDF_VERSION)


def _render_document(template, context):
    """WeasyPrint の document (ページに割り付けた物) を返す。

    WeasyPrint を読めるかを先に確かめてから、テンプレートを render_html で描く (読めない置き場では描かずに PdfUnavailable)。
    """
    make = _document_maker()
    return make(render_html(template, context))


def _document_maker():
    """HTML の字から WeasyPrint の document (ページに割り付けた物) を作る関数を返す。WeasyPrint が無ければ PdfUnavailable。"""
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

    def make(html):
        return HTML(string=html, base_url=BASE_URL, url_fetcher=LocalFetcher()).render()

    return make


def fetch_local(url):
    """帳票が読むファイルの中身と型を返す。static と media と、説明書の絵 (manual/images/) の下だけを読む。

    BASE_URL の起点の /static/ の下は staticfiles の finders で引き (collectstatic に頼らない)、
    /media/ の下は MEDIA_ROOT の下だけを読む。/manual/images/ の下は、説明書の置き場 (core.manual.manual_dir())
    の images/ の直下の絵だけを、絵の口 (core.manual.manual_image) と同じ制限で読む (名前が . で始まらない・
    .. を含まない・拡張子は .png・.jpg・.jpeg・.webp だけ・symlink の先も images/ の中)。ほかの URL (http・https・file・data など) と、
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
    elif path.startswith(MANUAL_IMAGES_PREFIX):
        file_path = _manual_image_path(path[len(MANUAL_IMAGES_PREFIX) :], path)
    else:
        raise ValueError(f"static と media と manual/images の外は読みません: {path}")

    if not file_path.is_file():
        raise FileNotFoundError(f"ファイルがありません: {path}")
    content_type = CONTENT_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
    return file_path.read_bytes(), content_type


def _manual_image_path(name, path):
    """説明書の絵 (manual/images/<name>) の実体のパスを返す。絵の口 (core.manual.manual_image) と同じ制限で断る。

    置き場が無い・名前の形の外 (images/ の直下でない・. で始まる・.. を含む・拡張子が外れ)・
    images/ の外を指す (resolve() の後に見るので symlink の先も) なら ValueError。無いファイルは fetch_local が断る。
    core.manual は頭で import しない (core.manual が reports を頭で import するので輪になる)。
    """
    from seizou.core.manual import IMAGE_TYPES, manual_dir

    folder = manual_dir()
    if folder is None:
        raise ValueError("説明書の置き場 (BASE_DIR) が無いので絵は読みません")
    name = _checked_relative(name)
    if "/" in name or name.startswith(".") or ".." in name or Path(name).suffix.lower() not in IMAGE_TYPES:
        raise ValueError(f"説明書の絵として読めない名前です: {path}")
    root = (folder / "images").resolve()
    file_path = (root / name).resolve()
    if file_path.parent != root:
        raise ValueError(f"manual/images の外のパスは読みません: {path}")
    return file_path


def _checked_relative(relative):
    """根からの相対のパスを確かめて返す。根の外に出る形 (.. や絶対のパス) は断る。"""
    if not relative or relative.startswith("/") or any(c in relative for c in "\\:\x00"):
        raise ValueError(f"読めないパスの形です: {relative}")
    if any(segment in ("", ".", "..") for segment in relative.split("/")):
        raise ValueError(f"読めないパスの形です: {relative}")
    return relative
