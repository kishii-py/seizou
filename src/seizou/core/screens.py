"""画面の目録 (05 の 10 節)。土台を継承した画面の名前と 1 文と URL を集める。

説明書の命令 (manage.py seizou_screens) と左のメニューの確かめが使う。
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module, util

from django.urls import URLPattern, URLResolver, get_resolver

from seizou.parts import PARTS


@dataclass(frozen=True)
class Screen:
    app: str
    view: str
    name: str
    help: str
    url_name: str
    path: str


def _walk(patterns, namespace="", prefix=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            ns = p.namespace or namespace
            yield from _walk(p.url_patterns, ns, prefix + str(p.pattern))
        elif isinstance(p, URLPattern):
            view_class = getattr(p.callback, "view_class", None)
            if view_class is not None:
                url_name = f"{namespace}:{p.name}" if namespace and p.name else (p.name or "")
                yield view_class, url_name, prefix + str(p.pattern)


def all_screens() -> list[Screen]:
    from seizou.core.views import ScreenMixin

    for part in PARTS:
        name = f"{part}.views"
        if util.find_spec(name) is not None:
            import_module(name)
    screens: list[Screen] = []
    for view_class, url_name, path in _walk(get_resolver().url_patterns):
        if not issubclass(view_class, ScreenMixin):
            continue
        screen_name = getattr(view_class, "screen_name", "")
        if not screen_name:
            continue
        screens.append(
            Screen(
                app=view_class.__module__.split(".")[-2],
                view=view_class.__name__,
                name=screen_name,
                help=getattr(view_class, "screen_help", ""),
                url_name=url_name,
                path="/" + path,
            )
        )
    return screens
