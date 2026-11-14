"""標準の説明書の断片 (core/manual_default/) のうち、会社の置き場の manual/ に無い物を写す (manage.py seizou_manual_defaults の中身)。

出所: 11-14 に、PyPI の 0.1.0 で作った置き場を今の版に上げる通しで見つけた事。置き場の manual/ は作った時の断片のままなので、
後の版で増えた画面の「使い方」が「この画面の説明書はまだありません。」になる。置き場を作る命令 (seizou new) は同じ写し方を
するが、版を上げた置き場のための口が無かった (手引きの cp -n は Windows に無く、pip で入れた版では標準の組の道も違う)。

- 写すのは、置き場に同じ名前のファイルが無い断片だけ。既にある断片は、会社が直した物か前の版の標準の物なので、上書きしない。
- 標準の組に無い名前の断片 (会社が足した物) は、数えず、触らない。
- 使い方の画面の部品 (core/manual.py) は読むだけの決まりなので、書く処理はこの module に分けた。表に書く処理は無い。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings


class ManualDefaultsError(Exception):
    """写せないときの、日本語の文つきの誤り。"""


def default_dir() -> Path:
    """標準の組の置き場 (部品の中。pip で入れた版にも入る)。"""
    return Path(__file__).parent / "manual_default"


def site_dir() -> Path:
    """会社の置き場の manual/。settings に BASE_DIR が無ければ ManualDefaultsError。"""
    base = getattr(settings, "BASE_DIR", None)
    if not base:
        raise ManualDefaultsError("会社の置き場が分かりません (settings に BASE_DIR がありません)。置き場の manage.py から打ってください。")
    return Path(base) / "manual"


def copy_missing() -> dict:
    """標準の組のうち、置き場の manual/ に無い断片を写す。返す物 = {"folder": 置き場の manual/, "copied": 写した名前, "different": 標準と字が違う名前}。

    置き場に manual/ が無ければ作る。同じ名前のファイルがあれば写さない (上書きしない)。
    different は、写す前から置き場にあって、標準の今の断片と中身 (bytes) が違う物の名前 (会社が直した物か、前の版の標準の物)。
    訳: 置き場の断片は会社の物 (/seizou:manual の命令や人が直す) なので、版を上げる命令が黙って書き替えない。
    標準の新しい文に替えるかは、会社が断片ごとに決める (替えるなら、置き場のファイルを消してからもう一度写す)。
    出所 = 置き場を作る命令の写し方 (seizou.cli.copy_manual_default。同じ名前は上書きしない)。変えてよい条件 = 本人が決め直したとき。
    """
    folder = site_dir()
    folder.mkdir(parents=True, exist_ok=True)
    copied = []
    different = []
    for source in sorted(default_dir().glob("*.html")):
        target = folder / source.name
        if not target.exists():
            shutil.copyfile(source, target)
            copied.append(source.name)
        elif target.is_file() and target.read_bytes() != source.read_bytes():
            different.append(source.name)
    return {"folder": folder, "copied": copied, "different": different}
