"""受注の横断の検索 (core の検索の画面が呼ぶ。約束は seizou.core.search)。

受注番号・得意先の注文番号・明細の品目の品番と品名で部分一致。取引先を渡すと、その得意先の受注に絞る。読むだけ。
語の揺れ (全角と半角・ひらがなとカタカナ) は一覧の検索と同じ (core の search_q)。
詳細の URL (orders:order_detail) がまだ無ければ、行の url は None にする。
列に出ていない得意先の注文番号と明細の品番と品名で当たった行は、SearchRow.match にその項目を入れる。
"""

from django.db.models import Q

from seizou.core.search import SearchColumn, SearchRow, SearchSection, find_match
from seizou.core.views import lookup_label, search_q, url_or_none

from .models import Order, OrderLine

KIND = "orders"
LABEL = "受注"
COLUMNS = (
    SearchColumn("受注番号", is_code=True),
    SearchColumn("得意先"),
    SearchColumn("得意先の注文番号", is_code=True),
    SearchColumn("受注日", is_date=True),
    SearchColumn("品番"),
    SearchColumn("状態"),
)
#: 受注の表で探す項目。search() と search_labels() がここから作る (「探した項目」の 1 行と実際に探す所をずらさないため)
ORDER_LOOKUPS = ("order_no", "customer_order_no")
#: 明細の表で探す項目 (明細の品目の品番と品名)
LINE_LOOKUPS = ("item__part_no", "item__name")


def search_labels() -> list[str]:
    """search() が探す項目の見出し (約束は seizou.core.search)。受注番号・得意先の注文番号・自社品番・品名。"""
    labels = [lookup_label(Order, lookup) for lookup in ORDER_LOOKUPS]
    labels += [lookup_label(OrderLine, lookup) for lookup in LINE_LOOKUPS]
    return list(dict.fromkeys(labels))


def part_no_cell(order) -> str:
    """明細の 1 行目の品番。明細が複数なら「品番 ほか n 件」。"""
    lines = list(order.lines.all())
    if not lines:
        return ""
    first = lines[0].item.part_no
    return first if len(lines) == 1 else f"{first} ほか {len(lines) - 1} 件"


def search(query, *, partner=None, limit=50):
    customer_order_no_label = lookup_label(Order, "customer_order_no")
    part_no_label = lookup_label(OrderLine, "item__part_no")
    name_label = lookup_label(OrderLine, "item__name")
    orders = Order.objects.all()
    if partner is not None:
        orders = orders.filter(customer_id=partner)
    for term in query.split():
        lines = OrderLine.objects.filter(search_q(LINE_LOOKUPS, term))
        orders = orders.filter(search_q(ORDER_LOOKUPS, term) | Q(pk__in=lines.values("order_id")))
    orders = orders.order_by("-order_date", "-pk")
    total = orders.count()
    page = orders.select_related("customer").prefetch_related("lines__item")[:limit]
    rows = []
    for order in page:
        cells = (
            order.order_no,
            order.customer,
            order.customer_order_no,
            order.order_date,
            part_no_cell(order),
            order.get_status_display(),
        )
        # 「品番」の列は明細の 1 行目と「ほか n 件」だけなので、2 行目より後の品番と品名は列に出ない
        candidates = [(customer_order_no_label, order.customer_order_no)]
        for line in order.lines.all():
            candidates += [(part_no_label, line.item.part_no), (name_label, line.item.name)]
        rows.append(
            SearchRow(
                cells=cells,
                url=url_or_none("orders:order_detail", order.pk),
                match=find_match(query, cells, candidates),
            )
        )
    return SearchSection(kind=KIND, label=LABEL, columns=COLUMNS, rows=tuple(rows), total=total)
