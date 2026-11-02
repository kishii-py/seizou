"""テンプレートに会社の設定と左のメニューを渡す。

メニューは、存在する部品 (seizou.parts.PARTS) の urls.py にある MENU = [("受注", "orders:list"), ...] を順に並べた物。
左のメニューでは、入口を部品の app ごとに区分 (SECTIONS) に分けて見出しを付ける。
入口が 1 つも無い区分は出さない。今開いている画面の入口には active の印を付ける。
"""

from __future__ import annotations

from importlib import import_module, util

from django.db import DatabaseError
from django.urls import NoReverseMatch, reverse

from seizou.parts import PARTS

#: 左のメニューの区分 (見出し, その区分に入る部品の app の名前)。並びはこの順。
SECTIONS = (
    ("はじめに", ("core",)),
    ("受注・出荷", ("orders", "intake", "shipping", "connect", "purchasing", "billing")),
    ("製造", ("workorders", "progress")),
    ("マスター", ("masters",)),
)

#: どの区分にも入らない部品の入口を置く区分の見出し
OTHER_SECTION = "その他"

#: core 自身の入口 (部品の MENU と同じ形)
CORE_MENU = (("はじめに", "core:home"),)


def url_or_none(url_name: str) -> str | None:
    try:
        return reverse(url_name)
    except NoReverseMatch:
        return None


def menu() -> list[dict]:
    """左のメニュー。URL が引ける入口だけを並べる (起動後に部品が増えた時などに 500 にしない)。

    1 つの入口は {"label", "url_name", "url", "app"} の辞書。app は部品の app の名前 (例 "masters")。
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
            items.append({"label": label, "url_name": url_name, "url": url, "app": app})
    return items


def core_menu() -> list[dict]:
    """core 自身の入口 (はじめに)。"""
    items = []
    for label, url_name in CORE_MENU:
        url = url_or_none(url_name)
        if url is not None:
            items.append({"label": label, "url_name": url_name, "url": url, "app": "core"})
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


def sections(items: list[dict]) -> list[dict]:
    """入口を区分に分ける。入口が無い区分は出さない。区分に入らない app は「その他」に置く。"""
    groups = [{"label": label, "items": []} for label, _apps in SECTIONS]
    by_app = {app: group for group, (_label, apps) in zip(groups, SECTIONS) for app in apps}
    other = {"label": OTHER_SECTION, "items": []}
    for item in items:
        by_app.get(item["app"], other)["items"].append(item)
    groups.append(other)
    return [group for group in groups if group["items"]]


def seizou(request) -> dict:
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
    items = [*core_menu(), *part_items]
    mark_active(items, getattr(request, "path", "") or "")
    return {
        "seizou": {
            "company": company,
            "company_name": company_name,
            "theme": theme,
            "menu": part_items,
            "sections": sections(items),
            "search_url": url_or_none("masters:item_list"),
            "manual_url": url_or_none("core:manual"),
        }
    }
