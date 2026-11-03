"""画面の土台 (01 の 2 節と 10.3)。

部品の画面はここの Base*View を継承する (R15)。継承した画面が上書きしてよいのは
model・ordering・columns・search_fields・tabs・form_class・service・template_name・
get_queryset・screen_name・screen_help の 11 だけ (OVERRIDABLE)。

約束:
- 一覧: 検索 (q)・並び替え (sort)・ページ送り (page・per) ・会社ごとの項目の列・同じ条件の CSV (format=csv)。
  htmx の要求には行の部分 (<app>/_rows.html か core/_rows.html) だけを返す。ただしブラウザの戻る
  (htmx の HX-History-Restore-Request) には画面の全体を返す。部分と全体は同じ URL なので Vary: HX-Request を付ける。
  合計の行は、列が合計を選んだときだけ出す (BaseListView の docstring)。
  画面の全体を描くときも、行の部分は同じ順 (<app>/_rows.html → core/_rows.html) で選ぶ (core/list.html の include)。
- 詳細: 1 件の項目と、関係する物の一覧 (tabs = ((見出し, related_name), ...))。
- 入力: form の値が通ったら、まず検査の層 (seizou.core.checks.run) を通し、それから service を呼ぶ。
  ポカよけの止めるは赤の知らせ、知らせるは黄の知らせと「このまま保存」(confirmed=1 で保存)、
  中心の決まり (RuleError) は赤の知らせ。入力の値は消さない。できたら緑の知らせと移動。
  検査の層に渡す data と service の引数は form が決める: form に service_kwargs() があればその辞書、
  無ければ cleaned_data (会社ごとの項目は ext= に分けて渡す)。検査の層の action は service の module と名前
  (例 seizou.shipping.services.ship → "shipping.ship")。
  新規登録の画面は、GET の値のうち form の欄の名前と同じ物を初期値にする (例 ?order_line=<pk>)。保存はいつもの検査と service を通る。
- 小窓: 見るだけと「詳細を見る」。template は <app>/_modal.html か core/_modal.html。
  会社ごとの項目 (一覧に出す物) と、tabs の関係する物も出す。htmx でない要求 (新しいタブ) は詳細へ移す。
- 画面の応答はブラウザに保存させない (Cache-Control: no-store。ログアウトの後の戻るで中身を見せない)。
- 候補の口: Tom Select の候補を 1 ページ (per_page 件) JSON で返す。

URL の名前の約束 (画面どうしの移動に使う。無ければ link を出さない):
  <app>:<model>_list / _detail / _create / _update / _modal
"""

from __future__ import annotations

import csv
import inspect
import unicodedata
from decimal import Decimal
from importlib import import_module
from urllib.parse import quote

from django import forms as django_forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.core.exceptions import FieldDoesNotExist
from django.db import DatabaseError, models
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import select_template
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers, patch_vary_headers
from django.views.generic import DetailView, FormView, ListView, TemplateView, View

from seizou.core import access
from seizou.core import checks
from seizou.core import dashboard
from seizou.core import fields as company_fields
from seizou.core.exceptions import CheckViolation, RuleError
from seizou.core.widgets import DecimalInput, IntegerInput, RemoteSelect

#: 継承した画面が上書きしてよい属性 (R15 の 2 文目。決まりの検査が見る)
OVERRIDABLE = (
    "model",
    "ordering",
    "columns",
    "search_fields",
    "tabs",
    "form_class",
    "service",
    "template_name",
    "get_queryset",
    "screen_name",
    "screen_help",
)

PAGE_SIZES = (20, 50, 100)
DEFAULT_PAGE_SIZE = 50
MASTER_APPS = ("masters",)

#: 一覧で右寄せにする項目の型 (IntegerField は Positive… と Small… と Big… を含む)。合計は列が選んだときだけ
NUMERIC_FIELDS = (models.DecimalField, models.IntegerField, models.FloatField)
#: 一覧で折り返さず、等幅の数字で出す項目の型 (DateTimeField は DateField の子。日時は別に見る)
DATE_FIELDS = (models.DateField,)
#: 一覧で等幅の字で出す識別子 (品番・番号・コード) の項目の名前と、名前の末尾
CODE_NAMES = ("code",)
CODE_SUFFIXES = ("_no", "_code")
#: 詳細で絵として出すファイルの拡張子
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")


