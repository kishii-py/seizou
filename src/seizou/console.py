"""端末への出力で、文字コードで表せない字を「?」にして書く守り (標準の library だけで書き、Django を読まない)。

命令 seizou (cli.py) と、置き場の manage.py の命令が同じ関数を呼ぶ。manage.py の命令では、core の app の読み込み
(CoreConfig.ready) と、雛形の manage.py (Django を読む前) の 2 か所から呼ぶ。cli.py は Django と core を
import しない決まりなので、この module を core の下に置かない。
"""

from __future__ import annotations

import sys


def replace_unencodable_output(streams=None) -> None:
    """標準出力と標準エラーを、その文字コードで表せない字を「?」にして書く形にする。文字コードそのものは変えない。

    streams を渡すと、その stream だけを直す (試験のため)。reconfigure を持たない物 (None・io.StringIO・
    差し替えた物) は飛ばす。何度呼んでも同じ形になる。直せない stream (閉じた物など) は飛ばし、誤りを外に出さない。

    訳: Windows の管か file に向けた出力 (英語の Windows は cp1252、日本語の Windows は cp932) で、日本語や
    cp932 に無い字 (「—」・絵文字) を書いた所で、命令が UnicodeEncodeError で止まらないようにするため。
    文字コードは変えない (UTF-8 に固定すると、cp932 の端末で字が化ける)。標準エラーの既定の形 (\\uXXXX に置き換える)
    も「?」になる (N392 の守りと同じ形)。誤りを外に出さないのは、この関数がすべての Django の process
    (runserver・gunicorn・MCP・試験) の起動で走り、守りが起動を止めると、守らない時より重いため。
    出所 = 命令 seizou の守り (開発の記録 docs/nights/20261111/N392.md) と、置き場の manage.py seizou_doctor が
    GitHub Actions の windows-latest (cp1252) で止まった結果 (11-11 02:2x。依頼書 N399、開発の記録 docs/nights/20261111/N399.md)。
    変えてよい条件 = 支える Python のすべてが、どの OS でも標準出力と標準エラーを UTF-8 で書くと確かめたとき
    (そのときは守りが要らない)、または出せない字の出し方 (今は「?」) を本人が決め直したとき。
    """
    for stream in (sys.stdout, sys.stderr) if streams is None else streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except Exception:
            # 閉じた stream (ValueError) や、reconfigure を持つ別の物の誤り。守れない stream はそのまま書く
            continue
