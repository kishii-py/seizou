"""部品マスターの横断の検索 (core の検索の画面が呼ぶ。約束は seizou.core.search)。

品番・品名・図面番号・取引先の品番と、会社ごとの項目のうち検索に使う物 (登録の searchable) で部分一致。
取引先を渡すと、その取引先の品番を持つ品目に絞る。読むだけ。
"""

from django.core.exceptions import FieldDoesNotExist
from django.db.models import Q
from django.urls import reverse

from seizou.core import fields as company_fields
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


def company_lookups() -> list[str]:
    """会社ごとの項目のうち検索に使う物の lookup (例 ext__ext01)。拡張の表が無い (登録だけある) 会社では空。

    一覧の絞り込み (BaseListView) は同じ登録を見るので、全体の検索でも同じ項目が当たる (初見テスト 2 回目の所見 1)。
    """
    lookups = company_fields.search_fields_for(Item)
    if not lookups:
        return []
    try:
        Item._meta.get_field(company_fields.EXT_ACCESSOR)
    except FieldDoesNotExist:
        return [lookup for lookup in lookups if not lookup.startswith(company_fields.EXT_ACCESSOR + "__")]
    return lookups


def search(query, *, partner=None, limit=50):
    links = PartnerItem.objects.all()
    items = Item.objects.all()
    if partner is not None:
        links = links.filter(partner_id=partner)
        items = items.filter(pk__in=links.values("item_id"))
    extra = company_lookups()
    for term in query.split():
        q = (
            Q(part_no__icontains=term)
            | Q(name__icontains=term)
            | Q(drawing_no__icontains=term)
            | Q(pk__in=links.filter(partner_part_no__icontains=term).values("item_id"))
        )
        for lookup in extra:
            q |= Q(**{f"{lookup}__icontains": term})
        items = items.filter(q)
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
