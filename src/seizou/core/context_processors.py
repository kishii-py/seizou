"""テンプレートに会社の設定と左のメニューを渡す。

メニューは、存在する部品 (seizou.parts.PARTS) の urls.py にある MENU = [("受注", "orders:list"), ...] を順に並べた物。
"""

from __future__ import annotations

from importlib import import_module, util

from django.db import DatabaseError

from seizou.parts import PARTS


def menu() -> list[dict]:
    items: list[dict] = []
    for part in PARTS:
        name = f"{part}.urls"
        if util.find_spec(name) is None:
            continue
        module = import_module(name)
        for label, url_name in getattr(module, "MENU", ()):
            items.append({"label": label, "url_name": url_name})
    return items


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
    return {
        "seizou": {
            "company": company,
            "company_name": company_name,
            "theme": theme,
            "menu": menu(),
        }
    }
