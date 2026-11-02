"""マニュアルのページ (05 の 10 節)。会社の置き場の manual/ にある画面ごとの説明書を、目次つきで 1 ページに並べる画面と、その約束。

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
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.generic import TemplateView

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


class ManualView(ScreenMixin, TemplateView):
    """マニュアルの画面。左に目次、右に全部の画面の断片。GET の print=1 で印刷用の 1 枚。読むだけ。"""

    screen_name = "マニュアル"
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
