"""会社の置き場の説明書の断片 (manual/) を、標準の組 (core/manual_default/) にそろえる (manage.py seizou_manual_defaults の中身)。

出所: 11-14 に、PyPI の 0.1.0 で作った置き場を今の版に上げる通しで見つけた事と、本人の決め 11-14 12:1x。
置き場の manual/ は作った時の断片のままなので、版を上げると、後の版で増えた画面の「使い方」が
「この画面の説明書はまだありません。」になり、前からある断片は古い説明のまま残る。

する事は 2 つ:
- 写す: 標準の組のうち、置き場に同じ名前のファイルが無い断片を写す。
- 替える: 置き場の断片が、前の版の標準と同じ中身 (会社が手を入れていない) なら、新しい標準に替える。
  前の版の中身は、印の一覧 (core/manual_default_history.txt。scripts/manual_history.py が git の履歴から作る) で見分ける。

しない事:
- 会社が直した断片 (どの版の標準とも中身が違う物) は替えない。数えて知らせるだけ。
- 標準の組に無い名前の断片 (会社が足した物) と、絵 (manual/images/) は、数えず、触らない。
- 使い方の画面の部品 (core/manual.py) は読むだけの決まりなので、書く処理はこの module に分けた。表に書く処理は無い。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from django.conf import settings


class ManualDefaultsError(Exception):
    """そろえられないときの、日本語の文つきの誤り。"""


def default_dir() -> Path:
    """標準の組の置き場 (部品の中。pip で入れた版にも入る)。"""
    return Path(__file__).parent / "manual_default"


def history_path() -> Path:
    """前の版の標準の中身の印の一覧 (部品の中。pip で入れた版にも入る)。"""
    return Path(__file__).parent / "manual_default_history.txt"


def site_dir() -> Path:
    """会社の置き場の manual/。settings に BASE_DIR が無ければ ManualDefaultsError。"""
    base = getattr(settings, "BASE_DIR", None)
    if not base:
        raise ManualDefaultsError("会社の置き場が分かりません (settings に BASE_DIR がありません)。置き場の manage.py から打ってください。")
    return Path(base) / "manual"


def content_hash(data: bytes) -> str:
    """断片の中身の印。改行の CRLF を LF にそろえた中身の sha256 (scripts/manual_history.py と同じ作り方)。

    訳: 改行だけが違う物を、同じ中身と見るため。0.1.0 には改行をそろえる設定 (.gitattributes) が無く、Windows の git で
    取り出した seizou から作った置き場の断片は CRLF になる。中身は標準のままなので、手を入れていない物として扱う。
    変えてよい条件 = 印の作り方を変えるときは、一覧を作る道具と一緒に変える (片方だけ変えると、どの断片も替わらなくなる)。
    """
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def load_history() -> dict[str, set[str]]:
    """印の一覧を読む。返す物 = 断片の名前 → その名前の標準が今までに持った中身の印の集まり。一覧が無ければ空の辞書。"""
    path = history_path()
    if not path.is_file():
        return {}
    history: dict[str, set[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, digest = line.partition(" ")
        history.setdefault(name, set()).add(digest)
    return history


def sync() -> dict:
    """置き場の manual/ を標準の組にそろえる。返す物 = {"folder": 置き場の manual/, "copied": 写した名前, "replaced": 替えた名前, "kept": 替えなかった名前}。

    標準の断片ごとに: 置き場に無ければ写す。あって中身が今の標準と同じなら何もしない。違うときは、置き場の中身の印が、
    同じ名前の前の版の印の一覧にあれば新しい標準に替え、無ければ替えない (kept = 会社が直した断片)。
    訳: 置き場の断片は会社の物 (/seizou:manual の命令や人が直す) なので、会社が直した物を命令が書き替えない。
    一方で、会社が触っていない断片を前の版の文のまま残すと、動きの変わった画面の「使い方」が古い説明になる
    (断るのをやめて知らせる形にした操作など)。前の版の標準と同じ中身かどうかで、この 2 つを見分ける。
    印は断片の名前ごとに見る (別の画面の前の版の中身が入っている断片は、会社が置いた物と見て替えない)。
    出所 = 本人の決め 11-14 12:1x (手を入れていない説明書だけを、命令が新しい標準に替える)。
    変えてよい条件 = 本人が決め直したとき。
    """
    folder = site_dir()
    folder.mkdir(parents=True, exist_ok=True)
    history = load_history()
    copied = []
    replaced = []
    kept = []
    for source in sorted(default_dir().glob("*.html")):
        target = folder / source.name
        if not target.exists():
            shutil.copyfile(source, target)
            copied.append(source.name)
            continue
        if not target.is_file():
            continue
        current = target.read_bytes()
        if content_hash(current) == content_hash(source.read_bytes()):
            continue
        if content_hash(current) in history.get(source.name, ()):
            shutil.copyfile(source, target)
            replaced.append(source.name)
        else:
            kept.append(source.name)
    return {"folder": folder, "copied": copied, "replaced": replaced, "kept": kept}
