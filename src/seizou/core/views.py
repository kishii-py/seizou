"""画面の土台 (01 の 2 節と 10.3)。

部品の画面はここの Base*View を継承する (R15)。継承した画面が上書きしてよいのは
columns・search_fields・tabs・form_class・service・template_name・get_queryset・
screen_name・screen_help の 9 つだけ (OVERRIDABLE)。

約束:
- 一覧: 検索 (q)・並び替え (sort)・ページ送り (page・per) ・会社ごとの項目の列・同じ条件の CSV (format=csv)。
  htmx の要求には行の部分 (<app>/_rows.html か core/_rows.html) だけを返す。
- 詳細: 1 件の項目と、関係する物の一覧 (tabs = ((見出し, related_name), ...))。
- 入力: form の値が通ったら、まず検査の層 (seizou.core.checks.run) を通し、それから service を呼ぶ。
  ポカよけの止めるは赤の知らせ、知らせるは黄の知らせと「このまま保存」(confirmed=1 で保存)、
  中心の決まり (RuleError) は赤の知らせ。入力の値は消さない。できたら緑の知らせと移動。
  検査の層に渡す data と service の引数は form が決める: form に service_kwargs() があればその辞書、
  無ければ cleaned_data (会社ごとの項目は ext= に分けて渡す)。検査の層の action は service の module と名前
  (例 seizou.shipping.services.ship → "shipping.ship")。
- 小窓: 見るだけと「詳細を開く」。template は <app>/_modal.html か core/_modal.html。
- 候補の口: Tom Select の候補を 1 ページ (per_page 件) JSON で返す。

URL の名前の約束 (画面どうしの移動に使う。無ければ link を出さない):
  <app>:<model>_list / _detail / _create / _update / _modal
"""

from __future__ import annotations

import csv
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import models
from django.db.models import Q
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import select_template
from django.urls import NoReverseMatch, reverse
from django.views.generic import DetailView, FormView, ListView, TemplateView, View

from seizou.core import checks
from seizou.core import fields as company_fields
from seizou.core.exceptions import CheckViolation, RuleError

