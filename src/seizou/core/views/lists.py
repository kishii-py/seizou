"""一覧の土台 (BaseListView) と候補の口の土台 (BaseSelectView) (core.views の package)。"""

from __future__ import annotations

import csv
from urllib.parse import quote

from django.db.models import Sum
from django.http import HttpResponse, JsonResponse
from django.template.loader import select_template
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.views.generic import ListView, View

from seizou.core import access
from seizou.core import csvio
from seizou.core import select_options
from seizou.core import fields as company_fields
from seizou.core import summary as list_summary

from .base import DEFAULT_PAGE_SIZE, NUMERIC_FIELDS, PAGE_SIZES, ScreenMixin, display_value, is_master, is_negative, lookup_label, model_url, search_q
from .list_specs import ColumnSpec, FilterSpec


class BaseListView(ScreenMixin, ListView):
    """一覧の土台。サーバーが行を描く。既定は 1 ページ 50 件 (20 / 50 / 100)。

    合計の行は既定では出さない (締日や単価を足しても意味が無いため)。合計を出したい列は辞書で書く:
        columns = ("order_no", {"name": "amount", "label": "金額", "total": True})
    合計に足すのは total が真で、表の数の項目 (DecimalField・IntegerField・FloatField) の列と、kind が number / money の
    annotate の列 (部品の get_queryset が annotate した名前) だけ。
    表の項目の並び替えは get_ordering が、sort_key があれば sort_key の鍵で返す。
    annotate の列の並び替えは、部品の get_queryset が annotate を足した後 (paginate_queryset と csv_response) で当てる
    (sort_key があれば sort_key の鍵で。Django の土台は get_ordering の並びを部品の get_queryset の super() の中、
    annotate より先に当てるため)。
    並び替えの要求がある時は、同じ値の行の順を pk でそろえる (昇順は pk、降順は -pk。ページ送りの境で行が重なったり抜けたりしない)。
    既定の並び (ordering) には足さない。
    絞り (filters) は FilterSpec の辞書の並び。get_queryset が検索の後に足すので、件数・合計・CSV も同じ条件になる。
    ページ送りと並び替えの link は GET の残り (query_without_page・query_without_sort) で、絞りの f_ の値を持ち越す。
    集計 (GET view=summary。core/summary.py): 表の代わりに、絞りの軸 (g_by) ごとの件数か数の列の合計 (g_val) の表と横棒の図を出す。
    絞り・検索・部品の get_queryset の後の行を数える。CSV (format=csv) も集計の行になる。軸が 1 つも無い一覧は表のまま。
    絞りのタブ (presets): 枠の値の組に名前を付けたタブを枠の上に出す (core/_presets.html)。件数は表と同じ条件で数える。先頭に「すべて」を土台が足す。
    効いている絞りの札 (filter_chips): 枠の下に「見出し: 値 ×」で並べる (core/_filter_clear.html)。× と「絞りを外す」は一覧の枠 (LIST_TARGET) を取り直す。
    検索 (search_fields): 1 要素は lookup の字か、列と同じ (lookup, 見出し) の組で、組の見出しは検索の欄の案内にそのまま出る
    (例 ("order_line__order__customer__name", "得意先の名前")。字なら案内は lookup_label の末尾の欄の名前)。
    """

    columns: tuple = ()
    search_fields: tuple = ()
    filters: tuple = ()
    presets: tuple = ()
    #: 宣言できるタブの数 (「すべて」を除く)。件数の問い合わせがタブの数だけ増えるため
    PRESET_LIMIT = 4
    ordering = None
    paginate_by = DEFAULT_PAGE_SIZE
    page_sizes = PAGE_SIZES
    template_name = "core/list.html"
    #: 札の × と「絞りを外す」と空の枠の「すべて外す」が取り直す一覧の枠の id (core/list.html)。
    #: この枠を名指しした htmx の要求には画面の全体を返し、htmx が枠だけを選んで入れ替える (欄・タブ・札・表がそろう。開発の記録 docs/nights/20261105/N161.md)
    LIST_TARGET = "list-card"

    # --- 列 ---
    def column_specs(self) -> list[ColumnSpec]:
        specs = [ColumnSpec(self.model, item) for item in self.columns]
        for col in company_fields.list_columns_for(self.model):
            specs.append(ColumnSpec(self.model, (company_fields.EXT_PREFIX + col.name, col.label), ext_type=col.type))
        # 金額の列は、金額の閲覧が無い人には列ごと出さない (見出し・欄・合計の行・並び替え・集計の量・CSV から外れ、「—」も出さない。
        # 本人の決め 11-05 09:4x。開発の記録 docs/nights/20261105/N155.md)。log の形では見せて記録に 1 行 (同じ要求は 1 行)。金額の列の無い一覧は判定しない。
        if any(spec.money for spec in specs) and not self._money_columns_shown():
            specs = [spec for spec in specs if not spec.money]
        return specs

    def _money_columns_shown(self) -> bool:
        """金額の列を見せるか。判定 (記録つき) は 1 要求で 1 回だけ呼び、答えを画面に控える。

        column_specs は 1 要求で何度も呼ばれる (並び替え・get_queryset・行・合計・CSV) ため。記録の行の数は今までと同じ (開発の記録 docs/nights/20261106/N174.md)。
        """
        if not hasattr(self, "_money_ok"):
            self._money_ok = access.can_see_money(self.request.user)
        return self._money_ok

    def sort_param(self) -> str:
        sort = self.request.GET.get("sort", "")
        allowed = {s.name for s in self.column_specs() if s.sortable}
        return sort if sort.lstrip("-") in allowed else ""

    def annotated_sort(self) -> str:
        """並び替えの先 (sort_param) が annotate の列なら、その字 (- 付きのまま)。ほかは空の字。"""
        sort = self.sort_param()
        name = sort.lstrip("-")
        return sort if sort and any(s.annotated and s.name == name for s in self.column_specs()) else ""

    def get_ordering(self):
        # annotate の列の並び替えは、ここでは既定の並びを返す (Django の土台がこの戻り値を部品の annotate より先に
        # order_by に渡し、無い名前は FieldError になるため)。並び替えは paginate_queryset と csv_response で当てる。
        sort = self.sort_param()
        if sort and not self.annotated_sort():
            spec = next(s for s in self.column_specs() if s.name == sort.lstrip("-"))
            prefix = "-" if sort.startswith("-") else ""
            return (prefix + spec.order_key(), prefix + "pk")
        return self.ordering or "-pk"

    def order_annotated(self, queryset):
        """annotate の列の並び替えを、部品の get_queryset の後の queryset に当てる。annotate に無い名前は並び替えない。"""
        sort = self.annotated_sort()
        if not sort:
            return queryset
        spec = next(s for s in self.column_specs() if s.name == sort.lstrip("-"))
        prefix = "-" if sort.startswith("-") else ""
        key = spec.order_key()
        if key in queryset.query.annotations:
            return queryset.order_by(prefix + key, prefix + "pk")
        return queryset

    def paginate_queryset(self, queryset, page_size):
        # 部品の get_queryset が annotate を足した後の queryset (self.object_list) がここに来る。
        # 頁は Paginator.get_page で選ぶ (範囲の外と 0 以下は最後の頁、数でない字は 1 頁目、last は最後の頁)。404 にしない。
        paginator = self.get_paginator(
            self.order_annotated(queryset),
            page_size,
            orphans=self.get_paginate_orphans(),
            allow_empty_first_page=self.get_allow_empty(),
        )
        number = self.kwargs.get(self.page_kwarg) or self.request.GET.get(self.page_kwarg) or 1
        page = paginator.get_page(paginator.num_pages if number == "last" else number)
        return (paginator, page, page.object_list, page.has_other_pages())

    def get_paginate_by(self, queryset):
        try:
            per = int(self.request.GET.get("per", ""))
        except ValueError:
            per = 0
        return per if per in self.page_sizes else self.paginate_by

    # --- 検索 ---
    def search_terms(self) -> list[str]:
        return [t for t in self.request.GET.get("q", "").split() if t]

    def search_lookups(self) -> list[str]:
        """絞る lookup の並び。search_fields の (lookup, 見出し) の組は lookup だけにする (列の書き方と同じ読み方)。"""
        own = [item[0] if isinstance(item, (tuple, list)) else item for item in self.search_fields]
        return [*own, *company_fields.search_fields_for(self.model)]

    def get_queryset(self):
        qs = super().get_queryset()
        # 列の FK (前向きの ForeignKey と OneToOneField。annotate の列は除く) の相手を、行と一緒に 1 回の SELECT で引く
        # (行ごとに相手を読む SELECT が行の数だけ出ないため。開発の記録 docs/nights/20261106/N174.md)。部品の get_queryset が同じ名前を足しても害は無い。
        related = [
            spec.name.split("__")[0]
            for spec in self.column_specs()
            if getattr(spec.field, "concrete", False)
            and (spec.field.many_to_one or spec.field.one_to_one)
            and not spec.annotated
        ]
        if related:
            qs = qs.select_related(*related)
        lookups = self.search_lookups()
        for term in self.search_terms():
            if not lookups:
                break
            qs = qs.filter(search_q(lookups, term))
        return self.apply_filters(qs)

    # --- 絞り ---
    def filter_specs(self) -> list[FilterSpec]:
        """宣言 (filters) の絞りと、その後ろに一覧に出す会社ごとの項目のうち絞れる物 (宣言に同じ項目が無い物)。"""
        specs = [FilterSpec(self.model, item) for item in self.filters]
        declared = {spec.field_name for spec in specs}
        for col in company_fields.list_columns_for(self.model):
            field = company_fields.EXT_PREFIX + col.name
            if col.type in FilterSpec.EXT_KINDS and field not in declared:
                specs.append(FilterSpec(self.model, {"name": col.name, "field": field}))
        # 金額の名前の絞りは、金額の閲覧が無い人には出さず効かせない (絞りの欄・札・apply_filters・flag_on・filter_chosen・集計の軸の全部から外れる。
        # 列と同じ = 本人の決め 11-05 09:4x。開発の記録 docs/nights/20261105/N155.md。作者の決め「金額を見られない人には列と絞りを出さない」)。範囲の欄が残ると、
        # 行が出るか出ないかで金額が分かってしまうため。URL に f_<名前>_from を書いても無い欄と同じ扱い (断る処理ではない)。
        # 判定は column_specs と同じ控え (1 要求で 1 回)。金額の絞りの無い一覧と、要求の無い画面の物 (起動の検査と試験が宣言だけを読む) は判定しない。
        if any(spec.money for spec in specs) and getattr(self, "request", None) is not None and not self._money_columns_shown():
            specs = [spec for spec in specs if not spec.money]
        return specs

    def apply_filters(self, queryset):
        """filters の値 (GET の f_<name>) で絞る。空と読めない値は絞らない。flag は入で disabled_when が真でないときだけ when を掛ける。"""
        specs = self.filter_specs()
        named = FilterSpec.values_by_name(specs, self.filter_params)
        for spec in specs:
            queryset = spec.apply(queryset, self.filter_params, named)
        return queryset

    def flag_on(self, name: str) -> bool:
        """flag の絞り name が入か (GET の f_<name>。鍵が無いか読めない値は宣言の default)。宣言に無い名前は偽。"""
        return any(spec.is_on(self.filter_params) for spec in self.filter_specs() if spec.name == name)

    def filter_chosen(self, name: str) -> bool:
        """絞り name (GET の f_<name>) に読めた値があるか。空と読めない値は無いのと同じ。宣言に無い名前は偽。"""
        return any(spec.values(self.filter_params) for spec in self.filter_specs() if spec.name == name)

    def filter_chips(self, specs, items) -> list[dict]:
        """効いている絞りの札 (core/_filter_clear.html。本人の決め 11-05 16:2x。開発の記録 docs/nights/20261105/N161.md)。

        絞りの枠の値のうち狭める物だけ (flag は入のときだけ。既定で入の印を切った物と、ほかの絞りに従って切に描いた印は札にしない)。
        キーワード・件数・並び替え・集計の軸は札にしない。タブ (presets) は札にしない (タブが入れた値は、その絞りの札になる)。
        1 つ = name・heading (見出し。flag は空)・text (値の字。flag は印の見出し)・query (その絞りだけを外した GET。page と format も外す)。
        """
        chips = []
        for spec, item in zip(specs, items):
            if not item["active"] or (spec.kind == "flag" and not item["checked"]):
                continue
            heading, text = ("", spec.label) if spec.kind == "flag" else (item["label"], FilterSpec.chip_text(item))
            query = self.query_without("page", "format", *spec.params)
            chips.append({"name": spec.name, "heading": heading, "text": text, "query": query})
        return chips

    # --- 絞りのタブ (presets) ---
    @property
    def filter_params(self):
        """絞りが読む GET (f_ の値)。タブの件数を数える間だけ、組の辞書に差し替える (preset_tabs)。"""
        params = getattr(self, "_filter_params", None)
        return self.request.GET if params is None else params

    def preset_specs(self) -> list[dict]:
        """宣言 (presets) のタブの並び。先頭に「すべて」(既定で入の印を切り、ほかの絞りを外す) を足す。params が関数なら呼んで辞書にする。"""
        if not self.presets:
            return []
        if len(self.presets) > self.PRESET_LIMIT:
            raise ValueError(f"絞りのタブは「すべて」を除いて {self.PRESET_LIMIT} つまでです ({len(self.presets)} つ)。")
        specs = self.filter_specs()
        everything = {spec.param: "0" for spec in specs if spec.kind == "flag" and spec.default}
        tabs = [{"label": "すべて", "params": everything}]
        for item in self.presets:
            unknown = [key for key in item if key not in ("label", "params")]
            if unknown:
                raise ValueError(f"絞りのタブに書けない鍵があります: {unknown!r}。label / params のどちらかにしてください。")
            params = item["params"]() if callable(item["params"]) else item["params"]
            tabs.append({"label": item["label"], "params": dict(params)})
        return tabs

    def preset_tabs(self) -> list[dict]:
        """テンプレート (core/_presets.html) が描くタブ: label・href・count・selected。

        href は今の GET から page・format と絞りの f_ を外し、組の値を足した物 (キーワード・件数・並び替え・集計の軸は残る)。
        count は表と同じ queryset (検索と部品の get_queryset を含む) に組の絞りだけを当てた件数 (タブの数だけ問い合わせが増える。行の数では増えない)。
        selected は、今の GET の絞りの読めた値が組と同じとき (組に無い絞りが入っていれば、どのタブも選ばれない)。CSV の要求では空。
        """
        tabs = self.preset_specs()
        if not tabs or self.request.GET.get("format") == "csv":
            return []
        specs = self.filter_specs()
        names = [name for spec in specs for name in spec.params]
        current = {spec.name: spec.value(self.request.GET) for spec in specs}
        base = self.request.GET.copy()
        for key in ("page", "format", *names):
            base.pop(key, None)
        rows = []
        for tab in tabs:
            query = base.copy()
            for key, value in tab["params"].items():
                query[key] = value
            self._filter_params = tab["params"]
            try:
                count = self.get_queryset().count()
            finally:
                self._filter_params = None
            chosen = {spec.name: spec.value(tab["params"]) for spec in specs}
            rows.append({"label": tab["label"], "href": "?" + query.urlencode(), "count": count, "selected": chosen == current})
        return rows

    # --- 行 ---
    def row_for(self, obj, specs) -> dict:
        cells = []
        for spec in specs:
            value = display_value(obj, spec.name)
            master = value if is_master(value) else None
            # マスター自身の一覧では、名前の字を押すと自分の小窓が開く (決め 11-02 12:5x)。
            if master is None and spec.name == "name" and is_master(obj):
                master = obj
            cells.append(
                {
                    "value": value,
                    "master": master,
                    "numeric": spec.numeric,
                    "negative": spec.numeric and is_negative(value),
                    "money": spec.money,
                    "date": spec.is_date,
                    "datetime": spec.is_datetime,
                    "code": spec.is_code,
                    # 状態の列 (choices を持つ status の項目。status_for と同じ読み方) は core/_rows.html が状態の色の表で印にする (開発の記録 docs/nights/20261105/N152.md)
                    "status": spec.name == "status" and bool(getattr(spec.field, "choices", None)),
                    # 真偽の値は、行のテンプレートが bool_mark の印にする (真 = チェックの丸・偽 = 空。本人の決め 11-06 17:1x)
                    "boolean": isinstance(value, bool),
                }
            )
        return {"pk": obj.pk, "obj": obj, "cells": cells, "url": model_url(self.model, "detail", obj.pk)}

    def totals_for(self, queryset, specs) -> list | None:
        """絞り込みに追従する合計の行 (合計を選んだ、表の数の項目と、kind が number / money の annotate の列。
        無ければ出さない)。1 列 = {"value", "numeric", "money"} (money は金額の列なら真。テンプレートが yen_if で ¥ を付ける)。annotate の列は queryset の annotate にある名前だけ足す。"""
        sums = {}
        for spec in specs:
            if spec.total and (
                isinstance(spec.field, NUMERIC_FIELDS)
                or (spec.annotated and spec.numeric and spec.name in queryset.query.annotations)
            ):
                sums[spec.name] = Sum(spec.name)
        if not sums:
            return None
        # 集計の名前は列の名前と分ける (annotate と同じ名前で aggregate すると、Django は annotate の値を足さない)。
        aliases = {f"seizou_total_{index}": name for index, name in enumerate(sums)}
        result = queryset.aggregate(**{alias: sums[name] for alias, name in aliases.items()})
        agg = {name: result[alias] for alias, name in aliases.items()}
        return [{"value": agg.get(spec.name), "numeric": spec.name in sums, "money": spec.money} for spec in specs]

    def search_hint(self) -> str:
        """検索の欄の案内 (例「自社品番・品名・図面番号・材質」)。search_fields の見出しと、検索に使う会社ごとの項目の見出し。

        search_fields の見出しは、字の lookup なら lookup_label (辿る物は末尾の欄の名前)、(lookup, 見出し) の組なら組の見出し
        (末尾の欄の名前では何の名前か分からない所に書く。例 出荷の一覧の「得意先の名前」= 作者の決め)。
        """
        labels = [item[1] if isinstance(item, (tuple, list)) else lookup_label(self.model, item) for item in self.search_fields]
        labels += [c.label for c in company_fields.columns_for(self.model) if c.searchable]
        return "・".join(dict.fromkeys(labels))

    def query_without(self, *keys) -> str:
        params = self.request.GET.copy()
        for key in keys:
            params.pop(key, None)
        return params.urlencode()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        specs = self.column_specs()
        context["columns"] = specs
        # 集計 (view=summary) のときは表の行と合計の行を作らない (画面は core/_summary.html だけを描く)。
        context["summary"] = self.summary_for(specs)
        context["rows"] = [] if context["summary"] else [self.row_for(obj, specs) for obj in context["object_list"]]
        context["totals"] = None if context["summary"] else self.totals_for(self.get_queryset(), specs)
        context["search_q"] = self.request.GET.get("q", "")
        context["search_hint"] = self.search_hint()
        context["sort"] = self.sort_param()
        context["per"] = self.get_paginate_by(None)
        context["page_sizes"] = self.page_sizes
        context["query_without_page"] = self.query_without("page", "format")
        context["query_without_sort"] = self.query_without("sort", "page", "format")
        filter_specs = self.filter_specs()
        named = FilterSpec.values_by_name(filter_specs, self.request.GET)
        context["filters"] = [spec.context(self.request.GET, named) for spec in filter_specs]
        context["filter_active"] = any(item["active"] for item in context["filters"])
        context["query_without_filters"] = self.query_without(
            "page", "format", *(name for spec in filter_specs for name in spec.params)
        )
        # 効いている絞りの札 (core/_filter_clear.html が絞り込みの枠の下に並べる。開発の記録 docs/nights/20261105/N161.md)
        context["filter_chips"] = self.filter_chips(filter_specs, context["filters"])
        context["presets"] = self.preset_tabs()
        context["create_url"] = model_url(self.model, "create")
        if context["create_url"] and not access.can_write_model(self.request.user, self.model, "create"):
            context["create_url"] = None
        context["rows_template_names"] = self.rows_template_names()
        context["summary_axes"] = context["summary"]["axes"] if context["summary"] else self.summary_axes()
        context["query_without_view"] = self.query_without("page", "format", *list_summary.PARAMS)
        return context

    # --- 集計 (GET view=summary。core/summary.py) ---
    def summary_axes(self) -> list:
        """集計の軸 (絞りの choice と lookup の項目と、date_range の項目の月。多の側へ辿る項目は外す)。"""
        return list_summary.axes_for(self.model, self.filter_specs())

    def summary_for(self, specs) -> dict | None:
        """GET view=summary のときの集計。ほかの view と、軸が 1 つも無い一覧は None (表のまま)。

        量は件数と、表の数の項目の列 (主キーを除く) の合計。金額の列は金額の閲覧が無い人には出さず、GET で選んでも件数に戻す。
        """
        if self.request.GET.get("view") != list_summary.VIEW:
            return None
        axes = self.summary_axes()
        if not axes:
            return None
        numeric = [s for s in specs if isinstance(s.field, NUMERIC_FIELDS) and not s.field.primary_key]
        money_ok = not any(s.money for s in numeric) or access.can_see_money(self.request.user, record=False)
        measures = list_summary.measures_for(numeric, money_ok=money_ok)
        axis = list_summary.pick_axis(axes, self.request.GET.get("g_by", ""))
        measure = list_summary.pick_measure(measures, self.request.GET.get("g_val", ""))
        if measure.money:
            access.can_see_money(self.request.user)  # 金額を見せる時の記録 (row_for と同じ。log の形のとき 1 行)
        return {**list_summary.summarize(self.get_queryset(), axis, measure), "axes": axes, "measures": measures}

    def rows_template_names(self) -> list[str]:
        return [f"{self.model._meta.app_label}/_rows.html", "core/_rows.html"]

    def wants_rows_only(self) -> bool:
        """行の部分だけを返すか。htmx の要求でも、ブラウザの戻る (履歴の復元) には画面の全体を返す
        (htmx は復元の要求にも HX-Request を付け、応答で body を丸ごと差し替えるため)。
        一覧の枠 (LIST_TARGET) を名指しした要求 (札の × と「絞りを外す」と「すべて外す」) にも画面の全体を返す (htmx が枠だけを選ぶ)。"""
        headers = self.request.headers
        return (
            bool(headers.get("HX-Request"))
            and not headers.get("HX-History-Restore-Request")
            and headers.get("HX-Target") != self.LIST_TARGET
        )

    def render_to_response(self, context, **response_kwargs):
        if self.request.GET.get("format") == "csv":
            summary = context.get("summary")
            return self.summary_csv_response(summary) if summary else self.csv_response(context)
        if self.wants_rows_only():
            # partial: 行の部分だけを返す印。core/_rows.html はこのとき道具の帯の件数と CSV も差し替える (hx-swap-oob)。
            # 集計のときは core/_summary.html (同じく oob で差し替える)。
            context["partial"] = True
            template = select_template(["core/_summary.html"] if context.get("summary") else self.rows_template_names())
            response = HttpResponse(template.render(context, self.request))
        else:
            response = super().render_to_response(context, **response_kwargs)
        # 同じ URL で部分と全体を返すので、ブラウザの保存が取り違えないように見出しで分ける。
        patch_vary_headers(response, ("HX-Request", "HX-Target"))
        return response

    def csv_filename(self) -> tuple[str, str]:
        """CSV のファイルの名前 (画面の名前_YYYYMMDD.csv) と、ASCII だけの代わりの名前 (<model>_YYYYMMDD.csv)。"""
        today = timezone.localdate().strftime("%Y%m%d")
        name = self.screen_name or str(self.model._meta.verbose_name)
        return f"{name}_{today}.csv", f"{self.model._meta.model_name}_{today}.csv"

    def csv_response(self, context) -> HttpResponse:
        specs = context["columns"]
        if any(s.money for s in specs) and not access.can_see_money(self.request.user):
            specs = [s for s in specs if not s.money]
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        filename, fallback = self.csv_filename()
        response["Content-Disposition"] = (
            f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"
        )
        response.write("﻿")
        writer = csv.writer(response)
        writer.writerow([s.label for s in specs])
        for obj in self.order_annotated(self.get_queryset()):
            writer.writerow([csvio.to_text(display_value(obj, s.name)) for s in specs])
        return response

    def summary_csv_response(self, summary) -> HttpResponse:
        """集計の CSV (画面の表と同じ行と合計の行)。ファイルの名前は 画面の名前_集計_YYYYMMDD.csv (代わりは <model>_summary_YYYYMMDD.csv)。"""
        (name, day), (ascii_name, ascii_day) = (part.rsplit("_", 1) for part in self.csv_filename())
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f"attachment; filename=\"{ascii_name}_summary_{ascii_day}\"; "
            f"filename*=UTF-8''{quote(f'{name}_集計_{day}', safe='')}"
        )
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerows(list_summary.csv_rows(summary))
        return response


