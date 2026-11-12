"""請求書の横断の検索 (core の検索の画面が呼ぶ。約束は seizou.core.search)。

請求書番号で部分一致 (事務が得意先から請求書番号で問い合わせを受けるため。本体の決め 11-11 14:2x)。取消の請求書も出す (状態の列が「取消」)。
取引先を渡すと、その得意先の請求書に絞る。読むだけ。
合計は金額の列 (SearchColumn の money)。金額の閲覧の無い人には core の display_section が「—」にする。
探すのは列に出ている請求書番号だけなので、SearchRow.match は作らない (find_match に金額を渡さない)。
語の揺れ (全角と半角・ひらがなとカタカナ) は一覧の検索と同じ (core の search_q)。行の url は請求書の詳細。
"""

from seizou.core.search import SearchColumn, SearchRow, SearchSection
from seizou.core.views import lookup_label, search_q, url_or_none

from .forms import period_text
from .models import Invoice

KIND = "invoices"
LABEL = "請求書"
COLUMNS = (
    SearchColumn("請求書番号", is_code=True),
    SearchColumn("得意先"),
    SearchColumn("発行日", is_date=True),
    SearchColumn("締め期間"),
    SearchColumn("合計", numeric=True, money=True),
    SearchColumn("状態"),
)
#: 請求書の表で探す項目。search() と search_labels() がここから作る (「探した項目」の 1 行と実際に探す所をずらさないため)
LOOKUPS = ("invoice_no",)


def search_labels() -> list[str]:
    """search() が探す項目の見出し (約束は seizou.core.search)。請求書番号。"""
    return list(dict.fromkeys(lookup_label(Invoice, lookup) for lookup in LOOKUPS))


def search(query, *, partner=None, limit=50):
    invoices = Invoice.objects.all()
    if partner is not None:
        invoices = invoices.filter(customer_id=partner)
    for term in query.split():
        invoices = invoices.filter(search_q(LOOKUPS, term))
    invoices = invoices.order_by("-issued_on", "-pk")
    total = invoices.count()
    page = invoices.select_related("customer")[:limit]
    rows = []
    for invoice in page:
        cells = (
            invoice.invoice_no,
            invoice.customer,
            invoice.issued_on,
            period_text(invoice.period_from, invoice.period_to),
            invoice.total,
            invoice.get_status_display(),
        )
        rows.append(SearchRow(cells=cells, url=url_or_none("billing:invoice_detail", invoice.pk)))
    return SearchSection(kind=KIND, label=LABEL, columns=COLUMNS, rows=tuple(rows), total=total)