#: 継承した画面が上書きしてよい属性 (R15 の 2 文目。決まりの検査が見る)
OVERRIDABLE = (
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


class ScreenMixin(LoginRequiredMixin):
    """画面の名前と 1 文。画面の目録 (manage.py seizou_screens)・説明書・左のメニューが読む。"""

    screen_name: str = ""
    screen_help: str = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["screen_name"] = self.screen_name
        context["screen_help"] = self.screen_help
        return context


class ColumnSpec:
    def __init__(self, model, item):
        if isinstance(item, (tuple, list)):
            self.name, self.label = item[0], item[1]
        else:
            self.name, self.label = item, None
        self.field = None
        if not self.name.startswith(company_fields.EXT_PREFIX) and "__" not in self.name:
            try:
                self.field = model._meta.get_field(self.name)
            except Exception:  # noqa: BLE001 - 項目でない名前 (関数など) は並び替えない
                self.field = None
        if self.label is None:
            self.label = str(self.field.verbose_name) if self.field is not None else self.name
        self.sortable = self.field is not None or self.name.startswith(company_fields.EXT_PREFIX)
        self.desc_name = "-" + self.name

    def order_key(self) -> str:
        if self.name.startswith(company_fields.EXT_PREFIX):
            return f"{company_fields.EXT_ACCESSOR}__{self.name[len(company_fields.EXT_PREFIX):]}"
        return self.name


class BaseListView(ScreenMixin, ListView):
    """一覧の土台。サーバーが行を描く。既定は 1 ページ 50 件 (20 / 50 / 100)。"""

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
            specs.append(ColumnSpec(self.model, (company_fields.EXT_PREFIX + col.name, col.label)))
        return specs

    def sort_param(self) -> str:
        sort = self.request.GET.get("sort", "")
        allowed = {s.name for s in self.column_specs() if s.sortable}
        return sort if sort.lstrip("-") in allowed else ""

    def get_ordering(self):
        sort = self.sort_param()
        if sort:
            spec = next(s for s in self.column_specs() if s.name == sort.lstrip("-"))
            return ("-" if sort.startswith("-") else "") + spec.order_key()
        return self.ordering or "-pk"

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
            q = Q()
            for lookup in lookups:
                q |= Q(**{f"{lookup}__icontains": term})
            qs = qs.filter(q)
        return qs

    # --- 行 ---
    def row_for(self, obj, specs) -> dict:
        cells = []
        for spec in specs:
            value = display_value(obj, spec.name)
            cells.append({"value": value, "master": value if is_master(value) else None})
        return {"pk": obj.pk, "obj": obj, "cells": cells, "url": model_url(self.model, "detail", obj.pk)}

    def totals_for(self, queryset, specs) -> list | None:
        """絞り込みに追従する合計の行 (数の項目だけ。無ければ出さない)。"""
        sums = {}
        for spec in specs:
            if isinstance(spec.field, (models.DecimalField, models.IntegerField, models.FloatField)):
                sums[spec.name] = models.Sum(spec.name)
        if not sums:
            return None
        agg = queryset.aggregate(**sums)
        return [agg.get(spec.name) for spec in specs]

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
        context["search_hint"] = "、".join(
            str(getattr(self.model._meta.get_field(f.split("__")[0]), "verbose_name", f))
            for f in self.search_fields
        )
        context["sort"] = self.sort_param()
        context["per"] = self.get_paginate_by(None)
        context["page_sizes"] = self.page_sizes
        context["query_without_page"] = self.query_without("page", "format")
        context["query_without_sort"] = self.query_without("sort", "page", "format")
        context["create_url"] = model_url(self.model, "create")
        return context

    def rows_template_names(self) -> list[str]:
        return [f"{self.model._meta.app_label}/_rows.html", "core/_rows.html"]

    def render_to_response(self, context, **response_kwargs):
        if self.request.GET.get("format") == "csv":
            return self.csv_response(context)
        if self.request.headers.get("HX-Request"):
            template = select_template(self.rows_template_names())
            return HttpResponse(template.render(context, self.request))
        return super().render_to_response(context, **response_kwargs)

    def csv_response(self, context) -> HttpResponse:
        specs = context["columns"]
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{self.model._meta.model_name}.csv"'
        response.write("﻿")
        writer = csv.writer(response)
        writer.writerow([s.label for s in specs])
        for obj in self.get_queryset():
            writer.writerow(["" if v is None else str(v) for v in (display_value(obj, s.name) for s in specs)])
        return response


class BaseDetailView(ScreenMixin, DetailView):
    """詳細の土台。1 件と、関係する物の一覧 (tabs = ((見出し, related_name), ...))。"""

    tabs: tuple = ()
    template_name = "core/detail.html"

    def field_items(self, obj) -> list[dict]:
        items = []
        for f in obj._meta.concrete_fields:
            if f.primary_key or f.name in ("password",):
                continue
            value = display_value(obj, f.name)
            items.append({"label": str(f.verbose_name), "value": value, "master": value if is_master(value) else None})
        for col in company_fields.columns_for(obj.__class__):
            items.append({"label": col.label, "value": company_fields.ext_value(obj, col.name), "master": None})
        return items

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = context["object"]
        context["fields"] = self.field_items(obj)
        context["tabs"] = [
            {"label": label, "rows": list(getattr(obj, related).all()[:100])}
            for label, related in self.tabs
            if hasattr(obj, related)
        ]
        context["edit_url"] = model_url(obj.__class__, "update", obj.pk)
        return context


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
            self.object = get_object_or_404(self.model, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.object is not None and "instance" in self.form_class.__init__.__code__.co_varnames:
            kwargs["instance"] = self.object
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
            css = "checkbox" if isinstance(field.widget, forms_checkbox_types()) else "input input-bordered"
            if isinstance(field.widget, forms_select_types()):
                css = "select select-bordered"
            field.widget.attrs.setdefault("class", css)
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


def forms_checkbox_types():
    from django.forms import widgets

    return (widgets.CheckboxInput,)


def forms_select_types():
    from django.forms import widgets

    return (widgets.Select, widgets.SelectMultiple)


class BaseModalView(ScreenMixin, DetailView):
    """小窓の土台。見るだけと、詳細への移動のボタン。template は <app>/_modal.html か core/_modal.html。"""

    modal_fields: int = 8

    def get_template_names(self):
        if self.template_name:
            return [self.template_name]
        return [f"{self.model._meta.app_label}/_modal.html", "core/_modal.html"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = context["object"]
        items = []
        for f in obj._meta.concrete_fields:
            if f.primary_key:
                continue
            items.append({"label": str(f.verbose_name), "value": display_value(obj, f.name)})
        context["fields"] = items[: self.modal_fields]
        context["detail_url"] = model_url(obj.__class__, "detail", obj.pk)
        return context


class BaseSelectView(ScreenMixin, View):
    """候補の口。Tom Select の候補を 1 ページ返す (05 の 2 節)。"""

    model = None
    search_fields: tuple = ()
    per_page = 30

    def get_queryset(self):
        return self.model._default_manager.all()

    def get(self, request, *args, **kwargs):
        term = request.GET.get("q", "").strip()
        try:
            page = max(int(request.GET.get("page", "1")), 1)
        except ValueError:
            page = 1
        qs = self.get_queryset()
        if term and self.search_fields:
            q = Q()
            for lookup in self.search_fields:
                q |= Q(**{f"{lookup}__icontains": term})
            qs = qs.filter(q)
        start = (page - 1) * self.per_page
        rows = list(qs[start : start + self.per_page + 1])
        more = len(rows) > self.per_page
        items = [{"id": obj.pk, "text": str(obj)} for obj in rows[: self.per_page]]
        return JsonResponse({"items": items, "more": more})


class HomeView(ScreenMixin, TemplateView):
    """最初の画面。左のメニューと同じ入口を並べる (ログインの後の移動先)。"""

    screen_name = "はじめに"
    screen_help = "左のメニューから画面を選びます。"
    template_name = "core/home.html"


def decimal_or_none(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None
