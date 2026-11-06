"""使い方のページ (05 の 10 節。画面ごとの説明書。業務マニュアルの部品 seizou.manuals とは別)。会社の置き場の manual/ にある画面ごとの説明書を、目次つきで 1 ページに並べる画面と、その約束。

約束 (会社の置き場が持つ物。/seizou:manual の命令が書く):
  置き場: Path(settings.BASE_DIR) / "manual" の 1 か所。BASE_DIR が無ければ、置き場が無いのと同じに扱う。
  断片: 画面の目録 (seizou.core.screens.all_screens) の 1 画面ごとに manual/<名前>.html。
    名前は URL の名前の : を _ に替えた物 (例 orders:order_list → manual/orders_order_list.html)。
    中身は素の HTML の <section> 1 つ (class・<script>・<style> を持たない)。UTF-8 で書く。
    断片の無い画面には「まだありません」の文を出す。UTF-8 で読めない断片の画面には、作り直しを頼む 1 文を出す。
  前書き: manual/index.html があれば、本文の先頭に出す (無くてよい)。
  絵: manual/images/<名前>.png。断片の中から images/<名前>.png の相対の道で指す。
    ページの道は末尾に / を付けた /manual/ なので、相対の道は /manual/images/<名前> に解ける。
    そこは manual_image が返す (ログインが要る。拡張子は .png・.jpg・.jpeg・.webp だけ)。
    冊子 (PDF) では <base href="/manual/"> で同じ道に解け、帳票の部品 (core.reports.fetch_local の /manual/images/ の根) が manual/images/ から同じ制限で読む。
  読むだけ。置き場に書かない。断片の中身は直さず、そのまま出す (mark_safe)。

画面の並び:
  目次と本文は目録の並び (部品の並びと URL の並び) のまま。左のメニューの区分には分けない。
  /manual/?print=1 は印刷用の 1 枚 (core/manual_print.html。左のメニューと上の帯なし) を返す。

冊子 (PDF):
  /manual/pdf/ (manual_pdf) は、表紙・目次・画面ごとの章・ページ番号の冊子 (core/manual_booklet.html) を帳票の部品 (core.reports) で PDF にして返す。
  PDF を作れなければ、同じ 1 枚を印刷用の HTML にし、上に知らせを出して返す。
  命令 manage.py seizou_manual_pdf は、同じ冊子を manual/使い方.pdf に書き出す (--out で書き出し先を替える)。
  口の Content-Disposition は ASCII の名前 (PDF_FILENAME) と日本語の名前 (BOOKLET_FILENAME。UTF-8 を % で書いた filename*) の両方を持つ。
  冊子に絵が入る (断片の <img src="images/…"> をそのまま出し、帳票の部品が manual/images/ から読む。無い絵は飛ばす)。
  表紙の主色は BOOKLET_PRIMARY_COLOR の固定の 1 色 (会社の行に色の列が無い)。副題は BOOKLET_SUBTITLE。

小窓 (画面と枠の「?」):
  /manual/fragment/<anchor>/ (manual_fragment) は、htmx の要求に、その画面の断片を小窓の中身 (core/_manual_fragment.html) にして返す。
  ?part=<h3 の id> を付けると、断片のその <h3 id="…"> から次の <h3 か </section> の前までだけを出す。無ければ画面の全部を出す。
  htmx でない要求 (新しいタブ・JS の無いとき) は /manual/#<anchor> へ移す。anchor が画面の目録に無ければ 404。
  絵の相対の道 (src="images/…") は、小窓を開いたページの道で解けないように /manual/images/ に直して出す (置き場のファイルは直さない)。

自動の付け足し (依頼書 N168。置き場のファイルは直さず、出すときに断片の字の後に足す。断片の無い画面と読めない画面には足さない):
  入れる物: 画面の view が form_class を持ち、断片に <h3>入れる物</h3> が無ければ、欄の名前・必須・説明の表 (input_rows)。
  絵: 断片に <img が無く、manual/images/<名前>.png があれば、その絵 (命令 manage.py seizou_manual_shots が撮る)。
  止まるときの文: 部品の services.py の断りの文のうち REFUSAL_HEADS で始まる物 (refusal_texts)。使い方の画面と印刷と小窓は、その部品の画面ごとに足す。
    冊子は、その部品の最初の章の前に 1 回だけ置く (manual_sections(booklet=True) の app_refusals)。小窓で枠 (?part=) を選んだときは何も足さない。

業務の流れの章 (依頼書 N169。断片は使わず、固定の表 FLOWS から描く):
  使い方の画面と印刷の 1 枚は画面の箱の並びの前に、冊子は目次 (と前書き) の後で最初の画面の章の前に、同じ core/_manual_flows.html を出す。
  手順の画面の名前は、同じページのその画面の章 (id = 断片の名前) へ飛ぶ。目次と章の番号と冊子の栞には入れない。
  手順の画面が目録に無い (部品が入っていない) 手順は落とす。最初の手順が落ちた流れは出さない。流れが 1 つも無ければ章を出さない。
"""