class BaseSelectView(ScreenMixin, View):
    """候補の口。Tom Select の候補を 1 ページ返す (選ぶ欄の設計の決め)。

    columns に項目の名前を並べると、候補ごとに id と text の横にその値も返す (例 部品の unit と standard_price。
    選ぶ欄の JS が、同じ行の空の単位と単価の欄に写す = 選ぶ欄の設計の決めの「選んだら写す」)。金額の項目
    (access.is_money_name) は、金額の閲覧が無い人には返さない。
    候補ごとに text (選んだ値の欄の字。今の 1 行) の横に、2 段の字 title と sub (core.select_options.lines) も返す (開発の記録 docs/nights/20261105/N162.md)。
    字を打つ前の 1 ページ目は recent (core.select_options.recent) も返す。
    0 件のときの登録の行 create (core.select_options.create_entry。書けない人には None) も返す。
    """

    model = None
    search_fields: tuple = ()
    columns: tuple = ()
    per_page = 30
    opens_by_department = False

    def get_queryset(self):
        return self.model._default_manager.all()

    def get(self, request, *args, **kwargs):
        term = request.GET.get("q", "").strip()
        try:
            page = max(int(request.GET.get("page", "1")), 1)
        except ValueError:
            page = 1
        qs = self.get_queryset()
        if not qs.ordered:  # 並びが無いとページの境目が DB 次第になる。既定は pk の順。
            qs = qs.order_by("pk")
        qs = select_options.with_company_fields(qs)
        if term and self.search_fields:
            qs = qs.filter(search_q(self.search_fields, term))
        start = (page - 1) * self.per_page
        rows = list(qs[start : start + self.per_page + 1])
        more = len(rows) > self.per_page
        names = self.shown_columns()
        items = [self.candidate(obj, names) for obj in rows[: self.per_page]]
        recent = []
        if not term and page == 1:  # 字を打つ前の 1 ページ目だけ (開発の記録 docs/nights/20261105/N162.md)
            recent = [self.candidate(obj, names) for obj in select_options.recent(qs)]
        create = select_options.create_entry(request.user, self.model)
        return JsonResponse({"items": items, "more": more, "recent": recent, "create": create})

    def candidate(self, obj, names) -> dict:
        """候補の 1 つ。text は今の 1 行の字 (選んだ値の欄の字。変えない)。title と sub は候補の 2 段の字 (開発の記録 docs/nights/20261105/N162.md)。"""
        title, sub = select_options.lines(obj)
        return {"id": obj.pk, "text": str(obj), "title": title, "sub": sub, **{name: getattr(obj, name) for name in names}}

    def shown_columns(self) -> tuple:
        """候補に足す項目 (columns)。金額の閲覧が無い人には金額の項目を外す。

        判定は要求ごとに 1 回。記録は書かない (候補の口は字を打つたびに呼ばれる。単価が欄に見える受注の入力の画面が記録する)。
        """
        names = tuple(self.columns)
        if any(access.is_money_name(name) for name in names) and not access.can_see_money(self.request.user, record=False):
            names = tuple(name for name in names if not access.is_money_name(name))
        return names
