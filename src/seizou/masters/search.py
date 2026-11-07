"""部品マスターの横断の検索 (core の検索の画面が呼ぶ。約束は seizou.core.search)。

品番・品名・図面番号・取引先の品番と、会社ごとの項目のうち検索に使う物 (登録の searchable) で部分一致。
語の揺れ (全角と半角・ひらがなとカタカナ) は一覧の検索と同じ (core の search_q)。
取引先を渡すと、その取引先の部品の取引先の行 (品番の空の行も含む) を持つ品目に絞る。読むだけ。
列に出ていない取引先の品番と会社ごとの項目で当たった行は、SearchRow.match にその項目を入れる。
"""

from django.core.exceptions import FieldDoesNotExist
from django.db.models import Prefetch, Q
from django.urls import reverse

from seizou.core import fields as company_fields
from seizou.core.search import SearchColumn, SearchRow, SearchSection, find_match
from seizou.core.views import lookup_label, search_q

from .models import Item, PartnerItem

KIND = "items"
LABEL = "部品マスター"
COLUMNS = (
    SearchColumn("自社品番", is_code=True),
    SearchColumn("品名"),
    SearchColumn("図面番号", is_code=True),
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


def company_candidates(item, lookups) -> list[tuple[str, object]]:
    """会社ごとの項目のうち lookups (company_lookups() の返す物) にある物の (見出し, 値) の並び。find_match に渡す。

    ext__ で始まる lookup ごとに、その字を外した名前と同じ名前の列 (登録の Column.name) の label と、
    ext_value で読んだ値を並べる。ext__ で始まらない lookup と、列の見つからない lookup は飛ばす。
    """
    prefix = company_fields.EXT_ACCESSOR + "__"
    columns = {column.name: column for column in company_fields.columns_for(Item)}
    found = []
    for lookup in lookups:
        if not lookup.startswith(prefix):
            continue
        column = columns.get(lookup[len(prefix):])
        if column is None:
            continue
        found.append((column.label, company_fields.ext_value(item, column.name)))
    return found


def search(query, *, partner=None, limit=50):
    links = PartnerItem.objects.all()
    items = Item.objects.all()
    if partner is not None:
        links = links.filter(partner_id=partner)
        items = items.filter(pk__in=links.values("item_id"))
    company = company_lookups()
    lookups = ["part_no", "name", "drawing_no", *company]
    for term in query.split():
        items = items.filter(
            search_q(lookups, term)
            | Q(pk__in=links.filter(search_q(["partner_part_no"], term)).values("item_id"))
        )
    items = items.order_by("part_no")
    total = items.count()
    # 当たった項目の 1 行のため、ページの品目の取引先の品番 (探すときと同じ取引先の分。登録の古い順) を 1 回で読む
    page = items.prefetch_related(Prefetch("partner_items", queryset=links.order_by("pk")))
    if any(lookup.startswith(company_fields.EXT_ACCESSOR + "__") for lookup in company):
        page = page.select_related(company_fields.EXT_ACCESSOR)
    partner_label = lookup_label(PartnerItem, "partner_part_no")
    rows = []
    for item in page[:limit]:
        cells = (item.part_no, item.name, item.drawing_no, item.unit)
        candidates = [(partner_label, link.partner_part_no) for link in item.partner_items.all()]
        candidates += company_candidates(item, company)
        rows.append(
            SearchRow(
                cells=cells,
                url=reverse("masters:item_detail", args=[item.pk]),
                match=find_match(query, cells, candidates),
            )
        )
    return SearchSection(kind=KIND, label=LABEL, columns=COLUMNS, rows=tuple(rows), total=total)
