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
  module は次の 1 つを持ってもよい (持たなくてもよい。持たない部品も今までどおり探せ、「探した項目」の 1 行に出ないだけ)。
  - search_labels() -> list[str]: search() が探す項目の見出しの並び (例 ["題", "本文"])。検索の画面が、言葉があるときに
      「探した項目」の 1 行へ「LABEL: 見出し・見出し」の形で出す (初見テスト R5 の候補 C36 の 2)。見出しは一覧の案内
      (BaseListView.search_hint) と同じく、表の項目は lookup_label、会社ごとの項目は列の label。search() と同じ lookup の並び
      (module の定数) から作り、字の 1 行と実際に探す所をずらさない。読むだけ。表を読まない。

返す物の形:
  SearchSection(kind, label, columns, rows, total)
    columns: SearchColumn の並び (見出しの字と、数・識別子・日付の印)。字だけを渡せば印の無い列。
    rows: SearchRow の並び。
  SearchRow(cells, url, match)
    cells: 列の値の並び (columns と同じ数)。数は Decimal / int のまま返す (テンプレートが区切りと右寄せをする)。
      品目と取引先は物のまま返すと、押すと小窓が開く link になる (一覧と同じ)。
    url: 詳細の URL か None。
    match: 列に出ていない項目で当たった所 (SearchMatch(label, before, hit, after)) か None。部品の search() が
      find_match(query, cells, candidates) で作る。画面は 1 列目の link の下に「見出し: 値」を灰の小さい字で出し、
      当たった字を主色の太字にする。
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from importlib import import_module, util
from urllib.parse import urlencode

from django.apps import apps
from django.db import DatabaseError
from django.views.generic import TemplateView

from seizou.core import access
from seizou.core.views import ScreenMixin, is_master, is_negative, load_model, search_variants
from seizou.parts import PARTS

#: 1 種類で画面に出す行の上限 (ページ送りは作らない。超えたら絞り込みを頼む)
LIMIT = 50
#: 全部の種類を探す印 (GET の kind)
ALL = "all"
#: 列に出ていない項目で当たった所の 1 行に出す値の字の数の目安 (「…」は数えない)
MATCH_CHARS = 24
#: 当たった字の前に残す字の数
MATCH_BEFORE = 8
#: 省いた所の印 (U+2026 の 1 字)
ELLIPSIS = "\u2026"
#: 行の頭に続く Markdown の印 (空白・タブ・# > - * + | : と [ ] [x])。位置を渡して match で当てるので ^ は付けない
LINE_MARKS = re.compile(r"(?:[ \t#>*+|:-]|\[[ xX]\])+")


@dataclass(frozen=True)
class SearchColumn:
    """結果の表の列 1 つ。numeric は右寄せと区切り、is_code は等幅の識別子、is_date は等幅の日付、money は金額の列 (一覧の ColumnSpec の印と同じ名前)。"""

    label: str
    numeric: bool = False
    is_code: bool = False
    is_date: bool = False
    money: bool = False  # 金額の列。金額の閲覧が無い人には「—」


@dataclass(frozen=True)
class SearchMatch:
    """列に出ていない項目で当たった所。label は項目の見出し、hit は当たった字 (元の値の字のまま)、before と after はその前後の字。"""

    label: str
    before: str
    hit: str
    after: str

    @property
    def text(self) -> str:
        """1 行に出す値の字の全部 (画面の title に使う)。"""
        return self.before + self.hit + self.after


@dataclass(frozen=True)
class SearchRow:
    """結果の行 1 つ。match は列に出ていない項目で当たった所 (無ければ None)。"""

    cells: tuple
    url: str | None = None
    match: SearchMatch | None = None


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


def provider_app(module) -> str:
    """search module の部品の app の名前 (seizou.orders.search → orders)。部署の門 (access.can_open) に渡す名前。"""
    parts = module.__name__.split(".")
    return parts[-2] if len(parts) >= 2 else parts[-1]


def open_providers(user) -> list:
    """providers() のうち、利用者が部署でその部品を開ける物だけ (判定は左のメニューと同じ access.can_open。記録は書かない)。

    訳: 部署の判定が on のとき、部署で一覧を開けない部品 (例 受注) の行を、横断の検索から読めないようにする。
    何のため: 検索は core の画面 (部署に関係なく開く) なので、部品ごとに門を通さないと、一覧の 403 で閉じた部品の行が検索で読める。
    log (既定) と off では can_open が真を返すので、今までどおり全部を探す (記録の行は書かない。検索は字を打つたびに開くため)。
    出所 = Claude Security の走査の候補 11-07 (横断検索が部署の門を通らない)。
    変えてよい条件 = 部署の門が一覧の画面でも部署を見なくなったとき (そのときは検索も同じにそろえる)。
    """
    return [module for module in providers() if access.can_open(user, provider_app(module), record=False)]


def run_search(
    query: str, *, kind: str = ALL, partner: int | None = None, limit: int = LIMIT, modules: list | None = None
) -> list[SearchSection]:
    """kind の種類 (ALL なら全部) を探す。当たりの無い種類と、表の読めない種類は並べない。

    modules は探す search module の並び (検索の画面は open_providers の物を渡す)。None なら providers() の全部。
    """
    query = (query or "").strip()
    if not query:
        return []
    sections = []
    for module in providers() if modules is None else modules:
        if kind != ALL and module.KIND != kind:
            continue
        try:
            section = module.search(query, partner=partner, limit=limit)
        except DatabaseError:
            continue
        if section is not None and section.total:
            sections.append(section)
    return sections


