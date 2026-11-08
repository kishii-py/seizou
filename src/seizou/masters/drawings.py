"""部品の図面のファイルを、ログインした人にだけ返す口の約束 (部品の図面の設計の決め)。読むだけ。表に書かない。

口 (views.item_drawing)・Item の URL (file_url・display_drawing_url)・図面ビューアの template tag が、ここの 1 か所を呼ぶ。

URL: /masters/items/<pk>/drawing/<filename>
- filename は 2 つの形だけ。original<拡張子> (元のファイル。拡張子は元の名前の拡張子を小文字にした字。無ければ original) と
  preview.png (表示用の PNG)。ほかの字は 404。
- GET の version (版の番号。無ければ今の図面) と page (表示用だけが見る。1 から。既定 1)。読めない値と無い物は 404。
- 返すファイルの名前は表の字 (部品か版の行) だけから決め、要求の字から道を作らない。開くのは storage.open() だけ。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlencode

from django.urls import reverse

ORIGINAL = "original"
PREVIEW = "preview"
PREVIEW_FILENAME = "preview.png"

#: 画面の中で開いてよい形 (Content-Disposition: inline)。ほかは application/octet-stream の attachment (画面の中で開かない)。
INLINE_TYPES = {
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
#: 名前の見出しにそのまま使える品番の字。ほかの字 (日本語・空白・引用符など) が混ざれば FALLBACK_NAME。
_SAFE_PART_NO = re.compile(r"[A-Za-z0-9._-]+")
FALLBACK_NAME = "drawing"
#: GET の version と page の字 (半角の数字だけ。全角や上付きの数字を通さない)
_DIGITS = re.compile(r"[0-9]{1,9}")


@dataclass(frozen=True)
class Source:
    """返す図面の組 (今の図面か版の行)。名前は storage の名前の字。"""

    version_no: int
    drawing: str
    preview: str
    error: str
    page_count: int
    storage: object
    row: object = None  # 版の行 (今の図面なら None)


@dataclass(frozen=True)
class Found:
    """口が返すファイル。"""

    storage: object
    name: str
    content_type: str
    disposition: str


def suffix_of(name):
    """名前の拡張子を小文字で (点を含む。無ければ空の字)。"""
    return PurePosixPath(name or "").suffix.lower()


def original_filename(drawing_name):
    """元のファイルの URL の名前 (original<拡張子>)。"""
    return f"{ORIGINAL}{suffix_of(drawing_name)}"


def page_name(preview_name, page):
    """表示用の n ページ目の名前。1 ページ目は表示用の名前のまま、n ページ目は同じフォルダの <stem>-p<n>.png。"""
    if page == 1:
        return preview_name
    path = PurePosixPath(preview_name)
    return str(path.with_name(f"{path.stem}-p{page}.png"))


def current_source(item):
    return Source(
        version_no=item.drawing_version_no,
        drawing=item.drawing.name or "",
        preview=item.drawing_preview.name or "",
        error=item.drawing_preview_error,
        page_count=item.drawing_page_count,
        storage=item.drawing.storage,
    )


def version_source(version):
    return Source(
        version_no=version.version_no,
        drawing=version.drawing.name or "",
        preview=version.drawing_preview.name or "",
        error=version.drawing_preview_error,
        page_count=version.page_count,
        storage=version.drawing.storage,
        row=version,
    )


def positive_int(raw):
    """1 以上の整数の字なら数、ほかは None。"""
    if raw is None or not _DIGITS.fullmatch(str(raw)):
        return None
    number = int(raw)
    return number if number >= 1 else None


def find_version(item, raw):
    """GET の version の字からこの部品の版の行を引く。読めない・無い番号は None。"""
    number = positive_int(raw)
    if number is None:
        return None
    return item.drawing_versions.filter(version_no=number).first()


def source_for(item, params):
    """GET の version を見て返す組を選ぶ。version が無ければ今の図面。読めない・無い番号は None。"""
    if "version" not in params:
        return current_source(item)
    version = find_version(item, params.get("version"))
    return version_source(version) if version is not None else None


def disposition(part_no, version_no, suffix, *, inline, page=None):
    """名前の見出し。元 = <品番>-v<版><拡張子>、表示用 = <品番>-v<版>-p<n>.png。品番に使えない字があれば drawing。"""
    base = part_no if _SAFE_PART_NO.fullmatch(part_no or "") else FALLBACK_NAME
    name = f"{base}-v{version_no}" + (f"-p{page}" if page is not None else "") + suffix
    return f'{"inline" if inline else "attachment"}; filename="{name}"'


def resolve(item, filename, params):
    """口が返すファイルを決める。返す物が無ければ None (口は 404)。"""
    source = source_for(item, params)
    if source is None or not source.drawing:
        return None
    if filename == original_filename(source.drawing):
        suffix = suffix_of(source.drawing)
        content_type = INLINE_TYPES.get(suffix)
        return Found(
            storage=source.storage,
            name=source.drawing,
            content_type=content_type or "application/octet-stream",
            disposition=disposition(item.part_no, source.version_no, suffix, inline=content_type is not None),
        )
    if filename != PREVIEW_FILENAME or not source.preview:
        return None
    page = positive_int(params.get("page", "1"))
    if page is None or page > source.page_count:
        return None
    name = page_name(source.preview, page)
    if page > 1 and not source.storage.exists(name):
        return None
    return Found(
        storage=source.storage,
        name=name,
        content_type="image/png",
        disposition=disposition(item.part_no, source.version_no, ".png", inline=True, page=page),
    )


def drawing_url(item, kind, *, version=None, page=None):
    """口の URL。kind は original か preview。version は版の行 (今の図面なら None)。

    version と page は要るときだけ GET に付ける (今の図面の 1 ページ目には GET を付けない)。
    """
    if kind == ORIGINAL:
        drawing = version.drawing if version is not None else item.drawing
        filename = original_filename(drawing.name)
    else:
        filename = PREVIEW_FILENAME
    url = reverse("masters:item_drawing", args=[item.pk, filename])
    query = {}
    if version is not None:
        query["version"] = version.version_no
    if kind != ORIGINAL and page is not None and page > 1:
        query["page"] = page
    return f"{url}?{urlencode(query)}" if query else url
