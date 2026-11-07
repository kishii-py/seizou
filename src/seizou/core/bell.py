"""上の帯の知らせの鈴 (受け渡しの seizou-部品の6案-20261107/指示書.md の 4。本人の決め 11-07 18:2x〜18:4x)。読むだけ (書くのは services.open_bell)。

鈴は「知っておいた方がいいこと」の印。新しく届いて、まだ見ていない物だけを赤い数で数え、鈴を開くと印が消える (本人の言葉
「やることというか知っておいた方がいいことだね。見たらなくなる感じ」)。「今日やること」の件数は鈴に入れない (ダッシュボードと左のメニューに残す)。
CLAUDE.md の「知らせの強さ」の 5 つの段とは別の受け箱で、6 つ目の段は作らない (ログインの時に 1 回出る知らせも今のまま)。

数え方: 1 人 1 行の「鈴を開いた日時」(core.models.BellOpened。無ければ登録した日時) より後に起きた知らせの数。
知らせの元 (SOURCES) は部品の services の読むだけの 2 つの関数:
  bell_count(user, since) -> int            since より後の知らせの数 (件数だけ。上の帯は全画面で描くので軽くする)
  bell_notices(user, *, limit) -> list[dict]  箱に並べる最近の知らせ (新しい順に limit 件)。1 件 = {"when", "title", "text", "url", "icon"}
見られない物 (左のメニューと同じ判定 = access.can_open) は部品の側で外す。部品が入っていない会社はその元を飛ばす。
core は部品の services だけを呼ぶ (CLAUDE.md の置き場)。module は名前で遅く読む (core が部品を import しない)。
"""

from __future__ import annotations

from importlib import import_module

from django.apps import apps
from django.db import DatabaseError

from seizou.core import services

#: 知らせの元 (部品の短い名前, services の module)。並びは箱の並びに関わらない (日時で並べ直す)
SOURCES = (("connect", "seizou.connect.services"), ("feedback", "seizou.feedback.services"))
#: 箱に並べる数
LIMIT = 10
#: 赤い数の上限 (超えたら「99+」)
BADGE_MAX = 99


def _sources():
    """入っている部品の元の module の並び。"""
    return [import_module(module_name) for part, module_name in SOURCES if apps.is_installed(f"seizou.{part}")]


def unread_count(user) -> int:
    """赤い数 = 鈴を開いた日時より後の知らせの数 (件数だけ数える)。未ログインは 0。表が読めなければ 0 (偽の数を置かず、印を出さない)。"""
    try:
        since = services.bell_opened_at(user)
        if since is None:
            return 0
        return sum(module.bell_count(user, since) for module in _sources())
    except DatabaseError:
        return 0


def badge_text(count) -> str:
    """赤い数の字 (BADGE_MAX を超えたら「99+」。0 以下は空の字 = 印を出さない)。"""
    if count <= 0:
        return ""
    return f"{BADGE_MAX}+" if count > BADGE_MAX else str(count)


def notices(user, *, since=None, limit=LIMIT) -> list[dict]:
    """箱に並べる知らせ (新しい順に limit 件)。1 件に new (since より後 = まだ見ていない) を足して返す。

    since は鈴を開く前の日時を呼び手が渡す (開いた後の日時で比べると全部が古い扱いになる)。無ければ今の日時 (services.bell_opened_at)。
    未ログインは空の並び。
    """
    if since is None:
        since = services.bell_opened_at(user)
    if since is None:
        return []
    rows = []
    for module in _sources():
        rows.extend(module.bell_notices(user, limit=limit))
    rows.sort(key=lambda row: row["when"], reverse=True)
    rows = rows[:limit]
    for row in rows:
        row["new"] = row["when"] > since
    return rows
