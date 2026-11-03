"""受注の横断の検索 (core の検索の画面が呼ぶ。約束は seizou.core.search)。

受注番号・得意先の注文番号・明細の品目の品番と品名で部分一致。取引先を渡すと、その得意先の受注に絞る。読むだけ。
語の揺れ (全角と半角・ひらがなとカタカナ) は一覧の検索と同じ (core の search_q)。
詳細の URL (orders:order_detail) がまだ無ければ、行の url は None にする。
"""

from django.db.models import Q

from seizou.core.search import SearchColumn, SearchRow, SearchSection
from seizou.core.views import search_q, url_or_none

from .models import Order, OrderLine

KIND = "orders"
LABEL = "受注"
COLUMNS = (
    SearchColumn("受注番号", code=True),
    SearchColumn("得意先"),
    SearchColumn("受注日", date=True),
    SearchColumn("品番"),
    SearchColumn("状態"),
)


def part_no_cell(order) -> str:
    """明細の 1 行目の品番。明細が複数なら「品番 ほか n 件」。"""
    lines = list(order.lines.all())
    if not lines:
        return ""
    first = lines[0].item.part_no
    return first if len(lines) == 1 else f"{first} ほか {len(lines) - 1} 件"


def search(query, *, partner=None, limit=50):
    orders = Order.objects.all()
    if partner is not None:
        orders = orders.filter(customer_id=partner)
    for term in query.split():
        lines = OrderLine.objects.filter(search_q(["item__part_no", "item__name"], term))
        orders = orders.filter(search_q(["order_no", "customer_order_no"], term) | Q(pk__in=lines.values("order_id")))
    orders = orders.order_by("-order_date", "-pk")
    total = orders.count()
    page = orders.select_related("customer").prefetch_related("lines__item")[:limit]
    rows = tuple(
        SearchRow(
            cells=(order.order_no, order.customer, order.order_date, part_no_cell(order), order.get_status_display()),
            url=url_or_none("orders:order_detail", order.pk),
        )
        for order in page
    )
    return SearchSection(kind=KIND, label=LABEL, columns=COLUMNS, rows=rows, total=total)
