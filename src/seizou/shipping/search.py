"""出荷の横断の検索 (core の検索の画面が呼ぶ。約束は seizou.core.search)。

出荷番号と納品書番号で部分一致 (事務が得意先から番号で問い合わせを受けるため。本体の決め 11-11 14:2x)。
行は出荷。納品書番号で当たった出荷も、列の「納品書」にその番号が出るので、SearchRow.match は作らない。
取引先を渡すと、受注の得意先がその取引先の出荷に絞る。読むだけ。金額の列は出さない。
語の揺れ (全角と半角・ひらがなとカタカナ) は一覧の検索と同じ (core の search_q)。
行の url は出荷の詳細 (納品書へは出荷の詳細から移る)。
"""

from seizou.core.search import SearchColumn, SearchRow, SearchSection
from seizou.core.views import lookup_label, search_q, url_or_none

from .models import Shipment

KIND = "shipments"
LABEL = "出荷"
COLUMNS = (
    SearchColumn("出荷番号", is_code=True),
    SearchColumn("得意先"),
    SearchColumn("品番"),
    SearchColumn("数量", numeric=True),
    SearchColumn("出荷日", is_date=True),
    SearchColumn("納品書", is_code=True),
)
#: 出荷の表で探す項目。search() と search_labels() がここから作る (「探した項目」の 1 行と実際に探す所をずらさないため)
LOOKUPS = ("shipment_no", "delivery_note__note_no")


def search_labels() -> list[str]:
    """search() が探す項目の見出し (約束は seizou.core.search)。出荷番号・納品書番号。"""
    return list(dict.fromkeys(lookup_label(Shipment, lookup) for lookup in LOOKUPS))


def search(query, *, partner=None, limit=50):
    shipments = Shipment.objects.all()
    if partner is not None:
        shipments = shipments.filter(order_line__order__customer_id=partner)
    for term in query.split():
        shipments = shipments.filter(search_q(LOOKUPS, term))
    shipments = shipments.order_by("-shipped_at", "-pk")
    total = shipments.count()
    page = shipments.select_related("order_line__order__customer", "order_line__item", "delivery_note")[:limit]
    rows = []
    for shipment in page:
        note = shipment.delivery_note
        cells = (
            shipment.shipment_no,
            shipment.order_line.order.customer,
            shipment.order_line.item.part_no,
            shipment.qty,
            shipment.shipped_at,
            note.note_no if note is not None else "",
        )
        rows.append(SearchRow(cells=cells, url=url_or_none("shipping:shipment_detail", shipment.pk)))
    return SearchSection(kind=KIND, label=LABEL, columns=COLUMNS, rows=tuple(rows), total=total)
