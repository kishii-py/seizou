"""業務マニュアルの template tag (library の名前は manuals_tags。core の seizou_tags・seizou_icons と重ねない)。

読むだけ。表に書かない (R13)。描いた本文に安全の印を付けるのは、この app ではここの 1 か所だけ
(Markdown の部品が逃がしと許す形を済ませた字)。テンプレートに safe の filter と autoescape の切り替えを書かない。
"""

from __future__ import annotations

from django import template
from django.db.models import Count
from django.utils.safestring import mark_safe

from seizou.core.views import model_url
from seizou.manuals import diff, services
from seizou.manuals.markdown import Rendered
from seizou.manuals.models import ManualFolder

register = template.Library()


@register.simple_tag
def version_diff(version):
    """{% version_diff version as rows %}。その版の本文と今の本文の行の差分 (diff.DiffRow の並び)。"""
    return diff.diff_rows(version.body, version.manual.body)


@register.simple_tag
def manual_link(manual):
    """{% manual_link manual as link %}。紐付けの印の材料。紐付けが無ければ None。

    link = {"kind": 種類の verbose_name, "label": linked_label(), "href": その記録の詳細の URL か None}。
    """
    if manual.linked_type_id is None or manual.linked_id is None:
        return None
    obj = manual.linked_object()
    model = manual.linked_type.model_class() if manual.linked_type is not None else None
    kind = str(model._meta.verbose_name) if model is not None else ""
    href = model_url(type(obj), "detail", obj.pk) if obj is not None else None
    return {"kind": kind, "label": manual.linked_label(), "href": href}


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
    folder = ManualFolder.objects.filter(pk=int(text)).first() if text.isdecimal() else None
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
