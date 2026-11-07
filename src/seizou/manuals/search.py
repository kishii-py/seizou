"""業務マニュアルの横断の検索 (core の検索の画面が呼ぶ。約束は seizou.core.search)。

題か本文に、空白で区切った言葉を全部含むマニュアル (services.search_manuals と同じ AND と並び。揺れは core の search_q)。読むだけ。
マニュアルは取引先に属さないので、取引先で絞るときは空の結果を返す。
列に出ていない本文で当たった行は、SearchRow.match にその項目を入れる (本文は Markdown の字のまま。描いた HTML は作らない)。
"""

from django.urls import reverse
from django.utils import timezone

from seizou.core.search import SearchColumn, SearchRow, SearchSection, find_match
from seizou.core.views import lookup_label, search_q

from .models import Manual

KIND = "manuals"
LABEL = "マニュアル"
COLUMNS = (
    SearchColumn("題"),
    SearchColumn("フォルダ"),
    SearchColumn("形式"),
    SearchColumn("最終更新", is_date=True),
)


def search(query, *, partner=None, limit=50):
    if partner is not None:
        return SearchSection(kind=KIND, label=LABEL, columns=COLUMNS)
    body_label = lookup_label(Manual, "body")
    manuals = Manual.objects.all()
    for term in query.split():
        manuals = manuals.filter(search_q(["title", "body"], term))
    manuals = manuals.order_by("title", "pk").select_related("folder")
    total = manuals.count()
    rows = []
    for manual in manuals[:limit]:
        cells = (
            manual.title,
            manual.folder_path_text(),
            manual.get_format_display(),
            timezone.localtime(manual.updated_at).date(),
        )
        rows.append(
            SearchRow(
                cells=cells,
                url=reverse("manuals:manual_detail", args=[manual.pk]),
                match=find_match(query, cells, [(body_label, manual.body)]),
            )
        )
    return SearchSection(kind=KIND, label=LABEL, columns=COLUMNS, rows=tuple(rows), total=total)
