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
  読むだけ。置き場に書かない。断片の中身は直さず、そのまま出す (mark_safe)。

画面の並び:
  目次と本文は目録の並び (部品の並びと URL の並び) のまま。左のメニューの区分には分けない。
  /manual/?print=1 は印刷用の 1 枚 (core/manual_print.html。左のメニューと上の帯なし) を返す。

冊子 (PDF):
  /manual/pdf/ (manual_pdf) は、表紙・目次・画面ごとの章・ページ番号の冊子 (core/manual_booklet.html) を帳票の部品 (core.reports) で PDF にして返す。
  PDF を作れなければ、同じ 1 枚を印刷用の HTML にし、上に知らせを出して返す。
  命令 manage.py seizou_manual_pdf は、同じ冊子を manual/使い方.pdf に書き出す (--out で書き出し先を替える)。
  冊子に絵は入らない (帳票の部品が manual/images/ を読まないため)。
  表紙の主色は BOOKLET_PRIMARY_COLOR の固定の 1 色 (会社の行に色の列が無い)。副題は BOOKLET_SUBTITLE。

小窓 (画面と枠の「?」):
  /manual/fragment/<anchor>/ (manual_fragment) は、htmx の要求に、その画面の断片を小窓の中身 (core/_manual_fragment.html) にして返す。
  ?part=<h3 の id> を付けると、断片のその <h3 id="…"> から次の <h3 か </section> の前までだけを出す。無ければ画面の全部を出す。
  htmx でない要求 (新しいタブ・JS の無いとき) は /manual/#<anchor> へ移す。anchor が画面の目録に無ければ 404。
  絵の相対の道 (src="images/…") は、小窓を開いたページの道で解けないように /manual/images/ に直して出す (置き場のファイルは直さない)。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from datetime import timezone as dt_timezone
from importlib import metadata
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError
from django.http import FileResponse, Http404
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
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

#: 口の Content-Disposition の名前 (見出しは ASCII。日本語の名前は Django が MIME の形にし、ブラウザで崩れる)
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

#: 冊子の主色 (表紙の帯と題・章の頭の帯と番号)。会社の行に色の列が無いので固定の濃い青
BOOKLET_PRIMARY_COLOR = "#1f3a5f"

#: 冊子の表紙の副題
BOOKLET_SUBTITLE = "画面ごとの操作の手引きと、お金と数の決まり"


def manual_dir() -> Path | None:
    """説明書の置き場 (会社の置き場の manual/)。settings に BASE_DIR が無ければ None。"""
    base = getattr(settings, "BASE_DIR", None)
    if not base:
        return None
    return Path(base) / "manual"


def fragment_name(url_name: str) -> str:
    """断片の名前。URL の名前の : を _ に替えた物 (例 orders:order_list → orders_order_list)。"""
    return url_name.replace(":", "_")


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


def manual_sections() -> list[dict]:
    """目録の 1 画面ごとの本文の材料。目録の並びのまま返す。

    1 画面 = {"name", "help", "url_name", "anchor", "html", "unreadable"}。
    anchor は断片の名前 (目次の link の飛び先)。html は断片の字 (mark_safe) か None。
    """
    sections = []
    for screen in all_screens():
        anchor = fragment_name(screen.url_name)
        fragment = read_fragment(anchor)
        unreadable = fragment is UNREADABLE
        sections.append(
            {
                "name": screen.name,
                "help": screen.help,
                "url_name": screen.url_name,
                "anchor": anchor,
                "html": None if fragment is None or unreadable else mark_safe(fragment),
                "unreadable": unreadable,
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
        "sections": manual_sections(),
        "intro": None if intro is None or intro is UNREADABLE else mark_safe(intro),
        "intro_unreadable": intro is UNREADABLE,
        "has_manual": folder is not None and folder.is_dir(),
        "primary_color": BOOKLET_PRIMARY_COLOR,
        "subtitle": BOOKLET_SUBTITLE,
    }


def render_booklet_pdf() -> bytes:
    """冊子の PDF の bytes。WeasyPrint が無ければ reports.PdfUnavailable がそのまま上がる (口と命令がそれぞれ受ける)。"""
    return reports.render_pdf(BOOKLET_TEMPLATE, booklet_context())


class ManualView(ScreenMixin, TemplateView):
    """使い方の画面 (画面ごとの説明書)。左に目次、右に全部の画面の断片。GET の print=1 で印刷用の 1 枚。読むだけ。"""

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
    response["Content-Disposition"] = f'inline; filename="{PDF_FILENAME}"'
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


def fragment_for_modal(fragment: str, part: str | None) -> str:
    """小窓に出す字。枠 (part) が断片にあればその枠だけ、無ければ画面の全部 (最初の <h2> を外す)。絵の道は直す。"""
    piece = fragment_part(fragment, part)
    if piece is None:
        piece = fragment_body(fragment)
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
    html = None if fragment is None or unreadable else mark_safe(fragment_for_modal(fragment, request.GET.get(PART_PARAM)))
    return render(
        request,
        FRAGMENT_TEMPLATE,
        {"screen": screen, "html": html, "unreadable": unreadable, "manual_url": manual_url},
    )
