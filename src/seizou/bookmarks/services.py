"""ブックマークの services (01 の 10.1)。表に書くのはここの関数だけ (R13)。

画面は URL の名前で覚える。登録できるのは、core の画面の目録 (seizou.core.screens.all_screens) にあって、
URL に引数の無い画面だけ (一覧・盤・目録のような画面)。目録は土台を継いだ画面だけなので、関数の view (口) は登録できない。
目録は URL の木を歩いて作るので、process の中で 1 回だけ作って覚える (catalog)。試験は catalog.cache_clear() で作り直す。
"""

from __future__ import annotations

from functools import lru_cache

from django.db import IntegrityError, transaction
from django.db.models import Max

from seizou.core.screens import Screen, all_screens
from seizou.core.templatetags.seizou_icons import menu_icon_name
from seizou.core.views import url_or_none

from .models import Bookmark

#: 1 人が登録できる画面の数の上限
MAX_BOOKMARKS = 50

#: 並びの間隔 (登録の順。次の行はその人の最大 + この値)
POSITION_STEP = 10

MESSAGE_NOT_BOOKMARKABLE = "この画面は登録できません。一覧や目録のような、番号の付かない画面だけ登録できます。"
MESSAGE_LIMIT = "ブックマークは 50 件までです。使わない物を外してから登録してください。"


@lru_cache(maxsize=1)
def catalog() -> dict[str, Screen]:
    """画面の目録を {URL の名前: Screen} にした物 (process の中で 1 回だけ作る)。"""
    return {screen.url_name: screen for screen in all_screens() if screen.url_name}


def screen_of(url_name: str) -> Screen | None:
    """URL の名前の画面 (目録に無ければ None)。"""
    return catalog().get(url_name or "")


def bookmarkable(url_name: str) -> bool:
    """登録できる画面か。目録にあり、URL に引数が無い (path に < が無い) ときだけ真。"""
    screen = screen_of(url_name)
    return screen is not None and "<" not in screen.path


def add_bookmark(user, url_name: str) -> Bookmark:
    """画面を登録する。登録した行を返す。

    登録できない画面は日本語の ValueError。既にあれば、その行を返す (増やさない。控えの名前を目録の今の名前に直す)。
    50 件を越えるときは日本語の ValueError。並びはその人の最大 + 10 (無ければ 10)。
    """
    if not bookmarkable(url_name):
        raise ValueError(MESSAGE_NOT_BOOKMARKABLE)
    label = screen_of(url_name).name[: Bookmark._meta.get_field("label").max_length]
    with transaction.atomic():
        rows = Bookmark.objects.filter(user=user)
        row = rows.filter(url_name=url_name).first()
        if row is not None:
            if row.label != label:
                row.label = label
                row.save(update_fields=["label"])
            return row
        if rows.count() >= MAX_BOOKMARKS:
            raise ValueError(MESSAGE_LIMIT)
        top = rows.aggregate(top=Max("position"))["top"] or 0
        try:
            # 同じ画面の登録が同時に 2 つ走ったときは、後の方が一意の制約に当たる。そのときは先の行を返す。
            with transaction.atomic():
                return Bookmark.objects.create(user=user, url_name=url_name, label=label, position=top + POSITION_STEP)
        except IntegrityError:
            return Bookmark.objects.get(user=user, url_name=url_name)


def remove_bookmark(user, url_name: str) -> None:
    """登録を外す。無ければ何もしない。目録から消えた画面の行も外せる (登録できるかは見ない)。"""
    with transaction.atomic():
        Bookmark.objects.filter(user=user, url_name=url_name).delete()


def is_bookmarked(user, url_name: str) -> bool:
    """その人がその画面を登録しているか (ログインしていない人は偽)。"""
    if not _logged_in(user) or not url_name:
        return False
    return Bookmark.objects.filter(user=user, url_name=url_name).exists()


def bookmarks_for(user) -> list[dict]:
    """その人の登録を並びの順に返す (読むだけ。DB を読むのは 1 回)。

    1 つは {"url_name", "label", "url", "icon", "lost"} の辞書。label は目録の今の名前 (目録に無ければ控えの名前)、
    url は reverse の結果 (作れなければ None)、icon は線の絵の名前 (目録に無ければ list)、
    lost は目録に無いか url が作れないときに真。ログインしていない人は空の並び。
    """
    if not _logged_in(user):
        return []
    screens = catalog()
    items: list[dict] = []
    for row in Bookmark.objects.filter(user=user).order_by("position", "pk"):
        screen = screens.get(row.url_name)
        url = url_or_none(row.url_name) if row.url_name else None
        items.append(
            {
                "url_name": row.url_name,
                "label": screen.name if screen is not None else row.label,
                "url": url,
                "icon": menu_icon_name(row.url_name, screen.app) if screen is not None else "list",
                "lost": screen is None or url is None,
            }
        )
    return items


def _logged_in(user) -> bool:
    return user is not None and bool(getattr(user, "is_authenticated", False))
