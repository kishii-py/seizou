"""決まりの番号の「?」が開く説明 (rule_help) と、RULES.md の行の読み方 (core.views の package)。"""

from __future__ import annotations

import re
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_GET

from seizou.core import checks


#: 決まりの一覧 (作品のリポジトリの根の RULES.md。src/seizou/core/views/rules.py の 4 つ上。配布の wheel には入らない)
RULES_MD = Path(__file__).resolve().parents[4] / "RULES.md"
#: 決まりの番号の形 (R と 2 桁)
RULE_CODE = re.compile(r"^R[0-9]{2}$")


def _rules_md() -> Path:
    """今の RULES_MD。呼ぶ時に package (seizou.core.views) の名前を読む。

    試験は seizou.core.views.RULES_MD を差し替える (RULES.md の無い置き場の形)。この module の RULES_MD を直に読むと、
    package の名前の差し替えが届かないため (11-08 に views.py を package に分けた時の約束)。
    """
    from seizou.core import views

    return views.RULES_MD


def rule_line(code: str) -> dict | None:
    """RULES.md の表の、番号が code の行。{"code", "text" (2 つ目の欄。` を外す), "section" (行の上の ## の見出しの字)}。

    番号の形が違う・RULES.md が無いか読めない・行が無ければ None。読むだけ。
    行の読み方は tests/test_r14_no_raw_sql.py の _rules_md_text と同じ (「| R01 |」で始まる行の 2 つ目の欄)。
    """
    if not isinstance(code, str) or not RULE_CODE.match(code):
        return None
    try:
        text = _rules_md().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    section = ""
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        elif line.startswith(f"| {code} |"):
            return {"code": code, "text": line.split("|")[2].strip().replace("`", ""), "section": section}
    return None


#: 設定の行の 2 文目 (会社の設定の値ごと)。R01・R04・R06・R07 のどれにも合う字にする (R04 は「超える」でないため)
RULE_SETTING_EFFECTS = {
    "stop": "この決まりに当たると保存しません。",
    "warn": "黄の知らせと「このまま保存」のボタンが出ます。",
    "off": "知らせずに保存します。",
}


def rule_setting_line(code: str) -> str:
    """決まりの番号の「?」の説明に出す、この会社の今の設定の 1 行。設定の鍵の無い番号は空の字。

    例「この会社の設定「受注より多い出荷 (R01・R04)」は「止める」です。この決まりに当たると保存しません。」。
    鍵は検査の層の登録 (checks.setting_key_for) から、見出しと値の字は設定の画面の SETTING_LABELS から引く。
    鍵が SETTING_LABELS に無ければ値を読まない (conf.setting は知らない鍵で KeyError)。値が表に無い字のときも空。
    Check.current_mode は使わない (知らない値で ValueError を投げ、説明が 500 になるため)。読むだけ。
    """
    key = checks.setting_key_for(code)
    if not key:
        return ""
    # settings_screen は module の頭で core.views を import するので、ここで import する (循環を避ける)
    from seizou.core import conf
    from seizou.core.settings_screen import SETTING_LABELS

    label = SETTING_LABELS.get(key)
    if label is None:
        return ""
    title, words = label
    value = conf.setting(key)
    if not isinstance(value, str) or value not in words or value not in RULE_SETTING_EFFECTS:
        return ""
    return f"この会社の設定「{title}」は「{words[value]}」です。{RULE_SETTING_EFFECTS[value]}"


@login_required
@require_GET
def rule_help(request, code):
    """赤の知らせの決まりの番号の横の「?」が開く説明。番号の形が違えば 404。

    htmx の要求 (画面の全体の右上の知らせと、盤やメモの書く枠の帯の「?」。同じタブの小窓で開く) には小窓の中身
    (core/_rule_help.html) を返す。htmx でない要求 (小窓の入力の赤い帯の「?」が開く新しいタブ。作者の決め。
    同じ小窓に説明を入れると入れた値が消えるため) には画面の全体 (core/rule_help.html。土台の画面の中に同じ中身の箱を置き、
    小窓の閉じるボタンは出さない) を返す。同じ URL で中身と全体を返すので Vary: HX-Request を付ける。
    行が無い (RULES.md が置き場に無い・番号が表に無い) ときも 200 で、無い旨の 1 文を出す (htmx が小窓を開けるように)。
    設定の鍵のある番号 (R01・R04・R06・R07) は、この会社の今の設定の行も出す (rule_setting_line)。読むだけ。
    """
    if not RULE_CODE.match(code):
        raise Http404("決まりの番号の形が違います。")
    context = {"code": code, "rule": rule_line(code), "setting_line": rule_setting_line(code)}
    if request.headers.get("HX-Request"):
        response = render(request, "core/_rule_help.html", context)
    else:
        # 題の段は土台の物 (screen_name と絵)。screen_app は無いので絵を渡す (app_icon に空の app を渡さない)
        context.update(screen_name=f"{code} の決まり", screen_icon="help")
        response = render(request, "core/rule_help.html", context)
    patch_vary_headers(response, ("HX-Request",))
    return response
