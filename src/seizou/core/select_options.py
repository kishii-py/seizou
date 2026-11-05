"""選ぶ欄の候補の 2 段の字・最近使った物・0 件のときの登録の入口 (N162。core の候補の口 BaseSelectView が呼ぶ。読むだけで表に書かない)。

- lines(obj): 候補の 2 段の字 (上の段, 下の段)。表 LINES に無い model は (str(obj), "") = 今の 1 行のまま。
- with_company_fields(queryset): 会社ごとの項目のある model なら拡張の表を一緒に読む (1 ページで 30 回読まない)。
- recent(queryset): queryset (候補の母集団) の行のうち、最近の受注・発注・出荷で使った物を新しい順に 5 件まで (2 節)。
- create_entry(user, model): 0 件のときの登録の行 {"url", "label"}。書けない人と、表 CREATE に無い model は None (3 節)。

部品の画面 (masters の ItemSelectView・PartnerSelectView) は R15 で 13 の属性の外を足せないので、model ごとの字の作り方はここに model の名前で持つ。
ほかの部品の model は apps.get_model で名前から引き、import しない (部品の入っていない会社と、DB が読めないときは飛ばす。core.dashboard と同じ形)。
"""

from __future__ import annotations

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
