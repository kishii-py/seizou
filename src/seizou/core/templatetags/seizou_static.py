"""静的なファイルの URL に中身の印を付ける template tag の library。

使い方は Django の static と同じ: `{% load seizou_static %}{% static 'css/app.css' %}` → `/static/css/app.css?v=<10 字>`。
`{% static '…' as 変数 %}` も同じ URL を変数に入れる。tag の名前を Django と同じ static にしてあるので、
テンプレートは load の行の 1 語 (static → seizou_static) を替えるだけで、`{% static … %}` の行を変えずに済む。
表を読まない。表に書かない。Python から static の URL を作る所 (core.media.favicon の static("favicon.svg")) は
Django の static() のままで、印を付けない。
"""

from __future__ import annotations

import hashlib
import os
import stat

from django import template
from django.contrib.staticfiles import finders
from django.core.exceptions import SuspiciousFileOperation
from django.templatetags.static import StaticNode

register = template.Library()

#: 印の長さ (中身の SHA-256 の 16 進の頭の字数)
STAMP_LENGTH = 10

#: 印の控え (process の中だけ)。ファイルの絶対パス → ((更新時刻の ns, 大きさ), 印)。
#: 鍵ごとに値は 1 つで、別の thread が同時に作っても同じ印になるので、錠を掛けない。
_stamps: dict[str, tuple[tuple[int, int], str]] = {}


def stamped_static(path):
    """Django の static の URL の後ろに、中身の印 `?v=<10 字>` を付けて返す (URL に既に `?` があれば `&v=<10 字>`)。

    印は file_stamp (中身の SHA-256 の頭 10 字)。ファイルが見つからない時は印を付けず、Django の URL のまま返す (断らない。例外も出さない)。

    訳: 版を上げた後 (JS か CSS を直した後) に、ブラウザが手元の控えの前の版の JS と CSS を使い、新しい画面と食い違わないようにするため。URL が版で変わらないと、ブラウザは控えを使い続ける。中身が変わると印が変わって URL が変わり、ブラウザは新しいファイルを取りに行く。置き場の settings.py (seizou new が会社ごとに作った写し) を変えずに済むよう、Django の ManifestStaticFilesStorage (ファイル名に印を付ける形) でなく、テンプレートの tag で URL の後ろに付ける。nginx で配る置き場でも、`?` の後ろはファイルの探し方に関わらないので、今の設定のまま動く。出所 = 510 回目の記録の残り (調整の小窓の JS を直した後に、前の JS がブラウザに残って新しい画面と食い違った) と本人の決め 11-12 23:5x。変えてよい条件 = 置き場の設定で配り方を変える形 (ManifestStaticFilesStorage など) にする時。
    """
    url = StaticNode.handle_simple(path)
    stamp = file_stamp(path)
    if not stamp:
        return url
    return f"{url}{'&' if '?' in url else '?'}v={stamp}"


def file_stamp(path):
    """static の相対のパスのファイルの印 (中身の SHA-256 の 16 進の頭 10 字) を返す。見つからない・ファイルでない・読めない時は空の字。

    ファイルは staticfiles の finders で探す (collectstatic に頼らない。帳票の core.reports.fetch_local と同じ探し方)。
    根の外を指すパス (finders が SuspiciousFileOperation を出す物) も、見つからない物として空の字を返す。
    印は process の中で控え、鍵は (ファイルの絶対パス, 更新時刻, 大きさ)。更新時刻は ns で見るので、同じ秒の中で直しても、
    時刻か大きさが変われば作り直す (開発サーバーで JS を直した時に、立て直さなくても印が変わる)。
    """
    try:
        found = finders.find(path)
    except SuspiciousFileOperation:
        return ""
    if not found:
        return ""
    file_path = os.path.abspath(found)
    try:
        info = os.stat(file_path)
        if not stat.S_ISREG(info.st_mode):
            return ""
        key = (info.st_mtime_ns, info.st_size)
        cached = _stamps.get(file_path)
        if cached is not None and cached[0] == key:
            return cached[1]
        stamp = _read_stamp(file_path)
    except OSError:
        return ""
    _stamps[file_path] = (key, stamp)
    return stamp


def _read_stamp(file_path):
    """ファイルの中身の SHA-256 の 16 進の頭 STAMP_LENGTH 字を返す (中身を読むのはここだけ)。"""
    with open(file_path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()[:STAMP_LENGTH]


class StampedStaticNode(StaticNode):
    """Django の StaticNode と同じ書き方 (そのまま出す形と `as 変数`) で、URL に中身の印を付ける (stamped_static)。"""

    def url(self, context):
        return stamped_static(self.path.resolve(context))


@register.tag("static")
def do_static(parser, token):
    """`{% static path [as varname] %}`。Django の static と同じ書き方で、URL の後ろに中身の印を付ける。"""
    return StampedStaticNode.handle_token(parser, token)