from __future__ import annotations

import ast
import re
from datetime import date, datetime
from datetime import timezone as dt_timezone
from importlib import metadata
from pathlib import Path
from urllib.parse import quote

from django import forms as django_forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError
from django.http import FileResponse, Http404
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView

from seizou.core import reports
from seizou.core.screens import all_screens
from seizou.core.views import ScreenMixin

#: 断片を UTF-8 で読めなかった印 (read_fragment が返す)
UNREADABLE = object()

#: 前書きの断片の名前 (manual/index.html)
INTRO_NAME = "index"

#: 絵の口が返す拡張子と型 (SVG は中に script を持てるので出さない)
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

#: 印刷用の 1 枚を選ぶ GET の値 (/manual/?print=1)
PRINT_PARAM = "print"

#: 冊子の 1 枚 (帳票の部品 core.reports が描く)
BOOKLET_TEMPLATE = "core/manual_booklet.html"

#: 命令の既定の書き出し先の名前 (会社の置き場の manual/ の下)
BOOKLET_FILENAME = "使い方.pdf"

#: 口の Content-Disposition の ASCII の名前 (filename=。古いブラウザ用)。日本語の名前は BOOKLET_FILENAME を UTF-8 の % の形の filename* で添える
PDF_FILENAME = "seizou-manual.pdf"

#: PDF を作れないときの知らせ (shipping・manuals の PDF_NOTICE と同じ字。ほかの app の views は import しない)
PDF_NOTICE = "PDF の準備がありません。印刷用の画面をお使いください。"

#: 枠を選ぶ GET の鍵 (/manual/fragment/<anchor>/?part=<h3 の id>)
PART_PARAM = "part"

