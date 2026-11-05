"""選ぶ欄の候補の 2 段の字・最近使った物・0 件のときの登録の入口 (N162。core の候補の口 BaseSelectView が呼ぶ。読むだけで表に書かない)。

- lines(obj): 候補の 2 段の字 (上の段, 下の段)。表 LINES に無い model は (str(obj), "") = 今の 1 行のまま。
- with_company_fields(queryset): 会社ごとの項目のある model なら拡張の表を一緒に読む (1 ページで 30 回読まない)。
- recent(queryset): queryset (候補の母集団) の行のうち、最近の受注・発注・出荷で使った物を新しい順に 5 件まで (2 節)。
- create_entry(user, model): 0 件のときの登録の行 {"url", "label"}。書けない人と、表 CREATE に無い model は None (3 節)。

部品の画面 (masters の ItemSelectView・PartnerSelectView) は R15 で 13 の属性の外を足せないので、model ごとの字の作り方はここに model の名前で持つ。
ほかの部品の model は apps.get_model で名前から引き、import しない (部品の入っていない会社と、DB が読めないときは飛ばす。core.dashboard と同じ形)。
"""

from __future__ import annotations

from django.apps import apps
from django.db import DatabaseError

from seizou.core import fields as company_fields

#: 下の段の字の区切り
SEP = "・"
#: 下の段の並びの中の印。ここに、会社ごとの項目のうち検索に使う物 (searchable。A 社の材質など) の値を並べる
COMPANY_FIELDS = "*company_fields*"
#: model の名前 → (上の段の項目, 下の段の項目)。上の段は空白で、下の段は SEP でつなぐ。空の値は飛ばす。choices の項目は表示名
LINES = {
    "masters.Item": (("part_no",), ("name", COMPANY_FIELDS, "unit")),
    "masters.Partner": (("code", "short_name"), ("name", "role")),
}


def lines(obj) -> tuple[str, str]:
    spec = LINES.get(obj._meta.label)
    if spec is None:
        return str(obj), ""
    top, bottom = spec
    return " ".join(_values(obj, top)), SEP.join(_values(obj, bottom))


def _values(obj, names) -> list[str]:
    values = []
    for name in names:
        if name == COMPANY_FIELDS:
            values += [company_fields.ext_value(obj, c.name) for c in company_fields.columns_for(obj) if c.searchable]
        elif obj._meta.get_field(name).choices:
            values.append(getattr(obj, f"get_{name}_display")())
        else:
            values.append(getattr(obj, name))
    return [str(value) for value in values if value not in (None, "")]


def with_company_fields(queryset):
    if company_fields.registration_for(queryset.model) is None:
        return queryset
    return queryset.select_related(company_fields.EXT_ACCESSOR)


#: 最近使った物の元。model の名前 → (表の名前, 候補の pk を読む lookup, 日付の lookup) の並び。並びの順は、同じ日付のときの順
RECENT_SOURCES = {
    "masters.Item": (
        ("orders.OrderLine", "item", "order__order_date"),
        ("purchasing.PurchaseOrderLine", "item", "purchase_order__order_date"),
        ("shipping.Shipment", "order_line__item", "shipped_at"),
    ),
    "masters.Partner": (
        ("orders.Order", "customer", "order_date"),
        ("purchasing.PurchaseOrder", "partner", "order_date"),
        ("shipping.Shipment", "order_line__order__customer", "shipped_at"),
    ),
}
#: 出す数と、元ごとに新しい方から読む行の数
RECENT_LIMIT = 5
RECENT_SCAN = 50


def recent(queryset) -> list:
    """queryset (候補の母集団。使用中と取引先の種類の絞りの後) の行のうち、最近の受注・発注・出荷で使った物を新しい順に RECENT_LIMIT 件まで。

    「最近」は会社の中の全員の記録で見る (受注・発注・出荷の表に書いた人の列が無いため。N162 の迷った事の 1)。
    日付の新しい順。同じ日付は RECENT_SOURCES の並びの順と、元の中の新しい行の順。部品の入っていない元と、DB が読めない元は飛ばす。
    """
    rows = []
    for label, value, day in RECENT_SOURCES.get(queryset.model._meta.label, ()):
        try:
            model = apps.get_model(label)
        except LookupError:
            continue
        try:
            rows += list(model._default_manager.order_by(f"-{day}", "-pk").values_list(day, value)[:RECENT_SCAN])
        except DatabaseError:
            continue
    rows.sort(key=lambda row: row[0], reverse=True)
    pks = list(dict.fromkeys(pk for _day, pk in rows if pk is not None))
    found = {obj.pk: obj for obj in queryset.filter(pk__in=pks)}
    return [found[pk] for pk in pks if pk in found][:RECENT_LIMIT]
