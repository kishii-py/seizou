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
from seizou.manuals.models import Manual, ManualFolder

register = template.Library()


@register.simple_tag
def version_diff(version):
    """{% version_diff version as rows %}。その版の本文と今の本文の行の差分 (diff.DiffRow の並び)。"""
    return diff.diff_rows(version.body, version.manual.body)


@register.simple_tag
def diff_summary(rows):
    """{% diff_summary rows as summary %}。差分の行 (diff.DiffRow の並び) の、種類ごとの行の数と最初の違う行の位置。

    summary = {"added", "removed", "changed", "first_index"}。first_index は最初の「同じ」でない行の位置
    (0 から数える。テンプレートの forloop.counter0 と比べる)。違う行が無ければ None。
    """
    kinds = [row.kind for row in rows]
    first_index = next((index for index, kind in enumerate(kinds) if kind != diff.SAME), None)
    return {
        "added": kinds.count(diff.ADDED),
        "removed": kinds.count(diff.REMOVED),
        "changed": kinds.count(diff.CHANGED),
        "first_index": first_index,
    }


@register.simple_tag
def version_rows(manual, versions):
    """{% version_rows object versions as rows %}。版の履歴の表の行 (頭に今の版の行、続けて前の版の行)。

    行 1 つ = {"number", "is_current", "saved_at", "saved_by", "note", "version"}。
    今の版の行は番号だけを持ち、日時・保存した人・メモは空で、version は None (差分と戻すの URL を作らない)。
    形は図面の版の表 (masters_drawing の rows) と同じ。メモは今のまま、変える前の中身の版の行に付く。
    versions はテンプレートが {% if versions %} で読んだ物を渡す (同じ問い合わせを 2 回送らないため)。
    """
    rows = [{"number": manual.version, "is_current": True, "saved_at": None, "saved_by": None, "note": "", "version": None}]
    for version in versions:
        rows.append(
            {
                "number": version.number,
                "is_current": False,
                "saved_at": version.saved_at,
                "saved_by": version.saved_by,
                "note": version.note,
                "version": version,
            }
        )
    return rows


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


@register.simple_tag
def manuals_in_folders():
    """{% manuals_in_folders as in_folders %}。フォルダに入っているマニュアルの数 (どの深さのフォルダの物も数える)。

    一覧のいちばん上は直下の物だけを並べるので、フォルダの中にある数を添えて「マニュアルが無い」と読まれないようにする。
    """
    return Manual.objects.filter(folder__isnull=False).count()