#: 枠の id の形。外れた値は「無い」と同じ扱い (画面の全部を出す)。値を HTML に戻さない
PART_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: 小窓の中身 (小窓の枠は base.html の #seizou-modal の 1 か所)
FRAGMENT_TEMPLATE = "core/_manual_fragment.html"

#: 断片の無い画面の 1 文の前半 (テンプレートが /seizou:manual の案内を続ける)
MISSING_SENTENCE = "この画面の説明書はまだありません。"

#: 業務の流れの章の流れ (依頼書 N169)。1 つの流れ = {"title", "steps"}。1 つの手順 = {"url_name", "do", "stage", "note"} と、相手の会社の手順だけ "remote": True。
#: url_name は画面の目録の URL の名前 (相手の会社の手順は、相手が使う同じ画面)。do は人がすること、note は終わると変わること (無ければ空)。
#: stage は受注の段 (orders.timeline.STAGES) か発注の段 (purchasing.timeline.STAGES) の字そのまま (段の変わらない手順は空)。字を変えるときは、その 2 つと合わせる。
FLOWS = (
    {
        "title": "注文を受けてから入金まで",
        "steps": (
            {"url_name": "orders:order_create", "do": "注文書や電話で受けた注文を、得意先と明細を入れて登録する。", "stage": "受注", "note": "生産管理ボードに出る。"},
            {"url_name": "connect:inbox_accept", "do": "seizou でつながった取引先の注文は、受信した注文から受け入れる。", "stage": "受注", "note": "受注ができ、相手に注文回答が送られる。"},
            {"url_name": "receiving:receiving_create", "do": "得意先からワークや支給材が届いたら「入荷」で記録する (表面処理のように現物が無いと始められない会社向け)。", "stage": "", "note": ""},
            {"url_name": "workorders:workorder_create", "do": "生産管理ボードで明細を選び、指示書を発行する。", "stage": "指示", "note": ""},
            {"url_name": "progress:progress_create", "do": "最初の工程の実績を入れる。", "stage": "着手", "note": "指示書の状態が「着手済」になる。"},
            {"url_name": "progress:progress_create", "do": "工程が終わるたびに、実績の数と日時を入れる。", "stage": "完了", "note": "全部の工程の実績が予定に達すると「完了」になる。"},
            {"url_name": "shipping:shipment_create", "do": "できた物を送るときに、明細ごとに出荷を記録する。", "stage": "出荷", "note": "受注の状態が「一部出荷」か「出荷済」になる。"},
            {"url_name": "shipping:deliverynote_create", "do": "同じ得意先の出荷をまとめて、納品書を作る。", "stage": "納品", "note": ""},
            {"url_name": "billing:invoice_create", "do": "締日の後に、納品書の出た出荷をまとめて請求書を作る。", "stage": "請求", "note": ""},
            {"url_name": "billing:payment_create", "do": "入金があったら、請求書に入金の日付と金額を記録する。", "stage": "入金", "note": "請求書の残が減る。"},
        ),
    },
    {
        "title": "発注から受入まで",
        "steps": (
            {"url_name": "purchasing:purchaseorder_create", "do": "協力企業と明細を入れて、発注を保存する。", "stage": "発注", "note": "seizou でつながった協力企業には注文が送られ、段が「送付」に進む。"},
            {"url_name": "purchasing:purchaseorder_detail", "do": "協力企業の注文回答と出荷の知らせを、発注の詳細で確かめる。", "stage": "回答", "note": "seizou でつながった協力企業だけ。出荷の知らせが届くと段が「出荷の知らせ」に進む。"},
            {"url_name": "purchasing:receipt_create", "do": "物が届いたら、発注の明細ごとに受入日と数を記録する。", "stage": "受入", "note": "seizou でつながった協力企業には検収が送られ、段が「検収」に進む。"},
        ),
    },
    {
        "title": "取引先とつなぐ",
        "steps": (
            {"url_name": "connect:connection_create", "do": "取引先ごとに、向き「送る」と「受ける」の 2 件を、同じ鍵の置き場の名前で登録する。", "stage": "", "note": "「送る」には相手の受け口の URL を入れる。"},
            {"url_name": "connect:connection_detail", "do": "会社の置き場で manage.py seizou_secret を打って鍵の値を作り、.env に置いて相手に人の手で渡す。", "stage": "", "note": "「鍵の設定」が「済み」になる。"},
            {"url_name": "connect:connection_create", "remote": True, "do": "相手の会社も、こちらとの接続を 2 件登録し、受け取った鍵の値を .env に置く。", "stage": "", "note": ""},
            {"url_name": "purchasing:purchaseorder_create", "do": "接続した協力企業に発注を保存する。", "stage": "送付", "note": "注文が相手の「受信した注文」に入る。"},
            {"url_name": "connect:inbox_accept", "remote": True, "do": "相手が受信した注文を受け入れる。", "stage": "回答", "note": "注文回答が返り、発注の状態が「相手が受け入れた」になる。"},
            {"url_name": "shipping:shipment_create", "remote": True, "do": "相手が出荷を記録する。", "stage": "出荷の知らせ", "note": "出荷の知らせが届く。"},
            {"url_name": "purchasing:receipt_create", "do": "届いた物を受入に記録する。", "stage": "検収", "note": "検収が相手に送られ、相手の受注の明細に入る。"},
        ),
    },
)

#: 冊子の主色 (表紙の帯と題・章の頭の帯と番号)。会社の行に色の列が無いので固定の濃い青
BOOKLET_PRIMARY_COLOR = "#1f3a5f"

#: 冊子の表紙の副題
BOOKLET_SUBTITLE = "画面ごとの操作の手引きと、お金と数の決まり"

#: 止まるときの文の 1 文目の語 (依頼書 N156 の型。tests/test_wording.py の REFUSAL_HEADS と同じ字の組)
REFUSAL_HEADS = (
    "保存できません。",
    "取り消せません。",
    "削除できません。",
    "送れません。",
    "受け入れられません。",
    "断れません。",
    "発行できません。",
    "着手できません。",
    "記録できません。",
    "出荷できません。",
)

#: 断片が自分で持つ「入れる物」の見出し (あれば表を足さない)
INPUT_HEADING = re.compile(r"<h3\b[^>]*>入れる物</h3>")

#: 道の引数 (<int:pk> など)。画面の view を引くときは 0 に替える
PATH_ARG = re.compile(r"<(?:\w+:)?\w+>")


def manual_dir() -> Path | None:
    """説明書の置き場 (会社の置き場の manual/)。settings に BASE_DIR が無ければ None。"""
    base = getattr(settings, "BASE_DIR", None)
    if not base:
        return None
    return Path(base) / "manual"


def fragment_name(url_name: str) -> str:
    """断片の名前。URL の名前の : を _ に替えた物 (例 orders:order_list → orders_order_list)。"""
    return url_name.replace(":", "_")


def manual_flows() -> list[dict]:
    """業務の流れの章の材料 (FLOWS の並びのまま)。手順の画面が目録に無い (部品が入っていない) 手順は落とす。

    最初の手順が落ちた流れは入れない (流れの入口の部品が無い)。読むだけ。
    1 つの流れ = {"title", "steps"}。1 つの手順 = {"anchor" (飛ぶ先の断片の名前), "screen" (画面の名前), "remote", "do", "stage", "note"}。
    """
    names = {screen.url_name: screen.name for screen in all_screens()}
    flows = []
    for flow in FLOWS:
        if flow["steps"][0]["url_name"] not in names:
            continue
        steps = [
            {
                "anchor": fragment_name(step["url_name"]),
                "screen": names[step["url_name"]],
                "remote": step.get("remote", False),
                "do": step["do"],
                "stage": step["stage"],
                "note": step["note"],
            }
            for step in flow["steps"]
            if step["url_name"] in names
        ]
        flows.append({"title": flow["title"], "steps": steps})
    return flows


def read_fragment(name: str):
    """manual/<name>.html を UTF-8 で読んだ字。ファイルが無ければ None、UTF-8 で読めなければ UNREADABLE。"""
    folder = manual_dir()
    if folder is None:
        return None
    path = folder / f"{name}.html"
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return UNREADABLE


def view_class_of(screen):
    """目録の画面 (Screen) の view の class。道の引数を 0 に替えて URL の木で引く。引けなければ None。"""
    try:
        return resolve(PATH_ARG.sub("0", screen.path)).func.view_class
    except (Resolver404, AttributeError):
        return None


def _literal_text(node) -> str | None:
    """断りの文の式の字。f-string の {…} は「…」に、足し算の右の名前や呼び出しも「…」にする。字で始まらない式は None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "…" for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_text(node.left)
        if left is None:
            return None
        right = _literal_text(node.right)
        return left + ("…" if right is None else right)
    return None


def refusal_texts(app: str) -> list[str]:
    """部品 (seizou の下の app) の services.py の raise ValueError と raise RuleViolation の文のうち、REFUSAL_HEADS で始まる物。

    ファイルを pathlib で読み、ast で見る (import しない・inspect を使わない)。同じ文は 1 つにし、ファイルの並びのまま返す。
    services.py の無い部品と、会社が足した app は空の並び。相手の会社への返事の文は頭の語で始まらないので入らない。
    """
    path = Path(__file__).resolve().parent.parent / app / "services.py"
    if not path.is_file():
        return []
    texts: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
            continue
        func = node.exc.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name not in ("ValueError", "RuleViolation"):
            continue
        for arg in node.exc.args:
            text = _literal_text(arg)
            if text and text.startswith(REFUSAL_HEADS) and text not in texts:
                texts.append(text)
    return texts


def input_rows(form_class) -> list[tuple[str, str, str]]:
    """入力の画面の「入れる物」の表の行 (欄の名前, 必須 か 空, 説明)。form は作らない (base_fields を読む)。

    説明は help_text。無ければ選ぶ欄の選択肢の字を「・」で並べる (表から引く選ぶ欄は DB を読むので並べない)。隠れた欄は入れない。
    """
    rows = []
    for name, field in form_class.base_fields.items():
        if field.widget.is_hidden:
            continue
        note = str(field.help_text or "")
        if not note and isinstance(field, django_forms.ChoiceField) and not isinstance(field, django_forms.ModelChoiceField):
            note = "・".join(str(label) for value, label in field.choices if value not in ("", None))
        rows.append((str(field.label or name), "必須" if field.required else "", note))
    return rows


def decorate_fragment(screen, html: str, refusals: list[str] | None = None) -> str:
    """断片の字の後に、入れる物の表・絵・止まるときの文を足した字 (置き場のファイルは直さない)。足す物が無ければそのまま返す。

    入れる物の表は、phone で画面全体が横に送れないよう、表だけを横に送れる箱 (overflow-x-auto) に包む。
    """
    extra = []
    form_class = getattr(view_class_of(screen), "form_class", None)
    if form_class is not None and not INPUT_HEADING.search(html):
        rows = input_rows(form_class)
        if rows:
            extra.append(
                format_html(
                    '<h3>入れる物</h3><div class="overflow-x-auto"><table><thead><tr><th>欄の名前</th><th>必須</th><th>説明</th></tr></thead><tbody>{}</tbody></table></div>',
                    format_html_join("", "<tr><td>{}</td><td>{}</td><td>{}</td></tr>", rows),
                )
            )
    name = fragment_name(screen.url_name)
    folder = manual_dir()
    if "<img" not in html and folder is not None and (folder / "images" / f"{name}.png").is_file():
        extra.append(format_html('<figure class="seizou-manual-shot"><img src="images/{}.png" alt="{} の画面"></figure>', name, screen.name))
    if refusals:
        extra.append(format_html("<h3>止まるときの文 (この部品)</h3><ul>{}</ul>", format_html_join("", "<li>{}</li>", ((text,) for text in refusals))))
    return html + "".join(extra)


def manual_sections(booklet: bool = False) -> list[dict]:
    """目録の 1 画面ごとの本文の材料。目録の並びのまま返す。

    1 画面 = {"name", "help", "url_name", "anchor", "html", "unreadable", "app_refusals"}。
    anchor は断片の名前 (目次の link の飛び先)。html は断片の字に自動の付け足しをした字 (mark_safe) か None。
    booklet が偽 (使い方の画面と印刷) は止まるときの文を画面ごとに足す。真 (冊子) は足さず、部品の最初の画面の app_refusals に入れる。
    """
    sections = []
    refusals: dict[str, list[str]] = {}
    for screen in all_screens():
        anchor = fragment_name(screen.url_name)
        fragment = read_fragment(anchor)
        unreadable = fragment is UNREADABLE
        first_of_app = screen.app not in refusals
        if first_of_app:
            refusals[screen.app] = refusal_texts(screen.app)
        if isinstance(fragment, str):
            fragment = decorate_fragment(screen, fragment, None if booklet else refusals[screen.app])
        sections.append(
            {
                "name": screen.name,
                "help": screen.help,
                "url_name": screen.url_name,
                "anchor": anchor,
                "html": None if fragment is None or unreadable else mark_safe(fragment),
                "unreadable": unreadable,
                "app_refusals": refusals[screen.app] if booklet and first_of_app else [],
            }
        )
    return sections


def package_version() -> str:
    """seizou の package の版 (冊子の表紙に出す)。package が入っていなければ空の字。"""
    try:
        return metadata.version("seizou")
    except metadata.PackageNotFoundError:
        return ""


def manual_date() -> date | None:
    """説明書を作った日。manual/ の下の断片 (*.html。前書きを含む) の最終更新の一番新しい物を、TIME_ZONE の日付にした物。

    置き場が無い・断片が 1 つも無いなら None (表紙に出さない)。
    """
    folder = manual_dir()
    if folder is None or not folder.is_dir():
        return None
    times = [path.stat().st_mtime for path in folder.glob("*.html") if path.is_file()]
    if not times:
        return None
    return timezone.localtime(datetime.fromtimestamp(max(times), tz=dt_timezone.utc)).date()


def booklet_context() -> dict:
    """冊子の材料 (口と命令が同じ物を描く)。帳票の部品は context processor を通らないので、会社名と版もここで渡す。

    会社名は会社の設定の 1 行の名前。DB を読めなければ "seizou" (context processor と同じ落とし方)。
    置き場と表に書かない (会社の行が無いときだけ、services.company が既定の 1 行を作る。context processor と同じ)。
    """
    from seizou.core import services

    try:
        company_name = services.company().name
    except DatabaseError:
        company_name = "seizou"
    folder = manual_dir()
    intro = read_fragment(INTRO_NAME)
    return {
        "company_name": company_name,
        "system_name": "seizou",
        "version": package_version(),
        "manual_date": manual_date(),
        "sections": manual_sections(booklet=True),
        "intro": None if intro is None or intro is UNREADABLE else mark_safe(intro),
        "flows": manual_flows(),
        "intro_unreadable": intro is UNREADABLE,
        "has_manual": folder is not None and folder.is_dir(),
        "primary_color": BOOKLET_PRIMARY_COLOR,
        "subtitle": BOOKLET_SUBTITLE,
    }


def render_booklet_pdf() -> bytes:
    """冊子の PDF の bytes。WeasyPrint が無ければ reports.PdfUnavailable がそのまま上がる (口と命令がそれぞれ受ける)。"""
    return reports.render_pdf(BOOKLET_TEMPLATE, booklet_context())


class ManualView(ScreenMixin, TemplateView):
    """使い方の画面 (画面ごとの説明書)。左に目次、右に全部の画面の断片。GET の print=1 で印刷用の 1 枚。読むだけ。

    使う人: 全員 / いつ: 画面の意味が分からないとき (特に初めての人) / 何のために: その画面の使い方を読む / 次にすること: 読んだ画面へ戻る
    """

    screen_name = "使い方"
    screen_help = "画面ごとの使い方。左の目次から読みたい画面を選びます。印刷用の 1 枚も開けます。"
    screen_icon = "book"
    template_name = "core/manual.html"
    print_template_name = "core/manual_print.html"

    def get_template_names(self):
        if self.request.GET.get(PRINT_PARAM) == "1":
            return [self.print_template_name]
        return super().get_template_names()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        folder = manual_dir()
        intro = read_fragment(INTRO_NAME)
        context["sections"] = manual_sections()
        context["intro"] = None if intro is None or intro is UNREADABLE else mark_safe(intro)
        context["flows"] = manual_flows()
        context["intro_unreadable"] = intro is UNREADABLE
        context["has_manual"] = folder is not None and folder.is_dir()
        context["print_url"] = f"{reverse('core:manual')}?{PRINT_PARAM}=1"
        context["pdf_url"] = reverse("core:manual_pdf")
        return context


@login_required
def manual_image(request, name: str):
    """manual/images/<name> の絵を返す。名前が . で始まる・.. を含む・拡張子が外れ・ファイルが無い・
    manual/images/ の外を指す、のどれかなら 404。読むだけ。"""
    folder = manual_dir()
    suffix = Path(name).suffix.lower()
    if folder is None or name.startswith(".") or ".." in name or suffix not in IMAGE_TYPES:
        raise Http404("絵がありません。")
    images = (folder / "images").resolve()
    path = (images / name).resolve()
    if path.parent != images or not path.is_file():
        raise Http404("絵がありません。")
    return FileResponse(path.open("rb"), content_type=IMAGE_TYPES[suffix])


@login_required
@never_cache
def manual_pdf(request):
    """使い方の冊子の PDF (表紙・目次・画面ごとの章・ページ番号)。作れなければ、印刷用の HTML の上に知らせを出して返す。読むだけ。"""
    context = booklet_context()
    try:
        data = reports.render_pdf(BOOKLET_TEMPLATE, context)
    except reports.PdfUnavailable:
        return HttpResponse(reports.render_html(BOOKLET_TEMPLATE, {**context, "pdf_notice": PDF_NOTICE}))
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f"inline; filename=\"{PDF_FILENAME}\"; filename*=UTF-8''{quote(BOOKLET_FILENAME, safe='')}"
    return response


def screen_for_anchor(anchor: str):
    """断片の名前 (anchor) の画面 (目録の Screen)。目録に無い名前 (.. や : 入り・前書きの index も) なら None。

    目録は 1 回だけ歩く (URL の木を読むので、1 要求に 1 回)。
    """
    for screen in all_screens():
        if fragment_name(screen.url_name) == anchor:
            return screen
    return None


def absolute_images(html: str) -> str:
    """断片の中の絵の相対の道 (src="images/…") を、使い方のページの道 (/manual/images/…) に直した字。

    小窓は /manual/ でないページに差し込まれるので、相対の道のままでは絵が出ない。置き場のファイルは直さない。
    """
    return html.replace('src="images/', f'src="{reverse("core:manual")}images/')


def fragment_body(html: str) -> str:
    """画面の全部を小窓に出すときの中身。最初の <h2>…</h2> を 1 つだけ外す (小窓の題が画面の名前を出すので、2 度出さない)。"""
    return re.sub(r"<h2\b[^>]*>.*?</h2>", "", html, count=1, flags=re.S)


def fragment_part(html: str, part: str | None) -> str | None:
    """断片のうち、枠の見出し <h3 id="<part>"> から、次の <h3 か </section> の手前まで (無ければ末尾まで)。

    part の形が PART_ID に合わない・その見出しが断片に無いなら None (口が画面の全部に落とす)。
    """
    if not part or not PART_ID.fullmatch(part):
        return None
    start = re.search(rf'<h3\b[^>]*\sid="{re.escape(part)}"', html)
    if start is None:
        return None
    end = re.compile(r"<h3\b|</section>").search(html, start.end())
    return html[start.start() : end.start() if end else len(html)]


def fragment_for_modal(fragment: str, part: str | None, screen=None) -> str:
    """小窓に出す字。枠 (part) が断片にあればその枠だけ、無ければ画面の全部 (最初の <h2> を外す)。絵の道は直す。

    screen (目録の Screen) を渡すと、画面の全部を出すときだけ自動の付け足し (止まるときの文を含む) をする。
    """
    piece = fragment_part(fragment, part)
    if piece is None:
        piece = fragment_body(fragment if screen is None else decorate_fragment(screen, fragment, refusal_texts(screen.app)))
    return absolute_images(piece)


@login_required
@never_cache
def manual_fragment(request, anchor: str):
    """画面と枠の「?」が開く小窓の中身 (その画面の断片。?part=<h3 の id> で枠だけ)。読むだけ。

    htmx でない要求は使い方のページのその画面の所 (/manual/#<anchor>) へ移す。anchor が目録に無ければ 404。
    part の値は HTML に戻さない。
    """
    screen = screen_for_anchor(anchor)
    if screen is None:
        raise Http404("この画面の説明書はありません。")
    manual_url = f"{reverse('core:manual')}#{anchor}"
    if not request.headers.get("HX-Request"):
        return HttpResponseRedirect(manual_url)
    fragment = read_fragment(anchor)
    unreadable = fragment is UNREADABLE
    html = None if fragment is None or unreadable else mark_safe(fragment_for_modal(fragment, request.GET.get(PART_PARAM), screen))
    return render(
        request,
        FRAGMENT_TEMPLATE,
        {"screen": screen, "html": html, "unreadable": unreadable, "manual_url": manual_url},
    )