def searched_fields(modules, kind: str = ALL) -> list[dict]:
    """「探した項目」の 1 行の並び。modules (providers() の返す物) の順に {"label": LABEL, "fields": 見出しを「・」でつないだ字}。

    kind が ALL でなければ、その種類だけにする (探していない種類を書かないため)。search_labels() を持たない部品と、
    見出しの無い部品は並べない (約束の「持たなくてもよい物」)。表を読まない。
    """
    found = []
    for module in modules:
        if kind != ALL and module.KIND != kind:
            continue
        labels = getattr(module, "search_labels", None)
        if not callable(labels):
            continue
        names = labels()
        if names:
            found.append({"label": module.LABEL, "fields": "・".join(names)})
    return found


def _contains(variant: str, value: str):
    """value の中で variant (揺れの 1 つ) が最初に現れる所 (大文字と小文字は同じに扱う)。無ければ None。"""
    return re.search(re.escape(variant), value, re.IGNORECASE)


def _make_match(label: str, value: str, found) -> SearchMatch:
    """value の中の当たった所 (found) から、当たった字のある行だけで SearchMatch を作る。

    行の頭に続く Markdown の印を外し、当たった字の前は MATCH_BEFORE 字まで、前と当たった字と後の合計は MATCH_CHARS 字までにする
    (省いた所は「…」。「…」は数えない)。当たった字だけで超えるときは、行が続けば後は「…」だけ。
    """
    start, end = found.span()
    line_start = max(value.rfind("\n", 0, start), value.rfind("\r", 0, start)) + 1
    line_ends = [i for i in (value.find("\n", end), value.find("\r", end)) if i != -1]
    line_end = min(line_ends) if line_ends else len(value)
    marks = LINE_MARKS.match(value, line_start, start)
    head = marks.end() if marks else line_start
    before = value[head:start]
    if len(before) > MATCH_BEFORE:
        before = before[-MATCH_BEFORE:]
        shown_before = ELLIPSIS + before
    else:
        shown_before = before
    hit = found.group()
    after = value[end:line_end]
    room = max(MATCH_CHARS - len(before) - len(hit), 0)
    if len(after) > room:
        after = after[:room] + ELLIPSIS
    return SearchMatch(label, shown_before, hit, after)


def find_match(query: str, cells, candidates) -> SearchMatch | None:
    """列に出ていない項目で当たった所を 1 つ返す。部品の search() が行ごとに呼ぶ。

    query は search() に来た言葉のまま (空白で分けた語ごとに見る)。cells はその行の列の値の並び (SearchRow の cells と同じ物)。
    candidates は (見出し, 値) の並びで、列に出ていない当たりうる項目を探す順に並べた物。
    語ごとに順に、揺れ (search_variants。search_q と同じ物) のどれかが列のどれかの字に含まれていれば次の語へ移る。
    どの列にも無ければ candidates を順に見て (None と空の字は飛ばす)、揺れの順に値の中を探し、最初に見つかった所で返す。
    どの語でも作れなければ None。行を出すかどうかは今のまま DB が決め、この関数は 1 行を足すかだけを決める
    (DB の icontains と扱いが違って見つからない行は None になり、今と同じ形で出る)。
    """
    shown = [str(value) for value in cells if value is not None]
    for term in query.split():
        variants = search_variants(term)
        if any(_contains(variant, text) for variant in variants for text in shown):
            continue
        for label, value in candidates:
            if value is None or value == "":
                continue
            value = str(value)
            for variant in variants:
                found = _contains(variant, value)
                if found:
                    return _make_match(label, value, found)
    return None


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
    """テンプレートが描く形。1 列 = SearchColumn、1 行 = {"cells", "url", "match"}、1 枡 = _rows.html と同じ印の辞書。
    枡の boolean は値が真偽なら真で、テンプレートが bool_mark で印にする (C55)。"""
    columns = [as_column(c) for c in section.columns]
    rows = []
    for row in section.rows:
        cells = []
        for column, value in zip(columns, row.cells):
            if hide_money and column.money:
                value = access.MASK
            cells.append(
                {
                    "value": value,
                    "boolean": isinstance(value, bool),
                    "master": value if is_master(value) else None,
                    "numeric": column.numeric,
                    "negative": column.numeric and is_negative(value),
                    "money": column.money,
                    "date": column.is_date and not isinstance(value, datetime.datetime),
                    "datetime": column.is_date and isinstance(value, datetime.datetime),
                    "code": column.is_code,
                }
            )
        rows.append({"cells": cells, "url": row.url, "match": row.match})
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
    screen_help = "部品マスター・受注・マニュアルを横断して探し、行を押すと詳細へ移る。"
    screen_icon = "search"
    template_name = "core/search.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get("q", "").strip()
        # 部署で開けない部品は、探す物にも chip にも「探した項目」にも入れない (open_providers の訳)
        modules = open_providers(self.request.user)
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
        sections = run_search(query, kind=kind, partner=partner, modules=modules) if query else []
        context.update(
            {
                "query": query,
                "kind": kind,
                "chips": chips,
                "kind_labels": "・".join(m.LABEL for m in modules),
                "customers": customers,
                "partner": partner_value,
                "sections": [display_section(s, hide_money=not access.can_see_money(self.request.user, record=False)) for s in sections],
                # 「探した項目」の 1 行 (言葉があるときだけ。初見テスト R5 の候補 C36 の 2)
                "searched_fields": searched_fields(modules, kind) if query else [],
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
