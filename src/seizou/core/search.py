"""横断の検索 (本人の求め 11-02 17:0x)。部品マスター・受注などを 1 つの言葉でまとめて探す画面と、その約束。

約束 (部品の側が持つ物):
  部品 (seizou.parts.PARTS の app) が seizou.<app>.search の module を持てば、core が順に呼んで結果を並べる。
  module は次の 3 つを持つ。
  - KIND: 種類の名前 (英字。例 "items")。画面の chip と GET の kind に使う。
  - LABEL: 種類の見出し (日本語。例 "部品マスター")。chip とカードの見出しに使う。
  - search(query, *, partner=None, limit=50) -> SearchSection
      query: 空白を除いた検索の言葉 (空では呼ばない)。空白で区切った言葉は全部を含む物を探す (一覧の検索と同じ)。
      partner: 取引先の pk (int)。None なら全部。
      limit: 返す行の上限。total には上限の前の件数を入れる。
      読むだけ。表に書かない。SQL を直接書かない (R14)。行は search() の中で list にして返す
      (表が無いときの DatabaseError を search() の中で起こし、core が飛ばせるように)。
  module が無い部品は飛ばす。search() が DatabaseError を投げた部品も飛ばす (HomeView の数字のカードと同じ)。

返す物の形:
  SearchSection(kind, label, columns, rows, total)
    columns: SearchColumn の並び (見出しの字と、数・識別子・日付の印)。字だけを渡せば印の無い列。
    rows: SearchRow の並び。
  SearchRow(cells, url)
    cells: 列の値の並び (columns と同じ数)。数は Decimal / int のまま返す (テンプレートが区切りと右寄せをする)。
      品目と取引先は物のまま返すと、押すと小窓が開く link になる (一覧と同じ)。
    url: 詳細の URL か None。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from importlib import import_module, util
from urllib.parse import urlencode

from django.apps import apps
from django.db import DatabaseError
from django.views.generic import TemplateView

from seizou.core import access
from seizou.core.views import ScreenMixin, is_master, is_negative, load_model, screen_value
from seizou.parts import PARTS

#: 1 種類で画面に出す行の上限 (ページ送りは作らない。超えたら絞り込みを頼む)
LIMIT = 50
#: 全部の種類を探す印 (GET の kind)
ALL = "all"


@dataclass(frozen=True)
class SearchColumn:
    """結果の表の列 1 つ。numeric は右寄せと区切り、is_code は等幅の識別子、is_date は等幅の日付、money は金額の列 (一覧の ColumnSpec の印と同じ名前)。"""

    label: str
    numeric: bool = False
    is_code: bool = False
    is_date: bool = False
    money: bool = False  # 金額の列。金額の閲覧が無い人には「—」


@dataclass(frozen=True)
class SearchRow:
    """結果の行 1 つ。"""

    cells: tuple
    url: str | None = None


@dataclass(frozen=True)
class SearchSection:
    """1 種類の結果。total は上限の前の件数。"""

    kind: str
    label: str
    columns: tuple
    rows: tuple = field(default_factory=tuple)
    total: int = 0


def providers() -> list:
    """探せる部品の search module の並び (PARTS の順)。入っていない部品と、module の無い部品は飛ばす。"""
    found = []
    for part in PARTS:
        if part == "seizou.core" or not apps.is_installed(part):
            continue
        name = f"{part}.search"
        if util.find_spec(name) is None:
            continue
        module = import_module(name)
        if getattr(module, "KIND", "") and callable(getattr(module, "search", None)):
            found.append(module)
    return found


def run_search(query: str, *, kind: str = ALL, partner: int | None = None, limit: int = LIMIT) -> list[SearchSection]:
    """kind の種類 (ALL なら全部) を探す。当たりの無い種類と、表の読めない種類は並べない。"""
    query = (query or "").strip()
    if not query:
        return []
    sections = []
    for module in providers():
        if kind != ALL and module.KIND != kind:
            continue
        try:
            section = module.search(query, partner=partner, limit=limit)
        except DatabaseError:
            continue
        if section is not None and section.total:
            sections.append(section)
    return sections


def customer_choices() -> list | None:
    """得意先の選択肢 (取引先の種類が得意先か両方の取引先)。マスターの部品が無いか、表が読めなければ None。"""
    if not apps.is_installed("seizou.masters"):
        return None
    partner_model = load_model("seizou.masters.models.Partner")
    if partner_model is None:
        return None
    try:
        return list(
            partner_model._default_manager.filter(
                role__in=(partner_model.ROLE_CUSTOMER, partner_model.ROLE_BOTH)
            ).order_by("code")
        )
    except DatabaseError:
        return None


def as_column(column) -> SearchColumn:
    return SearchColumn(column) if isinstance(column, str) else column


def display_section(section: SearchSection, *, hide_money=False) -> dict:
    """テンプレートが描く形。1 列 = SearchColumn、1 行 = {"cells", "url"}、1 枡 = _rows.html と同じ印の辞書。"""
    columns = [as_column(c) for c in section.columns]
    rows = []
    for row in section.rows:
        cells = []
        for column, value in zip(columns, row.cells):
            if hide_money and column.money:
                value = access.MASK
            cells.append(
                {
                    "value": screen_value(value),
                    "master": value if is_master(value) else None,
                    "numeric": column.numeric,
                    "negative": column.numeric and is_negative(value),
                    "date": column.is_date and not isinstance(value, datetime.datetime),
                    "datetime": column.is_date and isinstance(value, datetime.datetime),
                    "code": column.is_code,
                }
            )
        rows.append({"cells": cells, "url": row.url})
    return {
        "kind": section.kind,
        "label": section.label,
        "columns": columns,
        "rows": rows,
        "total": section.total,
        "shown": len(rows),
        "more": section.total > len(rows),
    }


def query_string(**params) -> str:
    return urlencode({k: v for k, v in params.items() if v not in (None, "")})


class SearchView(ScreenMixin, TemplateView):
    """横断の検索の画面。GET の q (言葉)・kind (種類。all か部品の KIND)・partner (取引先の pk)。読むだけ。

    使う人: 全員 / いつ: 品番や注文番号で探すとき / 何のために: 横断して探す / 次にすること: 行を押して詳細へ
    """

    screen_name = "検索"
    screen_help = "品番・品名・注文番号などで横断して探し、行を押すと詳細へ移る。"
    screen_icon = "search"
    template_name = "core/search.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get("q", "").strip()
        modules = providers()
        kinds = {m.KIND for m in modules}
        kind = self.request.GET.get("kind", ALL)
        if kind not in kinds:
            kind = ALL
        customers = customer_choices()
        partner = self.partner_param(customers)
        partner_value = "" if partner is None else str(partner)
        chips = [{"kind": ALL, "label": "全て"}] + [{"kind": m.KIND, "label": m.LABEL} for m in modules]
        for chip in chips:
            chip["selected"] = chip["kind"] == kind
            chip["query"] = query_string(q=query, kind="" if chip["kind"] == ALL else chip["kind"], partner=partner_value)
        sections = run_search(query, kind=kind, partner=partner) if query else []
        context.update(
            {
                "query": query,
                "kind": kind,
                "chips": chips,
                "kind_labels": "・".join(m.LABEL for m in modules),
                "customers": customers,
                "partner": partner_value,
                "sections": [display_section(s, hide_money=not access.can_see_money(self.request.user, record=False)) for s in sections],
                "limit": LIMIT,
            }
        )
        return context

    def partner_param(self, customers) -> int | None:
        """GET の partner を得意先の選択肢の pk に限って読む。空か、選択肢に無い値なら None (全得意先)。"""
        if not customers:
            return None
        try:
            pk = int(self.request.GET.get("partner", ""))
        except ValueError:
            return None
        return pk if any(p.pk == pk for p in customers) else None
