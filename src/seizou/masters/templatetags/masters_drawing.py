"""図面ビューアの template tag (読むだけ。表に書かない)。

{% drawing_viewer as viewer %}: 画面の部品 (context の object) と GET の version・page・compare から、
見る版・ページ・絵の URL (ログインした人にだけ返す口)・版の並びを決める。URL と名前の約束は masters.drawings の 1 か所。
読めない version・page は今の図面の 1 ページ目にする (画面は誤りにしない。口は 404)。
"""

from urllib.parse import urlencode

from django import template
from django.urls import reverse

from seizou.masters import drawings

register = template.Library()

#: 絵として出す元の拡張子 (表示用が無いとき)。Item.display_drawing と同じ。
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")


def _viewer_url(item, **query):
    url = reverse("masters:item_drawing_viewer", args=[item.pk])
    query = {key: value for key, value in query.items() if value}
    return f"{url}?{urlencode(query)}" if query else url


def _image(item, source, page):
    """組 (今の図面か版) の絵の URL と、ページの数。絵が無ければ (None, 1)。"""
    if not source.drawing or source.error:
        return None, 1
    if source.preview:
        return drawings.drawing_url(item, drawings.PREVIEW, version=source.row, page=page), source.page_count
    if drawings.suffix_of(source.drawing) in _IMAGE_SUFFIXES:
        return drawings.drawing_url(item, drawings.ORIGINAL, version=source.row), 1
    return None, 1


def _mode(source, image_url):
    if not source.drawing:
        return "none"
    if image_url:
        return "image"
    if drawings.suffix_of(source.drawing) == ".pdf":
        return "pdf"
    return "file"


@register.simple_tag(takes_context=True)
def drawing_viewer(context):
    item = context["object"]
    params = context["request"].GET
    current = drawings.current_source(item)
    version = drawings.find_version(item, params.get("version"))
    source = drawings.version_source(version) if version is not None else current
    version_no = version.version_no if version is not None else None

    page_count = source.page_count if source.preview else 1
    page = drawings.positive_int(params.get("page")) or 1
    if page > page_count:
        page = 1
    image_url, _ = _image(item, source, page)
    mode = _mode(source, image_url)
    if mode != "image":
        page, page_count = 1, 1

    compare_url = None
    current_image_url = None
    compare = False
    if version is not None and mode == "image":
        current_page = page if current.preview and page <= current.page_count else 1
        current_image_url, _ = _image(item, current, current_page)
        if current_image_url:
            compare = params.get("compare") == "1"
            compare_url = _viewer_url(item, version=version_no, page=page if page > 1 else None, compare="1")

    def page_url(number):
        return _viewer_url(
            item, version=version_no, page=number if number > 1 else None, compare="1" if compare else None
        )

    rows = [
        {
            "no": current.version_no,
            "is_current": True,
            "created_at": None,
            "note": "",
            "link": _viewer_url(item),
            "viewing": version is None,
        }
    ]
    for row in item.drawing_versions.all():
        rows.append(
            {
                "no": row.version_no,
                "is_current": False,
                "created_at": row.created_at,
                "note": row.note,
                "link": _viewer_url(item, version=row.version_no),
                "viewing": version is not None and row.pk == version.pk,
            }
        )

    return {
        "current_no": current.version_no,
        "version_no": version_no,
        "viewing_label": f"版 {version_no}" if version is not None else f"版 {current.version_no} (今の図面)",
        "current_label": f"版 {current.version_no} (今の図面)",
        "mode": mode,
        "image_url": image_url,
        "download_url": drawings.drawing_url(item, drawings.ORIGINAL, version=version) if source.drawing else None,
        "error": source.error,
        "page": page,
        "page_count": page_count,
        "prev_url": page_url(page - 1) if page > 1 else None,
        "next_url": page_url(page + 1) if page < page_count else None,
        "compare": compare,
        "compare_url": compare_url,
        "single_url": _viewer_url(item, version=version_no, page=page if page > 1 else None) if compare else None,
        "current_image_url": current_image_url if compare else None,
        "rows": rows,
        "version_count": len(rows) - 1,
        "restore_url": reverse("masters:item_drawing_restore", args=[item.pk]) if len(rows) > 1 else None,
    }
