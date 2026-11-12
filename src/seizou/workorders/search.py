"""指示書の横断の検索 (core の検索の画面が呼ぶ。約束は seizou.core.search)。

指示書番号で部分一致 (事務が指示書番号で問い合わせを受けるため。本体の決め 11-11 14:2x)。
品番と品名では探さない (部品マスターと受注の結果と重なるため)。行は指示書で、品番と品名は列に出す。
取引先を渡すと、受注の得意先がその取引先の指示書に絞る。読むだけ。
語の揺れ (全角と半角・ひらがなとカタカナ) は一覧の検索と同じ (core の search_q)。
「状態」の列は指示書の一覧の「状態」と同じ字 (実績から出す未着手・着手済・完了・取消。WorkOrder.progress_state_label)。
行ごとに工程を読まないように、一覧と同じく工程と実績の合計を 1 回で prefetch する。行の url は指示書の詳細。
"""

from django.db.models import Prefetch

from seizou.core.search import SearchColumn, SearchRow, SearchSection
from seizou.core.views import lookup_label, search_q, url_or_none

from .models import WorkOrder, WorkOrderStep, with_recorded_total

KIND = "workorders"
LABEL = "指示書"
COLUMNS = (
    SearchColumn("指示書番号", is_code=True),
    SearchColumn("品番"),
    SearchColumn("品名"),
    SearchColumn("数量", numeric=True),
    SearchColumn("発行日", is_date=True),
    SearchColumn("状態"),
)
#: 指示書の表で探す項目。search() と search_labels() がここから作る (「探した項目」の 1 行と実際に探す所をずらさないため)
LOOKUPS = ("wo_no",)


def search_labels() -> list[str]:
    """search() が探す項目の見出し (約束は seizou.core.search)。指示書番号。"""
    return list(dict.fromkeys(lookup_label(WorkOrder, lookup) for lookup in LOOKUPS))


def search(query, *, partner=None, limit=50):
    workorders = WorkOrder.objects.all()
    if partner is not None:
        workorders = workorders.filter(order_line__order__customer_id=partner)
    for term in query.split():
        workorders = workorders.filter(search_q(LOOKUPS, term))
    workorders = workorders.order_by("-issued_on", "-pk")
    total = workorders.count()
    steps = with_recorded_total(WorkOrderStep.objects.select_related("process").order_by("seq"))
    page = workorders.select_related("order_line__item").prefetch_related(Prefetch("steps", queryset=steps))[:limit]
    rows = []
    for workorder in page:
        item = workorder.order_line.item
        cells = (
            workorder.wo_no,
            item.part_no,
            item.name,
            workorder.qty,
            workorder.issued_on,
            workorder.progress_state_label(),
        )
        rows.append(SearchRow(cells=cells, url=url_or_none("workorders:workorder_detail", workorder.pk)))
    return SearchSection(kind=KIND, label=LABEL, columns=COLUMNS, rows=tuple(rows), total=total)
