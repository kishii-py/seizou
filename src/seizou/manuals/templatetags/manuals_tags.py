"""業務マニュアルの template tag (library の名前は manuals_tags。core の seizou_tags・seizou_icons と重ねない)。

読むだけ。表に書かない (R13)。描いた本文に安全の印を付けるのは、この app ではここの 1 か所だけ
(Markdown の部品が逃がしと許す形を済ませた字)。テンプレートに safe の filter と autoescape の切り替えを書かない。
"""

from __future__ import annotations

from django import template
from django.db.models import Count
from django.utils.safestring import mark_safe

from seizou.manuals import services
from seizou.manuals.markdown import Rendered
from seizou.manuals.models import ManualFolder

register = template.Library()


@register.simple_tag
def manual_body(manual, body=None, manual_format=None, pdf=False):
    """{% manual_body manual [body=…] [manual_format=…] [pdf=…] as rendered %}。目次 (toc) と本文の HTML。"""
    rendered = services.render_body(manual, body=body, manual_format=manual_format, pdf=bool(pdf))
    return Rendered(html=mark_safe(rendered.html), toc=rendered.toc)


@register.simple_tag
def folder_tree(folder_pk):
    """{% folder_tree folder_pk as tree %}。今のフォルダ (字の pk。空と無い番号はルート)・パンくず・子のフォルダ。

    tree = {"folder", "path", "children"}。子 1 つ = {"folder", "count"} (count は直下のフォルダとマニュアルの数)。
    """
    text = str(folder_pk or "").strip()
    folder = ManualFolder.objects.filter(pk=int(text)).first() if text.isdigit() else None
    children = (
        ManualFolder.objects.filter(parent=folder)
        .annotate(folder_count=Count("children", distinct=True), manual_count=Count("manuals", distinct=True))
        .order_by("sort_order", "name", "pk")
    )
    return {
        "folder": folder,
        "path": services.folder_path(folder),
        "children": [{"folder": child, "count": child.folder_count + child.manual_count} for child in children],
    }
