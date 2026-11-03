"""テンプレートに会社の設定と左のメニューを渡す。

メニューは、存在する部品 (seizou.parts.PARTS) の urls.py にある MENU = [("受注", "orders:list"), ...] を順に並べた物。
左のメニューでは、入口を部品の app ごとに区分 (SECTIONS) に分けて見出しを付ける。
入口が 1 つも無い区分は出さない。今開いている画面の入口には active の印を付ける。
"""

from __future__ import annotations

from importlib import import_module, util

from django.db import DatabaseError
from django.urls import NoReverseMatch, reverse

from seizou.core.templatetags.seizou_icons import menu_icon_name
from seizou.parts import PARTS

#: 左のメニューの区分 (見出し, その区分に入る部品の app の名前)。並びはこの順。
SECTIONS = (
    ("ダッシュボード", ("core",)),
    ("受注管理", ("orders", "intake", "connect")),
    ("協力企業管理", ("purchasing",)),
    ("製造", ("workorders", "progress")),
    ("出荷と請求", ("shipping", "billing")),
    ("マスター", ("masters", "accounts")),
    ("文書", ("manuals", "attachments")),
)

#: どの区分にも入らない部品の入口を置く区分の見出し
OTHER_SECTION = "その他"

#: core 自身の入口 (部品の MENU と同じ形)
CORE_MENU = (("ダッシュボード", "core:home"), ("検索", "core:search"))


def url_or_none(url_name: str) -> str | None:
    try:
        return reverse(url_name)
    except NoReverseMatch:
        return None


def entry(label: str, url_name: str, url: str, app: str) -> dict:
    return {"label": label, "url_name": url_name, "url": url, "app": app, "icon": menu_icon_name(url_name, app)}


def menu() -> list[dict]:
    """左のメニュー。URL が引ける入口だけを並べる (起動後に部品が増えた時などに 500 にしない)。

    1 つの入口は {"label", "url_name", "url", "app", "icon"} の辞書。app は部品の app の名前 (例 "masters")、
    icon は線の絵の名前 (seizou_icons)。
    """
    items: list[dict] = []
    for part in PARTS:
        name = f"{part}.urls"
        if util.find_spec(name) is None:
            continue
        module = import_module(name)
        app = part.rsplit(".", 1)[-1]
        for label, url_name in getattr(module, "MENU", ()):
            url = url_or_none(url_name)
            if url is None:
                continue
            items.append(entry(label, url_name, url, app))
    return items


def core_menu() -> list[dict]:
    """core 自身の入口 (ダッシュボード・検索)。"""
    items = []
    for label, url_name in CORE_MENU:
        url = url_or_none(url_name)
        if url is not None:
            items.append(entry(label, url_name, url, "core"))
    return items


def mark_active(items: list[dict], path: str) -> None:
    """今開いている画面の入口に active の印を付ける。

    URL の先頭が合う入口のうち、いちばん長い物を 1 つだけ選ぶ (/ は同じ道のときだけ)。
    """
    best = None
    for item in items:
        url = item["url"]
        hit = path == url if url == "/" else path.startswith(url)
        if hit and (best is None or len(url) > len(best["url"])):
            best = item
    for item in items:
        item["active"] = item is best


def custom_sections() -> list[dict]:
    """会社の設定 menu (左のメニューの括り)。1 つ = {"label", "apps", "screens"}。形の違う物は捨てる。空なら []。

    決め 11-03 (02 の 8 節の候補 4): 括りは会社ごとに答えの YAML で決め、同じ画面を 2 つの括りに出せる。
    """
    from seizou.core import conf

    value = conf.setting("menu")
    if not isinstance(value, list):
        return []
    result = []
    for raw in value:
        if not isinstance(raw, dict) or not isinstance(raw.get("label"), str) or not raw["label"].strip():
            continue
        apps = [a for a in (raw.get("apps") or []) if isinstance(a, str)]
        screens = [u for u in (raw.get("screens") or []) if isinstance(u, str)]
        result.append({"label": raw["label"].strip(), "apps": apps, "screens": screens})
    return result


def sections(items: list[dict]) -> list[dict]:
    """入口を区分に分ける。入口が無い区分は出さない。区分に入らない app は「その他」に置く。

    1 区分 = {"label", "items", "show_label"}。入口が 1 つで見出しと同じ字なら、見出しは出さない (show_label が偽)。
    会社の設定 menu があれば、その括り (app か画面の URL の名前で選ぶ。同じ入口を 2 つの括りに出せる) で分け、
    ダッシュボードの括り (core) はいつも先頭に置く。
    """
    custom = custom_sections()
    if custom:
        groups = [{"label": SECTIONS[0][0], "items": [item for item in items if item["app"] == "core"]}]
        placed = {id(item) for item in groups[0]["items"]}
        for section in custom:
            chosen = [item for item in items if item["app"] in section["apps"] or item["url_name"] in section["screens"]]
            placed.update(id(item) for item in chosen)
            groups.append({"label": section["label"], "items": chosen})
        groups.append({"label": OTHER_SECTION, "items": [item for item in items if id(item) not in placed]})
    else:
        groups = [{"label": label, "items": []} for label, _apps in SECTIONS]
        by_app = {app: group for group, (_label, apps) in zip(groups, SECTIONS) for app in apps}
        other = {"label": OTHER_SECTION, "items": []}
        for item in items:
            by_app.get(item["app"], other)["items"].append(item)
        groups.append(other)
    groups = [group for group in groups if group["items"]]
    for group in groups:
        only = group["items"][0] if len(group["items"]) == 1 else None
        group["show_label"] = not (only is not None and only["label"] == group["label"])
    return groups


def seizou(request) -> dict:
    from seizou.core import access
    from seizou.core import services

    try:
        company = services.company()
        company_name = company.name
        theme = company.theme or "light"
    except DatabaseError:
        company = None
        company_name = "seizou"
        theme = "light"
    part_items = menu()
    part_items = access.visible_menu(getattr(request, "user", None), part_items)
    items = [*core_menu(), *part_items]
    mark_active(items, getattr(request, "path", "") or "")
    return {
        "seizou": {
            "company": company,
            "company_name": company_name,
            "theme": theme,
            "menu": part_items,
            "sections": sections(items),
            "search_url": url_or_none("core:search"),
            "manual_url": url_or_none("core:manual"),
            # 存在する部品の短い名前 (例 "masters")。テンプレートが {% if "attachments" in seizou.parts %} で、
            # 入っている部品の枠だけを include するために使う。
            "parts": part_names(),
            "hide_money": access.hides_money(getattr(request, "user", None)),  # 金額の閲覧が無い人 (on) に金額の枡を隠す印
        }
    }


def part_names() -> list[str]:
    """存在する部品 (seizou.parts.PARTS) の app の短い名前の並び (例 ["core", "masters", ...])。"""
    return [part.rsplit(".", 1)[-1] for part in PARTS]
