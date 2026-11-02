"""線の絵 (アイコン) の template tag。外の library を使わず、ここに 24×24 の線で描いた物だけを置く。

使い方: {% load seizou_icons %}{% icon "home" %} / {% icon "search" "w-4 h-4" %}
名前が無ければ空の枠を出す (壊さない)。
"""

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

# 24×24、線の太さ 1.8、currentColor。形は簡素な線だけ。
PATHS = {
    "home": '<path d="M3 11.5 12 4l9 7.5"/><path d="M5 10.5V20h14v-9.5"/><path d="M10 20v-5h4v5"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    "box": '<path d="M12 3 4 7v10l8 4 8-4V7l-8-4z"/><path d="M4 7l8 4 8-4"/><path d="M12 11v10"/>',
    "users": '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19a5.5 5.5 0 0 1 11 0"/><circle cx="16.5" cy="9" r="2.5"/><path d="M15.5 14.5a4.5 4.5 0 0 1 5 4.5"/>',
    "clipboard": '<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4.5V3h6v1.5"/><path d="M9 11h6M9 15h6"/>',
    "truck": '<path d="M3 7h11v9H3z"/><path d="M14 10h4l3 3v3h-7"/><circle cx="7" cy="18" r="1.8"/><circle cx="17" cy="18" r="1.8"/>',
    "inbox": '<path d="M4 13V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v7"/><path d="M4 13h4l2 3h4l2-3h4v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/>',
    "factory": '<path d="M3 20V9l6 4V9l6 4V5h6v15z"/><path d="M8 16h2M13 16h2"/>',
    "activity": '<path d="M3 12h4l2.5-6 4 12 2.5-6H21"/>',
    "cart": '<path d="M3 4h2l2.4 11h11.2L21 7H6"/><circle cx="9" cy="19" r="1.5"/><circle cx="17" cy="19" r="1.5"/>',
    "file": '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h6"/>',
    "bell": '<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/>',
    "logout": '<path d="M10 4H5v16h5"/><path d="M14 8l4 4-4 4"/><path d="M9 12h9"/>',
    "login": '<path d="M14 4h5v16h-5"/><path d="M10 8l4 4-4 4"/><path d="M4 12h10"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "pencil": '<path d="m4 20 4-1L19 8l-3-3L5 16l-1 4z"/><path d="m14 7 3 3"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/>',
    "chevron-right": '<path d="m9 6 6 6-6 6"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "calendar": '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M8 3v4M16 3v4"/>',
    "check": '<path d="m5 12 4.5 4.5L19 7"/>',
    "alert": '<path d="M12 4 2.5 20h19z"/><path d="M12 10v4M12 17h.01"/>',
    "book": '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M4 21V5M8 7h7"/>',
    "image": '<rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="m4 17 5-4 3 2.5 3-3 5 4.5"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.6 2.2c-.8.4-1.1 1-1.1 1.8"/><path d="M12 17h.01"/>',
    "x": '<path d="M6 6l12 12M18 6 6 18"/>',
    "download": '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M4 19h16"/>',
    "arrow-left": '<path d="M19 12H5"/><path d="m11 6-6 6 6 6"/>',
    "user": '<circle cx="12" cy="8" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>',
    "eye": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-up": '<path d="m6 15 6-6 6 6"/>',
    "filter": '<path d="M3 5h18l-7 8v6l-4-2v-4z"/>',
    "yen": '<path d="m7 4 5 8 5-8"/><path d="M12 12v8"/><path d="M8 13h8M8 17h8"/>',
    "external": '<path d="M14 4h6v6"/><path d="M20 4 10 14"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>',
    "building": '<rect x="4" y="3" width="16" height="18" rx="1"/><path d="M8 7h2M14 7h2M8 11h2M14 11h2M8 15h2M14 15h2M10 21v-3h4v3"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/><path d="M12 15v2"/>',
}

#: 部品の app ごとの絵 (左のメニューで使う)
APP_ICONS = {
    "core": "home",
    "masters": "box",
    "orders": "clipboard",
    "workorders": "factory",
    "progress": "activity",
    "shipping": "truck",
    "billing": "file",
    "connect": "inbox",
    "purchasing": "cart",
    "intake": "file",
}


def svg(name: str, css: str = "w-5 h-5") -> str:
    body = PATHS.get(name, "")
    return format_html(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" class="{} shrink-0" aria-hidden="true">{}</svg>',
        css,
        mark_safe(body),  # noqa: S308 - 上の辞書の固定の文字列だけ
    )


@register.simple_tag
def icon(name: str, css: str = "w-5 h-5"):
    return svg(name, css)


@register.simple_tag
def app_icon(app_label: str, css: str = "w-5 h-5"):
    return svg(APP_ICONS.get(app_label, "list"), css)