class Notice:
    """赤の知らせの中身 (例外でない物を同じ形で描くため)。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


def url_or_none(name: str, *args) -> str | None:
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return None


def model_url(model, kind: str, *args) -> str | None:
    meta = model._meta
    return url_or_none(f"{meta.app_label}:{meta.model_name}_{kind}", *args)


def is_master(value) -> bool:
    meta = getattr(value, "_meta", None)
    return meta is not None and meta.app_label in MASTER_APPS


def display_value(obj, name: str):
    """項目の表示の値。choices は表示名、FK は相手、拡張の表は ext__<列> で読む。"""
    if name.startswith(company_fields.EXT_PREFIX):
        return company_fields.ext_value(obj, name[len(company_fields.EXT_PREFIX):])
    getter = getattr(obj, f"get_{name}_display", None)
    if callable(getter):
        return getter()
    value = obj
    for part in name.split("__"):
        value = getattr(value, part, None)
        if value is None:
            return None
    return value() if callable(value) else value


def screen_value(value):
    """画面に出す形の値。真偽は「はい」「いいえ」にする (True / False を画面に出さない)。ほかはそのまま。"""
    if isinstance(value, bool):
        return "はい" if value else "いいえ"
    return value


def is_negative(value) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool) and value < 0


def file_info(value, url: str | None = None) -> dict | None:
    """FileField の値から link の材料 (url・名前・絵かどうか) を作る。空なら None。

    url を渡せばその URL を使う (ログインした人にだけ返す口など)。無ければ storage の URL。
    """
    name = getattr(value, "name", "") or ""
    if not name:
        return None
    if url is None:
        try:
            url = value.url
        except ValueError:
            return None
    return {"url": url, "name": name.rsplit("/", 1)[-1], "is_image": name.lower().endswith(IMAGE_SUFFIXES)}


def file_url_for(obj, field_name: str) -> str | None:
    """model の file_url(field_name) があれば、その答え (空でない字) を返す。無ければ None (storage の URL を使う)。

    図面や添付のように、ログインした人にだけ返す口を持つ model が、core のテンプレートを触らずに
    詳細の画面のファイルの URL を差し替えるための約束。
    """
    hook = getattr(obj, "file_url", None)
    if not callable(hook):
        return None
    return hook(field_name) or None


def hidden_detail_fields(obj) -> frozenset:
    """詳細と小窓に出さない項目の名前。model の detail_hidden_fields (名前の並び) があればそれ、無ければ空。

    表示用の絵や変換の誤りの文のように、仕組みのために持つ列 (入力の画面に無い物) を、
    利用者の目に触れる一覧から外すための約束。入力の form と services には関係しない。
    """
    return frozenset(getattr(obj, "detail_hidden_fields", ()) or ())


class ScreenMixin(LoginRequiredMixin):
    """画面の名前と 1 文。画面の目録 (manage.py seizou_screens)・説明書・MCP の一覧が読む。

    1 文 (screen_help) は画面の題の下には出さない (決め 11-03 14:1x: 画面はシンプルに。「?」で説明書の断片を見る)。
    """

    screen_name: str = ""
    screen_help: str = ""
    screen_icon: str = ""  # 題の横の絵の名前 (seizou_icons)。空なら app の絵。core 自身の画面 (検索など) が使う
    opens_by_department = True  # 一覧と入力と盤は部署で開けるかを見る。詳細・小窓・候補の口は見ない

    @classmethod
    def screen_app(cls) -> str:
        """画面の部品の app の名前 (module の名前から。seizou.masters.views → masters)。見出しの絵に使う。"""
        parts = cls.__module__.split(".")
        return parts[-2] if len(parts) >= 2 else parts[-1]

    def dispatch(self, request, *args, **kwargs):
        denied = access.screen_denied(request, self.screen_app(), by_department=self.opens_by_department)
        if denied is not None:
            add_never_cache_headers(denied)
            return denied
        response = super().dispatch(request, *args, **kwargs)
        # ログアウトの後にブラウザの戻るで画面の中身が見えないように、画面の応答は保存させない (never_cache と同じ)。
        # 静的なファイル (static) は画面でないので、ここを通らない。
        add_never_cache_headers(response)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["screen_name"] = self.screen_name
        context["screen_help"] = self.screen_help
        context["screen_app"] = self.screen_app()
        context["screen_icon"] = self.screen_icon
        context["screen_anchor"] = (getattr(self.request.resolver_match, "view_name", "") or "").replace(":", "_") if hasattr(self, "request") else ""
        return context


class ColumnSpec:
    """一覧の列 1 つ。テンプレートが見る印: numeric (右寄せ)・total (合計の行に足す)・is_date / is_datetime (等幅の日付)・
    is_code (等幅の識別子)。

    列の書き方 (BaseListView.columns の 1 要素):
      "part_no"                                      項目の名前だけ (見出しは verbose_name。合計なし)
      ("order_line__item", "部品")                   名前と見出し (合計なし)
      {"name": "amount", "label": "金額", "total": True}   辞書。total が真で、表の数の項目なら合計の行に足す (label は省ける)
      {"name": "shipped_total", "label": "出荷済み", "kind": "number", "sortable": True, "total": True}   辞書の全部の鍵

    辞書の鍵:
      kind      number (右寄せ・負は赤) / money (右寄せ。金額の閲覧が無い人には「—」。CSV から外す) / date (等幅の Y-m-d)。
                書かなければ表の項目の型で決める (会社ごとの項目は型の number / date)。ほかの字は ValueError
      sortable  真なら見出しが並び替えの link になる。書かなければ表の項目と会社ごとの項目だけ並び替える
      total     真なら合計の行に足す
    表の項目でない名前 (annotate の列) に sortable か total を書くと、土台は order_by と Sum にその名前を渡す
    (queryset の annotate にある約束。無ければ何もしない)。board__<名前> のような Python の値 (model の property) には
    kind だけを書く (sortable・total を書かない)。

    印:
      numeric      右寄せと等幅の数字 (表の数の項目・kind が number / money・会社ごとの項目の数)。負は赤
      money        金額の列 (kind が money か、名前の最後が access.MONEY_FIELDS)。金額の閲覧の判定で欄・合計・CSV を「—」にする
      total        合計の行に足す (表の数の項目と、kind が number / money の annotate の列)
      is_date      等幅の Y-m-d (表の日付の項目・kind が date・会社ごとの項目の日付)
      is_datetime  等幅の Y-m-d H:i (表の日時の項目)
      is_code      等幅の識別子 (数でない、名前が code か _no / _code で終わる列)
      sortable     見出しが並び替えの link
      annotated    表の項目でない名前に sortable か total の印が付いた列 (annotate の列として order_by と Sum に渡す)
    """

    #: 辞書の kind に書ける値
    KINDS = ("number", "money", "date")

    def __init__(self, model, item, ext_type: str | None = None):
        self.total = False
        kind = None
        sortable = None
        if isinstance(item, dict):
            self.name, self.label = item["name"], item.get("label")
            self.total = bool(item.get("total"))
            kind = item.get("kind")
            if "sortable" in item:
                sortable = bool(item["sortable"])
        elif isinstance(item, (tuple, list)):
            self.name, self.label = item[0], item[1]
        else:
            self.name, self.label = item, None
        if kind is None and ext_type in ("number", "date"):
            kind = ext_type
        if kind is not None and kind not in self.KINDS:
            raise ValueError(f"知らない列の種類: {kind!r} ({self.name})。number / money / date のどれかにしてください。")
        self.kind = kind
        is_ext = self.name.startswith(company_fields.EXT_PREFIX)
        self.field = None
        if not is_ext and "__" not in self.name:
            try:
                self.field = model._meta.get_field(self.name)
            except Exception:  # noqa: BLE001 - 項目でない名前 (関数など) は並び替えない
                self.field = None
        if self.label is None:
            self.label = str(self.field.verbose_name) if self.field is not None else self.name
        self.sortable = sortable if sortable is not None else (self.field is not None or is_ext)
        self.desc_name = "-" + self.name
        self.numeric = self.kind in ("number", "money") or isinstance(self.field, NUMERIC_FIELDS) or ext_type == "number"
        self.money = self.kind == "money" or access.is_money_name(self.name)
        self.is_datetime = isinstance(self.field, models.DateTimeField)
        self.is_date = (
            self.kind == "date" or (isinstance(self.field, DATE_FIELDS) and not self.is_datetime) or ext_type == "date"
        )
        self.is_code = not self.numeric and (self.name in CODE_NAMES or self.name.endswith(CODE_SUFFIXES))
        self.annotated = (self.sortable or self.total) and self.field is None and not is_ext

    def order_key(self) -> str:
        if self.name.startswith(company_fields.EXT_PREFIX):
            return f"{company_fields.EXT_ACCESSOR}__{self.name[len(company_fields.EXT_PREFIX):]}"
        return self.name


class BaseListView(ScreenMixin, ListView):
    """一覧の土台。サーバーが行を描く。既定は 1 ページ 50 件 (20 / 50 / 100)。

    合計の行は既定では出さない (締日や単価を足しても意味が無いため)。合計を出したい列は辞書で書く:
        columns = ("order_no", {"name": "amount", "label": "金額", "total": True})
    合計に足すのは total が真で、表の数の項目 (DecimalField・IntegerField・FloatField) の列と、kind が number / money の
    annotate の列 (部品の get_queryset が annotate した名前) だけ。
    annotate の列の並び替えは、部品の get_queryset が annotate を足した後 (paginate_queryset と csv_response) で当てる
    (Django の土台は get_ordering の並びを部品の get_queryset の super() の中、annotate より先に当てるため)。
    """

    columns: tuple = ()
    search_fields: tuple = ()
    ordering = None
    paginate_by = DEFAULT_PAGE_SIZE
    page_sizes = PAGE_SIZES
    template_name = "core/list.html"

    # --- 列 ---
    def column_specs(self) -> list[ColumnSpec]:
        specs = [ColumnSpec(self.model, item) for item in self.columns]
        for col in company_fields.list_columns_for(self.model):
            specs.append(ColumnSpec(self.model, (company_fields.EXT_PREFIX + col.name, col.label), ext_type=col.type))
        return specs

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
            return ("-" if sort.startswith("-") else "") + spec.order_key()
        return self.ordering or "-pk"

    def order_annotated(self, queryset):
        """annotate の列の並び替えを、部品の get_queryset の後の queryset に当てる。annotate に無い名前は並び替えない。"""
        sort = self.annotated_sort()
        name = sort.lstrip("-")
        if sort and name in queryset.query.annotations:
            return queryset.order_by(("-" if sort.startswith("-") else "") + name)
        return queryset

    def paginate_queryset(self, queryset, page_size):
        # 部品の get_queryset が annotate を足した後の queryset (self.object_list) がここに来る。
        return super().paginate_queryset(self.order_annotated(queryset), page_size)

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
        return [*self.search_fields, *company_fields.search_fields_for(self.model)]

    def get_queryset(self):
        qs = super().get_queryset()
        lookups = self.search_lookups()
        for term in self.search_terms():
            if not lookups:
                break
            qs = qs.filter(search_q(lookups, term))
        return qs

    # --- 行 ---
    def row_for(self, obj, specs) -> dict:
        cells = []
        for spec in specs:
            value = display_value(obj, spec.name)
            if spec.money and not access.can_see_money(self.request.user):
                value = access.MASK
            master = value if is_master(value) else None
            # マスター自身の一覧では、名前の字を押すと自分の小窓が開く (決め 11-02 12:5x)。
            if master is None and spec.name == "name" and is_master(obj):
                master = obj
            cells.append(
                {
                    "value": screen_value(value),
                    "master": master,
                    "numeric": spec.numeric,
                    "negative": spec.numeric and is_negative(value),
                    "date": spec.is_date,
                    "datetime": spec.is_datetime,
                    "code": spec.is_code,
                }
            )
        return {"pk": obj.pk, "obj": obj, "cells": cells, "url": model_url(self.model, "detail", obj.pk)}

    def totals_for(self, queryset, specs) -> list | None:
        """絞り込みに追従する合計の行 (合計を選んだ、表の数の項目と、kind が number / money の annotate の列。
        無ければ出さない)。1 列 = {"value", "numeric"}。annotate の列は queryset の annotate にある名前だけ足す。"""
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
        masked = {spec.name for spec in specs if spec.money and spec.name in sums}
        if masked and not access.can_see_money(self.request.user):
            agg = {name: access.MASK if name in masked else value for name, value in agg.items()}
        return [{"value": agg.get(spec.name), "numeric": spec.name in sums} for spec in specs]

    def search_hint(self) -> str:
        """検索の欄の案内 (例「自社品番・品名・図面番号・材質」)。search_fields の見出しと、検索に使う会社ごとの項目の見出し。"""
        labels = [lookup_label(self.model, f) for f in self.search_fields]
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
        context["rows"] = [self.row_for(obj, specs) for obj in context["object_list"]]
        context["totals"] = self.totals_for(self.get_queryset(), specs)
        context["search_q"] = self.request.GET.get("q", "")
        context["search_hint"] = self.search_hint()
        context["sort"] = self.sort_param()
        context["per"] = self.get_paginate_by(None)
        context["page_sizes"] = self.page_sizes
        context["query_without_page"] = self.query_without("page", "format")
        context["query_without_sort"] = self.query_without("sort", "page", "format")
        context["create_url"] = model_url(self.model, "create")
        if context["create_url"] and not access.can_write_model(self.request.user, self.model, "create"):
            context["create_url"] = None
        context["rows_template_names"] = self.rows_template_names()
        return context

    def rows_template_names(self) -> list[str]:
        return [f"{self.model._meta.app_label}/_rows.html", "core/_rows.html"]

    def wants_rows_only(self) -> bool:
        """行の部分だけを返すか。htmx の要求でも、ブラウザの戻る (履歴の復元) には画面の全体を返す
        (htmx は復元の要求にも HX-Request を付け、応答で body を丸ごと差し替えるため)。"""
        headers = self.request.headers
        return bool(headers.get("HX-Request")) and not headers.get("HX-History-Restore-Request")

    def render_to_response(self, context, **response_kwargs):
        if self.request.GET.get("format") == "csv":
            return self.csv_response(context)
        if self.wants_rows_only():
            # partial: 行の部分だけを返す印。core/_rows.html はこのとき道具の帯の件数と CSV も差し替える (hx-swap-oob)。
            context["partial"] = True
            template = select_template(self.rows_template_names())
            response = HttpResponse(template.render(context, self.request))
        else:
            response = super().render_to_response(context, **response_kwargs)
        # 同じ URL で部分と全体を返すので、ブラウザの保存が取り違えないように見出しで分ける。
        patch_vary_headers(response, ("HX-Request",))
        return response

    def csv_filename(self) -> tuple[str, str]:
        """CSV のファイルの名前 (画面の名前_YYYYMMDD.csv) と、ASCII だけの代わりの名前 (<model>_YYYYMMDD.csv)。"""
        today = timezone.localdate().strftime("%Y%m%d")
        name = self.screen_name or str(self.model._meta.verbose_name)
        return f"{name}_{today}.csv", f"{self.model._meta.model_name}_{today}.csv"

    def csv_response(self, context) -> HttpResponse:
        specs = context["columns"]
        if not access.can_see_money(self.request.user):
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
            writer.writerow(["" if v is None else str(v) for v in (display_value(obj, s.name) for s in specs)])
        return response


class BaseDetailView(ScreenMixin, DetailView):
    """詳細の土台。1 件と、関係する物の一覧 (tabs = ((見出し, related_name), ...))。"""

    tabs: tuple = ()
    opens_by_department = False
    template_name = "core/detail.html"

    def field_items(self, obj) -> list[dict]:
        """項目の一覧。1 項目 = {"label", "value", "master", "file"} (file はファイルの項目だけ、file_info の形)。"""
        items = []
        hidden = hidden_detail_fields(obj)
        for f in obj._meta.concrete_fields:
            if f.primary_key or f.name in ("password",) or f.name in hidden:
                continue
            value = display_value(obj, f.name)
            if access.is_money_name(f.name) and not access.can_see_money(self.request.user):
                value = None
            is_file = isinstance(f, models.FileField)
            items.append(
                {
                    "label": str(f.verbose_name),
                    "value": "" if is_file else screen_value(value),
                    "master": value if is_master(value) else None,
                    "file": file_info(value, file_url_for(obj, f.name)) if is_file else None,
                }
            )
            # 受注の明細のような相手の表が部品を持つなら、部品の行を足して小窓で開けるようにする (初見テスト 2 回目の E-05)
            linked = getattr(value, "item", None) if value is not None and not is_master(value) else None
            if is_master(linked) and not any(c.name == "item" for c in obj._meta.concrete_fields):
                items.append({"label": "部品", "value": screen_value(linked), "master": linked, "file": None})
        for col in company_fields.columns_for(obj.__class__):
            value = screen_value(company_fields.ext_value(obj, col.name))
            items.append({"label": col.label, "value": value, "master": None, "file": None})
        return items

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = context["object"]
        fields = self.field_items(obj)
        context["fields"] = fields
        context["files"] = [item for item in fields if item["file"]]
        context["tabs"] = [self.tab_for(obj, label, related) for label, related in self.tabs if hasattr(obj, related)]
        context["edit_url"] = model_url(obj.__class__, "update", obj.pk)
        if context["edit_url"] and not access.can_write_model(self.request.user, obj.__class__, "update"):
            context["edit_url"] = None
        context["list_url"] = model_url(obj.__class__, "list")
        context["status"] = self.status_for(obj)
        return context

    def tab_for(self, obj, label: str, related: str) -> dict:
        return related_tab(obj, label, related)

    def status_for(self, obj) -> str | None:
        """見出しの横の状態の印。choices を持つ status の項目があればその表示名、無ければ None。"""
        field = next((f for f in obj._meta.concrete_fields if f.name == "status" and f.choices), None)
        return display_value(obj, "status") if field is not None else None


def related_tab(obj, label: str, related: str) -> dict:
    """関係する物の 1 つのタブ (詳細と小窓)。{"label", "related", "count", "rows"}。行は最大 100 件、1 行 = {"obj", "url"}。"""
    manager = getattr(obj, related)
    rows = [{"obj": row, "url": model_url(row.__class__, "detail", row.pk)} for row in manager.all()[:100]]
    return {"label": label, "related": related, "count": manager.count(), "rows": rows}


class BaseFormView(ScreenMixin, FormView):
    """入力の土台。form の値が通ったら検査の層を通し、service を呼ぶ。土台が ModelForm の save を呼ぶことは無い。"""

    model = None
    form_class = None
    service = None
    template_name = "core/form.html"
    success_message = "保存しました。"
    object = None

    # --- 対象 (直すとき) ---
    def dispatch(self, request, *args, **kwargs):
        if self.model is not None and "pk" in kwargs:
            # 部品の画面が get_queryset を持つときは、その絞りの中から対象を引く (絞りの外は 404)。土台は get_queryset を持たない。
            # ログインの前は利用者で絞れないので表の全部から引く (この後の ScreenMixin・LoginRequiredMixin の門がログインへ移す)。
            get_queryset = getattr(self, "get_queryset", None)
            source = get_queryset() if get_queryset is not None and request.user.is_authenticated else self.model
            self.object = get_object_or_404(source, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        """form の初期値。class の initial の写しに、新規登録の画面の GET の値を足す。

        受注の明細から出荷の新規登録・指示書の発行へ、明細を選んだ形で移る入口のため (例 ?order_line=<pk>)。
        写すのは GET の鍵の全部 (値は字のまま。同じ鍵が 2 つあれば後の物)。form の欄に無い鍵も写すので、form の __init__ が
        self.initial.get("from_line") のように読める (注残の盤の「発注する」が明細の pk を渡す入口のため。決め 11-03)。
        写さない物: POST の要求 (送られた値が正)・編集の画面 (対象の値が正)・会社ごとの項目 (ext__)。
        値の検査はしない。保存のときに form と検査の層と service が見る (数でない値や無い pk は、選ぶ欄が何も選ばないだけ)。
        """
        initial = super().get_initial()
        if self.request.method != "GET" or self.object is not None:
            return initial
        for name in self.request.GET:
            if not name.startswith(company_fields.EXT_PREFIX):
                initial[name] = self.request.GET.get(name)
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.object is not None and _accepts_instance(self.form_class):
            kwargs["instance"] = self.object
        if access.takes_hide_money(self.form_class) and not access.can_see_money(self.request.user):
            kwargs["hide_money"] = True
        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        model = self.model or getattr(getattr(form, "_meta", None), "model", None)
        if model is not None:
            for name, field in company_fields.form_fields_for(model).items():
                form.fields[name] = field
                if self.object is not None and not form.is_bound:
                    form.initial[name] = company_fields.ext_value(self.object, name[len(company_fields.EXT_PREFIX):])
        for field in form.fields.values():
            use_decimal_input(field)
            field.widget.attrs.setdefault("class", widget_css(field.widget))
        return form

    # --- 検査の層と service ---
    def get_action(self) -> str:
        service = self.get_service()
        module = service.__module__.split(".")
        app = module[-2] if len(module) >= 2 else module[-1]
        return f"{app}.{service.__name__}"

    def get_service(self):
        service = self.service
        if service is None:
            raise RuntimeError(f"{type(self).__name__} に service が無い")
        return service

    def service_kwargs(self, form) -> dict:
        if hasattr(form, "service_kwargs"):
            return dict(form.service_kwargs())
        data, ext = company_fields.split_ext(form.cleaned_data)
        kwargs = {"data": data}
        if ext is not None:
            kwargs["ext"] = ext
        return kwargs

    def call_service(self, form):
        kwargs = self.service_kwargs(form)
        args = (self.object,) if self.object is not None else ()
        return self.get_service()(*args, user=self.request.user, **kwargs)

    def form_valid(self, form):
        try:
            access.require_write(self.request.user, self.get_action())
        except access.AccessDenied as exc:
            return self.render_to_response(self.get_context_data(form=form, notice=exc))
        kwargs = self.service_kwargs(form)
        check_data = {**form.cleaned_data, **{k: v for k, v in kwargs.items() if k != "data"}, **kwargs.get("data", {})}
        if self.object is not None:
            check_data.setdefault("object", self.object)
        try:
            warnings = checks.run(self.get_action(), check_data, self.request.user)
        except CheckViolation as exc:
            return self.render_to_response(self.get_context_data(form=form, notice=exc))
        if warnings and self.request.POST.get("confirmed") != "1":
            return self.render_to_response(self.get_context_data(form=form, warnings=warnings))
        try:
            result = self.call_service(form)
        except RuleError as exc:
            return self.render_to_response(self.get_context_data(form=form, notice=exc))
        except ValueError as exc:
            # 部品の決まり (番号の無い物。品番の一意など) は日本語の文の ValueError。赤の知らせにする。
            return self.render_to_response(self.get_context_data(form=form, notice=Notice("入力", str(exc))))
        messages.success(self.request, self.success_message)
        url = self.get_success_url_for(result)
        if self.request.headers.get("HX-Request"):
            response = HttpResponse(status=200)
            response["HX-Redirect"] = url
            return response
        return HttpResponseRedirect(url)

    def get_success_url_for(self, result) -> str:
        if getattr(self, "success_url", None):
            return str(self.success_url)
        if result is not None and hasattr(result, "_meta") and getattr(result, "pk", None) is not None:
            url = model_url(result.__class__, "detail", result.pk)
            if url:
                return url
        if self.model is not None:
            url = model_url(self.model, "list")
            if url:
                return url
        return "/"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("notice", None)
        context.setdefault("warnings", [])
        context["object"] = self.object
        context["cancel_url"] = model_url(self.model, "list") if self.model is not None else None
        return context


def use_decimal_input(field) -> None:
    """数の欄の数の入力 (type="number") を、字の欄に替える (小数は DecimalInput、整数は IntegerInput)。

    type="number" はブラウザが「12e」のような字を空にして送り、空のまま保存されるため (整数の欄 = 締日なども同じ。
    初見テスト 2 回目の所見)。字の欄にして、誤りはサーバーの検査が日本語で出す (整数の下限と上限は
    ServiceModelForm が表の検査から form の検査に付けてある)。全角の数字と「1,200」は半角の数にそろえて通す。
    """
    if isinstance(field, (django_forms.DecimalField, django_forms.FloatField)):
        make = DecimalInput
    elif isinstance(field, django_forms.IntegerField):
        make = IntegerInput
    else:
        return
    old = field.widget
    if not isinstance(old, django_forms.NumberInput):
        return
    attrs = {k: v for k, v in old.attrs.items() if k not in ("step", "min", "max")}
    widget = make(attrs=attrs)
    widget.is_required = old.is_required
    widget.is_localized = old.is_localized
    field.widget = widget


def _accepts_instance(form_class) -> bool:
    """form が instance= を受けるか。ModelForm (ServiceModelForm を含む) か、__init__ が instance の引数を名指ししている物だけ。

    以前は form_class.__init__ の co_varnames を見ていたが、*args と **kwargs だけの __init__ (ServiceModelForm) を足したときに
    編集の画面が空で開く不具合になったので、形で判断する。
    """
    if issubclass(form_class, django_forms.ModelForm):
        return True
    try:
        parameters = inspect.signature(form_class.__init__).parameters
    except (TypeError, ValueError):
        return False
    return "instance" in parameters


def search_variants(term: str) -> list[str]:
    """検索の語の揺れ。全角と半角 (NFKC)・ひらがなとカタカナを同じに扱う (初見テスト 2 回目の所見)。

    返す物は重ねない並び (打った字・NFKC・カタカナ・ひらがな)。DB の側は変えず、OR で引く。
    """
    folded = unicodedata.normalize("NFKC", term)
    kata = "".join(chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch for ch in folded)
    hira = "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in folded)
    return list(dict.fromkeys([term, folded, kata, hira]))


def search_q(lookups, term: str) -> Q:
    """lookups のどれかが term (の揺れ) を含む Q。"""
    q = Q()
    for lookup in lookups:
        for variant in search_variants(term):
            q |= Q(**{f"{lookup}__icontains": variant})
    return q


def lookup_label(model, lookup: str) -> str:
    """検索の案内に出す欄の名前。`customer__name` のように辿る物は末尾の欄の名前 (相手の表の verbose_name)。"""
    current = model
    field = None
    for part in lookup.split("__"):
        try:
            field = current._meta.get_field(part)
        except FieldDoesNotExist:
            return part
        related = getattr(field, "related_model", None)
        if related is None:
            break
        current = related
    return str(getattr(field, "verbose_name", lookup))


def widget_css(widget) -> str:
    """欄の見た目の class (daisyUI)。form が class を持っていればそちらが勝つ (setdefault)。"""
    from django.forms import widgets

    if isinstance(widget, widgets.CheckboxInput):
        return "checkbox"
    if isinstance(widget, RemoteSelect):
        # 字を打って探す欄 (Tom Select) には daisyUI の select の class を付けない。Tom Select が包みに class を写し、
        # select の見た目 (overflow と矢印) が候補の一覧を隠して二重の枠と矢印になるため (初見テスト 2 回目の所見)。
        # 包みの見た目は scripts/css/input.css の .ts-wrapper の決まりで input にそろえる。
        return "w-full"
    if isinstance(widget, (widgets.Select, widgets.SelectMultiple)):
        return "select w-full"
    if isinstance(widget, widgets.Textarea):
        return "textarea w-full"
    if isinstance(widget, widgets.FileInput):
        return "file-input w-full"
    return "input w-full"


class BaseModalView(ScreenMixin, DetailView):
    """小窓の土台。見るだけと、詳細への移動のボタン。template は <app>/_modal.html か core/_modal.html。

    項目 (先頭の modal_fields 個) の後に、会社ごとの項目 (一覧に出す物。一覧の列と同じ取り方) を見出しと値で並べる。
    tabs = ((見出し, related_name), ...) があれば、関係する物の一覧も出す (詳細のタブと同じ形)。
    htmx でない要求 (link を新しいタブで開いた時) には、枠の無い中身でなく詳細の画面へ移す。
    """

    modal_fields: int = 8
    tabs: tuple = ()
    opens_by_department = False

    def get(self, request, *args, **kwargs):
        if not request.headers.get("HX-Request"):
            obj = self.get_object()
            url = model_url(obj.__class__, "detail", obj.pk)
            if url:
                return HttpResponseRedirect(url)
        return super().get(request, *args, **kwargs)

    def get_template_names(self):
        if self.template_name:
            return [self.template_name]
        return [f"{self.model._meta.app_label}/_modal.html", "core/_modal.html"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = context["object"]
        items = []
        hidden = hidden_detail_fields(obj)
        for f in obj._meta.concrete_fields:
            if f.primary_key or f.name in hidden:
                continue
            value = display_value(obj, f.name)
            if access.is_money_name(f.name) and not access.can_see_money(self.request.user):
                value = None
            if isinstance(f, models.FileField):
                info = file_info(value)
                value = info["name"] if info else ""
            items.append({"label": str(f.verbose_name), "value": screen_value(value)})
        ext_items = [
            {"label": col.label, "value": screen_value(company_fields.ext_value(obj, col.name))}
            for col in company_fields.list_columns_for(obj.__class__)
        ]
        context["fields"] = items[: self.modal_fields] + ext_items
        context["tabs"] = [related_tab(obj, label, related) for label, related in self.tabs if hasattr(obj, related)]
        context["detail_url"] = model_url(obj.__class__, "detail", obj.pk)
        return context


class BaseSelectView(ScreenMixin, View):
    """候補の口。Tom Select の候補を 1 ページ返す (05 の 2 節)。"""

    model = None
    search_fields: tuple = ()
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
        if term and self.search_fields:
            qs = qs.filter(search_q(self.search_fields, term))
        start = (page - 1) * self.per_page
        rows = list(qs[start : start + self.per_page + 1])
        more = len(rows) > self.per_page
        items = [{"id": obj.pk, "text": str(obj)} for obj in rows[: self.per_page]]
        return JsonResponse({"items": items, "more": more})


class RememberLoginView(LoginView):
    """ログイン。「ログイン状態を保存する」が外れていたら、ブラウザを閉じたときに切れる session にする。

    check が入っていれば session の長さは設定の既定 (SESSION_COOKIE_AGE) のまま。
    """

    template_name = "core/login.html"

    def form_valid(self, form):
        # ログインが session の鍵を作り直すので、その後に長さを決める。
        response = super().form_valid(form)
        if not self.request.POST.get("remember"):
            self.request.session.set_expiry(0)
        return response


def load_model(path: str):
    """"seizou.masters.models.Item" の形の名前から model を返す。部品が無ければ None。"""
    module_name, _sep, attr = path.rpartition(".")
    try:
        return getattr(import_module(module_name), attr)
    except (ImportError, AttributeError):
        return None


def count_or_none(queryset) -> int | None:
    try:
        return queryset.count()
    except DatabaseError:
        return None


def dashboard_stats() -> list[dict]:
    """最初の画面の数字のカード。部品が無い物は出さず、数は本物の集計だけ (DB が読めない物も出さない)。

    1 枚 = {"label", "value", "unit", "note", "icon", "url"}。金額のカード (支払の見込みと入金) は "money" が真で、
    HomeView が金額を見られない人に "hidden" を付け、画面は数を「—」にする (カードは残す)。
    """
    today = timezone.localdate()
    stats: list[dict] = []
    item_model = load_model("seizou.masters.models.Item")
    if item_model is not None:
        stats.append(
            {
                "label": "部品",
                "value": count_or_none(item_model._default_manager.all()),
                "unit": "件",
                "note": "部品マスターにある部品の数",
                "icon": "box",
                "url": url_or_none("masters:item_list"),
            }
        )
    partner_model = load_model("seizou.masters.models.Partner")
    if partner_model is not None:
        stats.append(
            {
                "label": "取引先",
                "value": count_or_none(partner_model._default_manager.all()),
                "unit": "社",
                "note": "得意先と協力企業の数",
                "icon": "users",
                "url": url_or_none("masters:partner_list"),
            }
        )
    order_model = load_model("seizou.orders.models.Order")
    if order_model is not None:
        this_month = order_model._default_manager.filter(
            order_date__year=today.year, order_date__month=today.month
        ).exclude(status=order_model.STATUS_CANCELLED)
        stats.append(
            {
                "label": "今月の受注",
                "value": count_or_none(this_month),
                "unit": "件",
                "note": f"{today.month} 月に受けた受注の件数 (取消を除く)",
                "icon": "clipboard",
                "url": url_or_none("orders:order_list"),
            }
        )
    line_model = load_model("seizou.orders.models.OrderLine")
    if line_model is not None and order_model is not None:
        open_lines = line_model._default_manager.filter(
            order__status__in=(order_model.STATUS_OPEN, order_model.STATUS_PARTIAL)
        )
        stats.append(
            {
                "label": "まだ出荷していない明細",
                "value": count_or_none(open_lines),
                "unit": "行",
                "note": "出荷が終わっていない受注の明細の数",
                "icon": "activity",
                "url": url_or_none("orders:order_list"),
            }
        )
    inbox_model = load_model("seizou.connect.models.Inbox")
    if inbox_model is not None:
        waiting = inbox_model._default_manager.filter(status=inbox_model.STATUS_RECEIVED)
        stats.append(
            {
                "label": "届いた注文 (未処理)",
                "value": count_or_none(waiting),
                "unit": "件",
                "note": "人が受け入れるのを待つ注文の数",
                "icon": "inbox",
                "url": url_or_none("connect:inbox_list"),
            }
        )
    # 6 枚目: 今月の支払の見込み (受入の部品があるとき。受け入れた数 × 発注の単価の合計 = N42 の要望 4)
    payable = dashboard.payable_this_month(today)
    if payable is not None:
        stats.append(
            {
                "label": "今月の支払の見込み",
                "value": payable,
                "unit": "円",
                "note": f"{today.month} 月に受け入れた数量 × 発注の単価の合計",
                "icon": "yen",
                "url": url_or_none("purchasing:receipt_list"),
                "money": True,
            }
        )
    # 7 枚目: 入金 (入金の表 = billing の Payment があるとき。入金日が今月の、取消でない入金の金額の合計 = N56 の要望)
    payments = dashboard.payments_this_month(today)
    if payments is not None:
        stats.append(
            {
                "label": "今月の入金",
                "value": payments,
                "unit": "円",
                "note": f"{today.month} 月に入金日のある入金の金額の合計 (取消を除く)",
                "icon": "yen",
                "url": url_or_none("billing:invoice_list"),
                "money": True,
            }
        )
    return [stat for stat in stats if stat["value"] is not None]


def recent_orders(limit: int = 5) -> list[dict] | None:
    """最近の受注 (直近 limit 件)。受注の部品が無いか DB が読めなければ None。

    1 件 = {"obj", "url", "total", "line_count", "status"}。total は明細の金額の合計 (読むだけ)、
    status は状態の値 (open / partial / shipped / cancelled。印の色を選ぶのに使う)。
    """
    order_model = load_model("seizou.orders.models.Order")
    if order_model is None:
        return None
    try:
        rows = list(
            order_model._default_manager.select_related("customer")
            .annotate(total=Sum("lines__amount"), line_count=Count("lines"))
            .order_by("-order_date", "-pk")[:limit]
        )
    except DatabaseError:
        return None
    return [
        {
            "obj": row,
            "url": model_url(order_model, "detail", row.pk),
            "total": row.total,
            "line_count": row.line_count,
            "status": row.status,
        }
        for row in rows
    ]


class HomeView(ScreenMixin, TemplateView):
    """最初の画面 (ログインの後の移動先。全員が意識する KPI の置き場)。上から 数字のカード → 出荷と生産の図 → 最近の受注と納期アラート → よく使う画面 (☆ で登録した画面)。

    使う人: 全員 / いつ: ログイン直後と朝 / 何のために: 今日の数字と、出荷と生産の図、納期の遅れを見る / 次にすること: 遅れの行から注文残へ。☆ で登録した画面へ
    """

    screen_name = "ダッシュボード"
    screen_help = "今日の数字と、出荷と生産の図、最近の受注と納期の遅れ。上の帯の ☆ で登録した画面が「よく使う画面」に並びます。"
    template_name = "core/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["stats"] = dashboard_stats()
        context["recent_orders"] = recent_orders()
        context["orders_url"] = url_or_none("orders:order_list")
        context["alerts"] = dashboard.due_alerts()
        context["shipping"] = dashboard.shipping_trend()
        context["production"] = dashboard.production_trend()
        if not access.can_see_money(self.request.user):
            context["recent_orders"] = access.without_totals(context["recent_orders"])
            context["shipping"] = dashboard.without_amounts(context["shipping"])
            for stat in context["stats"]:
                if stat.get("money"):
                    stat["hidden"] = True  # 金額のカードの数を「—」にする (カードは残す)
        return context


def decimal_or_none(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None
