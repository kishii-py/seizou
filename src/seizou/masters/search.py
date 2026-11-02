"""部品マスターの横断の検索 (core の検索の画面が呼ぶ。約束は seizou.core.search)。

品番・品名・図面番号・取引先の品番で部分一致。取引先を渡すと、その取引先の品番を持つ品目に絞る。読むだけ。
"""

from django.db.models import Q
from django.urls import reverse

from seizou.core.search import SearchColumn, SearchRow, SearchSection

from .models import Item, PartnerItem

KIND = "items"
LABEL = "部品マスター"
COLUMNS = (
    SearchColumn("自社品番", code=True),
    SearchColumn("品名"),
    SearchColumn("図面番号", code=True),
    SearchColumn("単位"),
)


def search(query, *, partner=None, limit=50):
    links = PartnerItem.objects.all()
    items = Item.objects.all()
    if partner is not None:
        links = links.filter(partner_id=partner)
        items = items.filter(pk__in=links.values("item_id"))
    for term in query.split():
        items = items.filter(
            Q(part_no__icontains=term)
            | Q(name__icontains=term)
            | Q(drawing_no__icontains=term)
            | Q(pk__in=links.filter(partner_part_no__icontains=term).values("item_id"))
        )
    items = items.order_by("part_no")
    total = items.count()
    rows = tuple(
        SearchRow(
            cells=(item.part_no, item.name, item.drawing_no, item.unit),
            url=reverse("masters:item_detail", args=[item.pk]),
        )
        for item in items[:limit]
    )
    return SearchSection(kind=KIND, label=LABEL, columns=COLUMNS, rows=rows, total=total)
