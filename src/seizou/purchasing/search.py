"""発注の横断の検索 (core の検索の画面が呼ぶ。約束は seizou.core.search)。

発注番号で部分一致 (協力企業から発注番号で問い合わせを受けるため。本体の決め 11-11 14:2x)。
取引先を渡すと、その取引先への発注に絞る。読むだけ。金額の列は出さない。
探すのは列に出ている発注番号だけなので、SearchRow.match は作らない。
語の揺れ (全角と半角・ひらがなとカタカナ) は一覧の検索と同じ (core の search_q)。行の url は発注の詳細。
"""

from seizou.core.search import SearchColumn, SearchRow, SearchSection
from seizou.core.views import lookup_label, search_q, url_or_none

from .models import PurchaseOrder

KIND = "purchaseorders"
LABEL = "発注"
COLUMNS = (
    SearchColumn("発注番号", is_code=True),
    SearchColumn("取引先"),
    SearchColumn("発注日", is_date=True),
    SearchColumn("品番"),
    SearchColumn("状態"),
)
#: 発注の表で探す項目。search() と search_labels() がここから作る (「探した項目」の 1 行と実際に探す所をずらさないため)
LOOKUPS = ("po_no",)


def search_labels() -> list[str]:
    """search() が探す項目の見出し (約束は seizou.core.search)。発注番号。"""
    return list(dict.fromkeys(lookup_label(PurchaseOrder, lookup) for lookup in LOOKUPS))


def part_no_cell(purchase_order) -> str:
    """明細の 1 行目 (行番号の順。明細の Meta.ordering) の品番。明細が複数なら「品番 ほか n 件」(受注の検索の品番の列と同じ形)。"""
    lines = list(purchase_order.lines.all())
    if not lines:
        return ""
    first = lines[0].item.part_no
    return first if len(lines) == 1 else f"{first} ほか {len(lines) - 1} 件"


def search(query, *, partner=None, limit=50):
    orders = PurchaseOrder.objects.all()
    if partner is not None:
        orders = orders.filter(partner_id=partner)
    for term in query.split():
        orders = orders.filter(search_q(LOOKUPS, term))
    orders = orders.order_by("-order_date", "-pk")
    total = orders.count()
    page = orders.select_related("partner").prefetch_related("lines__item")[:limit]
    rows = []
    for purchase_order in page:
        cells = (
            purchase_order.po_no,
            purchase_order.partner,
            purchase_order.order_date,
            part_no_cell(purchase_order),
            purchase_order.get_status_display(),
        )
        rows.append(SearchRow(cells=cells, url=url_or_none("purchasing:purchaseorder_detail", purchase_order.pk)))
    return SearchSection(kind=KIND, label=LABEL, columns=COLUMNS, rows=tuple(rows), total=total)
