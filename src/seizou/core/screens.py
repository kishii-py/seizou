"""画面の目録 (05 の 10 節)。土台を継承した画面の名前と 1 文と URL を集める。

説明書の命令 (manage.py seizou_screens) と左のメニューの確かめが使う。
画面の class の docstring のユースケース (使う人 / いつ / 何のために / 次にすること) を usecase として持つ。MCP と AI チャットの list_screens が返す。
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from importlib import import_module, util

from django.urls import URLPattern, URLResolver, get_resolver

from seizou.parts import PARTS

#: ユースケースの 4 つの鍵。画面の class の docstring に、この順で書く (CLAUDE.md の書き方の決まり)
USECASE_KEYS = ("使う人", "いつ", "何のために", "次にすること")


@dataclass(frozen=True)
class Screen:
    app: str
    view: str
    name: str
    help: str
    url_name: str
    path: str
    usecase: str = ""  # 使う人 / いつ / 何のために / 次にすること (docstring に無ければ空)


def usecase_from(doc: str | None) -> str:
    """docstring の字から、ユースケースの段落を 1 行にした字を返す。

    字下げを外し (inspect.cleandoc)、空行で区切った段落を順に見て、最初の鍵 (使う人) のある段落の
    鍵から段落の終わりまでを、空白を 1 つにそろえて返す。例外を投げない (None も空の字)。
    docstring が無い・鍵が無い画面は空の字 (落とさない)。段落は空行で区切る。
    """
    text = inspect.cleandoc(doc or "")
    marker = f"{USECASE_KEYS[0]}:"
    for paragraph in re.split(r"\n\s*\n", text):
        line = " ".join(paragraph.split())
        start = line.find(marker)
        if start >= 0:
            return line[start:]
    return ""


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
                usecase=usecase_from(view_class.__doc__),
            )
        )
    return screens


def usecase_rows(usecase: str) -> list[tuple[str, str]]:
    """usecase の 1 行の字 (使う人: … / いつ: … / 何のために: … / 次にすること: …) を、USECASE_KEYS の順の (鍵, 値) の並びにする。鍵の無い・値の空の物は入れない。例外を投げない。

    鍵の字で切る (行の頭か「 / 」の後の「鍵:」だけを鍵と見る)。値の中の「 / 」は、次に鍵が続かない限り値に残す。
    """
    keys = "|".join(re.escape(key) for key in USECASE_KEYS)
    pieces = re.split(rf"(?:^|\s*/\s*)({keys}):\s*", usecase or "")
    found: dict[str, str] = {}
    for key, value in zip(pieces[1::2], pieces[2::2]):
        value = value.strip()
        if value.endswith("/"):
            value = value[:-1].rstrip()
        if value and key not in found:
            found[key] = value
    return [(key, found[key]) for key in USECASE_KEYS if key in found]
