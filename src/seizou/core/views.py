"""画面の土台 (01 の 2 節と 10.3)。

部品の画面はここの Base*View を継承する (R15)。継承した画面が上書きしてよいのは
model・ordering・columns・search_fields・filters・presets・tabs・form_class・service・template_name・
get_queryset・screen_name・screen_help の 13 だけ (OVERRIDABLE)。

約束:
- 一覧: 検索 (q)・並び替え (sort)・ページ送り (page・per) ・会社ごとの項目の列・同じ条件の CSV (format=csv)。
  htmx の要求には行の部分 (<app>/_rows.html か core/_rows.html) だけを返す。ただしブラウザの戻る
  (htmx の HX-History-Restore-Request) には画面の全体を返す。部分と全体は同じ URL なので Vary: HX-Request を付ける。
  合計の行は、列が合計を選んだときだけ出す (BaseListView の docstring)。
  画面の全体を描くときも、行の部分は同じ順 (<app>/_rows.html → core/_rows.html) で選ぶ (core/list.html の include)。
- 詳細: 1 件の項目と、関係する物の一覧 (tabs = ((見出し, related_name), ...))。行の __str__ が FK を読むタブは、
  3 つ目に FK の名前の並びを書くと行と相手を 1 回で引く ((見出し, related_name, ("FK の名前", ...)))。
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
- rule_help: 赤の知らせの決まりの番号の「?」が開く説明 (RULES.md の行)。htmx の要求には小窓の中身、htmx でない要求
  (小窓の入力の赤い帯の「?」が開く新しいタブ) には画面の全体を返す。同じ URL なので Vary: HX-Request を付ける。

URL の名前の約束 (画面どうしの移動に使う。無ければ link を出さない):
  <app>:<model>_list / _detail / _create / _update / _modal
"""

from __future__ import annotations

import csv
import inspect
import json
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from importlib import import_module
from pathlib import Path
from urllib.parse import quote

from django import forms as django_forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.forms.utils import pretty_name
from django.db import DatabaseError, models
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.template.loader import select_template
from django.urls import NoReverseMatch, Resolver404, get_script_prefix, resolve, reverse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers, patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET
from django.views.generic import DetailView, FormView, ListView, TemplateView, View

from seizou.core import access
from seizou.core import checks
from seizou.core import csvio
from seizou.core import dashboard
from seizou.core import select_options
from seizou.core import fields as company_fields
from seizou.core import summary as list_summary
from seizou.core.exceptions import CheckViolation, RuleError
from seizou.core.templatetags.seizou_tags import rule_code
from seizou.core.widgets import ClearableFileWidget, DecimalInput, IntegerInput, RemoteSelect

#: 継承した画面が上書きしてよい属性 (R15 の 2 文目。決まりの検査が見る)
OVERRIDABLE = (
    "model",
    "ordering",
    "columns",
    "search_fields",
    "filters",
    "presets",
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


#: 入力の土台が欄の検査で止まった時の赤の知らせ (BaseFormView.form_invalid) の 1 文目。
#: 決め 11-06 C57-3「保存されなかったら理由が何でも出す。欄の誤りなら 1 (欄の下の赤い字) と 2 (止まった知らせ) の両方」。
#: service の関数の名前で選ぶ (依頼書 N156 の表のうち入力の土台に当たる行)。字は core.manual.REFUSAL_HEADS の組の中だけ。
#: 名前が cancel_ で始まる物は「取り消せません。」、BaseFormView.DELETE_SERVICE_PREFIXES で始まる物は「削除できません。」、
#: 下の表の名前はその字、ほかの全部 (service の無い画面も) は「保存できません。」。
REFUSAL_HEAD_SAVE = "保存できません。"
REFUSAL_HEAD_CANCEL = "取り消せません。"
REFUSAL_HEAD_DELETE = "削除できません。"
REFUSAL_HEAD_BY_SERVICE = {
    "create_invoice": "発行できません。",
    "create_delivery_note": "発行できません。",
    "issue_workorder": "発行できません。",
    "issue_account": "発行できません。",
    "record_payment": "記録できません。",
    "ship_lines": "出荷できません。",
    "receive": "受け入れられません。",
    "accept": "受け入れられません。",
    "accept_extracted": "受け入れられません。",
    "reject": "断れません。",
    "accept_with_conditions": "受け入れられません。",
    "hold": "保留できません。",
    "post_chat_message": "送れません。",
}
#: 欄の誤りの知らせに名前を並べる欄の数 (これより多い分は「ほか n つ」)
INVALID_LABELS_SHOWN = 3


def refusal_head(service_name: str, delete_prefixes: tuple[str, ...]) -> str:
    """service の関数の名前から、止まった知らせの 1 文目 (「<操作>できません。」) を選ぶ。空の名前は「保存できません。」。"""
    if service_name.startswith("cancel_"):
        return REFUSAL_HEAD_CANCEL
    if service_name.startswith(delete_prefixes):
        return REFUSAL_HEAD_DELETE
    return REFUSAL_HEAD_BY_SERVICE.get(service_name, REFUSAL_HEAD_SAVE)


def invalid_field_labels(form) -> str:
    """誤りのある見える欄の見出しを、form の欄の順に「」でくくって並べた字 (3 つまで。4 つ目からは「ほか n つ」)。無ければ空の字。

    隠しの欄と、見出しの空の欄は数えない (明細の行の誤りは行の中に出るので、ここには入らない)。
    """
    labels = [
        str(field.label)
        for name, field in form.fields.items()
        if name in form.errors and field.label and not form[name].is_hidden
    ]
    text = "".join(f"「{label}」" for label in labels[:INVALID_LABELS_SHOWN])
    if len(labels) > INVALID_LABELS_SHOWN:
        text += f"ほか {len(labels) - INVALID_LABELS_SHOWN} つ"
    return text


def invalid_form_message(form, head: str) -> str:
    """欄の検査で止まった時の赤の知らせの文。1 文目は head (refusal_head が選ぶ字)。

    欄の外の誤り (form.non_field_errors) が無い時は、誤りのある欄の名前と直し方。名前が 1 つも取れない時 (明細の行か隠しの欄だけ) は
    「入れた値に誤りがあります。…」。欄の外の誤りがある時は、その字を " " でつないで続け、名前の取れる欄の誤りもあれば
    「「品名」の欄も直してください。」を足す (欄の外の誤りの帯はこの知らせに移した。決め 11-06 C57-3)。
    """
    labels = invalid_field_labels(form)
    errors = " ".join(str(error) for error in form.non_field_errors())
    if errors:
        return f"{head}{errors} {labels}の欄も直してください。" if labels else f"{head}{errors}"
    if labels:
        return f"{head}{labels}の欄に誤りがあります。欄の下の赤い字のとおりに直してから、もう一度押してください。"
    return f"{head}入れた値に誤りがあります。赤い字のとおりに直してから、もう一度押してください。"


#: 画面の一部だけを差し替える応答で、止まった知らせ (赤) と確かめ (黄) を右上に出す合図の名前 (core/static/js/seizou-modal.js が受ける。決め 11-07 C57-5)
NOTICE_EVENT = "seizou:notice"


def add_notice_trigger(response, notice=None, warnings=()):
    """応答に、止まった知らせと確かめを右上の知らせに出す合図 (見出し HX-Trigger の seizou:notice) を付けて返す (決め 11-07 C57-5)。

    画面の一部だけを差し替える応答では、右上の知らせ (core/_toasts.html) が差し替えに入らない。合図を受けた seizou-modal.js が、
    base.html の template#seizou-toast-template の赤 (stop) と黄 (check) の箱を写す (見た目と印は _toasts.html と同じ。段は CLAUDE.md の「知らせの強さ」)。
    中身は {"items": [...]}。1 つは level (stop / check)・message (文の全部。1 文目の太字は JS が最初の「。」で分ける)・
    rule (決まりの番号。R と 2 桁でなければ空)・help (赤で番号がある時だけ決まりの説明の道。ほかは空)。並びは赤が先、黄は warnings の順。
    notice も warnings も無ければ何も付けない。鍵に target を使わない (htmx は detail.target を合図を出す要素と読むため)。
    JSON は既定の ensure_ascii のまま (見出しは latin-1 の字だけ。saved_in_modal と同じ訳)。
    """
    items = []
    if notice is not None:
        rule = rule_code(notice.code)
        help_url = (url_or_none("core:rule_help", rule) or "") if rule else ""
        items.append({"level": "stop", "message": str(notice.message), "rule": rule, "help": help_url})
    for warning in warnings or ():
        items.append({"level": "check", "message": str(warning.message), "rule": rule_code(warning.code), "help": ""})
    if items:
        response["HX-Trigger"] = json.dumps({NOTICE_EVENT: {"items": items}})
    return response


def url_or_none(name: str, *args) -> str | None:
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return None


def model_url(model, kind: str, *args) -> str | None:
    meta = model._meta
    return url_or_none(f"{meta.app_label}:{meta.model_name}_{kind}", *args)


def screen_name_of(url: str | None) -> str:
    """URL の画面の名前 (画面の class の screen_name)。詳細のパンくずの 1 段目に一覧の名前を出すため (N163)。

    URL が無いか、引けないか、画面の class が名前を持たなければ空。空なら詳細は「一覧に戻る」を出す。
    reverse は script の頭 (置き場の道) を付けた道を返し、resolve はそれを受けないので、頭を外してから引く。
    """
    if not url:
        return ""
    prefix = get_script_prefix()
    if prefix != "/" and url.startswith(prefix):
        url = "/" + url[len(prefix):]
    try:
        match = resolve(url)
    except Resolver404:
        return ""
    return getattr(getattr(match.func, "view_class", None), "screen_name", "") or ""


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

    表示用の絵や変換の誤りの文のように、仕組みのために持つ列 (入力の画面に無い物) と、
    取引先の支払の月のように、別の項目の字に含まれて二重になる列を、
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
      {"name": "board__days_left", "label": "納期まで", "kind": "number", "sort_key": "due_date"}   別の鍵で並べる

    辞書の鍵:
      kind      number (右寄せ・負は赤) / money (右寄せ。金額の閲覧が無い人には列ごと出さない。CSV からも外す) / date (等幅の Y-m-d)。
                書かなければ表の項目の型で決める (会社ごとの項目は型の number / date)。ほかの字は ValueError
      sortable  真なら見出しが並び替えの link になる。書かなければ表の項目と会社ごとの項目だけ並び替える
      total     真なら合計の行に足す
      sort_key  並び替えの鍵。書くと見出しが並び替えの link になる。Python の値の列 (board__<名前>) を、同じ順になる
                表の項目 (__ で辿る名前も可) か annotate の列で並べるときに書く
    表の項目でない名前 (annotate の列) に sortable か total を書くと、土台は order_by と Sum にその名前を渡す
    (queryset の annotate にある約束。無ければ何もしない)。board__<名前> のような Python の値 (model の property) には
    kind だけを書く (sortable・total を書かない。並べたいときは sort_key)。

    印:
      numeric      右寄せと等幅の数字 (表の数の項目・kind が number / money・会社ごとの項目の数)。負は赤
      money        金額の列 (kind が money か、名前の最後が access.MONEY_FIELDS)。金額の閲覧が無い人には BaseListView.column_specs が列ごと外す
      total        合計の行に足す (表の数の項目と、kind が number / money の annotate の列)
      is_date      等幅の Y-m-d (表の日付の項目・kind が date・会社ごとの項目の日付)
      is_datetime  等幅の Y-m-d H:i (表の日時の項目)
      is_code      等幅の識別子 (数でない、名前が code か _no / _code で終わる列)
      sortable     見出しが並び替えの link
      annotated    並び替えか合計の先が annotate の列 (sort_key があれば sort_key の先で決める。order_by と Sum に渡す)
    """

    #: 辞書の kind に書ける値
    KINDS = ("number", "money", "date")
    #: 辞書に書ける鍵 (起動の検査 seizou.core.system_checks が、ほかの鍵を誤りにする)
    KEYS = ("name", "label", "kind", "sortable", "total", "sort_key")

    def __init__(self, model, item, ext_type: str | None = None):
        self.total = False
        self.sort_key = None
        kind = None
        sortable = None
        if isinstance(item, dict):
            self.name, self.label = item["name"], item.get("label")
            self.sort_key = item.get("sort_key") or None
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
        if self.sort_key is not None:
            self.sortable = True
        else:
            self.sortable = sortable if sortable is not None else (self.field is not None or is_ext)
        self.desc_name = "-" + self.name
        self.numeric = self.kind in ("number", "money") or isinstance(self.field, NUMERIC_FIELDS) or ext_type == "number"
        self.money = self.kind == "money" or access.is_money_name(self.name)
        self.is_datetime = isinstance(self.field, models.DateTimeField)
        self.is_date = (
            self.kind == "date" or (isinstance(self.field, DATE_FIELDS) and not self.is_datetime) or ext_type == "date"
        )
        self.is_code = not self.numeric and (self.name in CODE_NAMES or self.name.endswith(CODE_SUFFIXES))
        if self.sort_key is not None:
            # 表の項目か __ で辿る名前は get_ordering の道、表に無い名前は annotate の列 (部品の get_queryset の後に並べる)
            self.annotated = "__" not in self.sort_key and not self._has_field(model, self.sort_key)
        else:
            self.annotated = (self.sortable or self.total) and self.field is None and not is_ext

    @staticmethod
    def _has_field(model, name: str) -> bool:
        try:
            model._meta.get_field(name)
        except FieldDoesNotExist:
            return False
        return True

    def order_key(self) -> str:
        if self.sort_key:
            return self.sort_key
        if self.name.startswith(company_fields.EXT_PREFIX):
            return f"{company_fields.EXT_ACCESSOR}__{self.name[len(company_fields.EXT_PREFIX):]}"
        return self.name


def field_at(model, path: str):
    """__ で辿る項目の名前 (例 workorder_step__process) の最後の項目。無ければ FieldDoesNotExist。"""
    current, field = model, None
    for part in path.split("__"):
        if current is None:
            raise FieldDoesNotExist(f"{model.__name__} に {path} がありません。")
        field = current._meta.get_field(part)
        current = field.related_model
    return field


def _takes_values(func) -> bool:
    """flag の when が (queryset, values) の 2 つを受けるか (1 つの関数には queryset だけを渡す)。"""
    try:
        inspect.signature(func).bind(None, None)
    except (TypeError, ValueError):
        return False
    return True


class FilterSpec:
    """一覧の絞り 1 つ (BaseListView.filters の 1 要素。決め 11-04 15:0x)。

    絞りの書き方 (辞書。鍵は KEYS):
      {"name": "status", "label": "状態", "kind": "choice", "field": "status"}               項目の choices から選ぶ
      {"name": "status", "label": "状態", "kind": "choice", "field": "status", "choices": open_statuses}
                                                    choices は (値, 字) の組の並びか、それを返す関数
      {"name": "ordered", "label": "受注日", "kind": "date_range", "field": "order_date"}    開始と終了の 2 欄
      {"name": "qty", "label": "数", "kind": "number_range", "field": "qty"}                 最小と最大の 2 欄
      {"name": "customer", "label": "得意先", "kind": "lookup", "field": "customer"}         FK の先の全部から選ぶ
      {"name": "customer", "label": "得意先", "kind": "lookup", "field": "customer", "choices": customers}
                                                    choices があれば FK の先の全部の代わりに候補にする ((pk, 字) の組の並びか関数)
      {"name": "open", "label": "出荷の残りがある物だけ", "kind": "flag", "when": open_only, "default": True}
                                                    チェック 1 つ。入のときだけ when (queryset を受けて queryset を返す関数) を掛ける
      {"name": "open", "label": "出荷の残りがある受注だけ", "kind": "flag", "when": open_only, "default": True,
       "disabled_when": lambda values: values.get("status") is not None}
                                                    disabled_when (values を受けて真か偽を返す関数) が真の間は、印を切に描いて押せなくし、when を掛けない
      {"name": "thickness", "field": "ext__ext02"}  会社ごとの項目 (core/fields.py)。種類と見出しは項目の登録から決める

    GET の名前は f_<name> (期間と数の範囲は f_<name>_from と f_<name>_to)。q・sort・page・per・format と重ならない。
    絞り方: choice と lookup は field と同じ値、date_range と number_range は両端を含む (gte と lte)。
    日時の項目の期間は日付で比べる (field__date__gte。終わりの日の夜の記録も入る)。
    空の欄と読めない値 (日付でない字・数でない字・候補に無い値) は、絞らない (空と同じ。知らせは出さない)。
    lookup の候補は FK の先の model の全部 (並びは model の ordering、無ければ name、無ければ USERNAME_FIELD、無ければ pk)。
    flag: f_<name>=1 が入、f_<name>=0 が切、鍵が無いか読めない値は default (省略は切)。field と choices は書けない。
      欄は hidden の 0 の後にチェックの 1 を置くので、入のときは両方が届き、後の 1 を読む。when の中は .filter()・.exclude()・
      annotate だけにする (services を呼ばない。SQL を書かない)。「クリア」と「絞りを外す」は f_ を外すので default に戻る。
      default と違う値のときを active とする。札 (BaseListView.filter_chips) は active のうち狭める物 (flag は入) だけで、「絞りを外す」は札があるときだけ出す (N161)。
      values: 同じ一覧の絞りの名前ごとの読めた値の辞書 (FilterSpec.values_by_name。読めた値の無い絞りは鍵が無い)。
      when が 2 つの引数を受ける関数なら when(queryset, values) で呼ぶ (1 つの関数は今までどおり when(queryset)。inspect.signature で見る)。
      disabled_when が真の間は、隠しの欄が印の値を持ち越す (入なら 1)。従う絞りを外すと、印は元の値に戻る。
      htmx の応答では、disabled_when のある印だけを core/_filter_clear.html が oob で描き直す (チェックと表を合わせる)。
    会社ごとの項目: field に ext__<列の名前> を書く。型が choice なら choice (候補は項目の choices)、date なら date_range、
      number なら number_range。text と bool は絞れない (ValueError)。kind は省ける (書くなら同じ種類)。
      BaseListView は一覧に出す会社ごとの項目のうち絞れる物を、宣言の後ろに自動で足す (GET の名前は f_<列の名前>)。
    """

    #: kind に書ける値
    KINDS = ("choice", "date_range", "number_range", "lookup", "flag")
    #: 辞書に書ける鍵
    KEYS = ("name", "label", "kind", "field", "choices", "when", "default", "disabled_when")
    #: flag に書ける鍵 (when と default と disabled_when は flag だけ)
    FLAG_KEYS = ("name", "label", "kind", "when", "default", "disabled_when")
    #: flag だけに書ける鍵
    FLAG_ONLY_KEYS = ("when", "default", "disabled_when")
    #: 会社ごとの項目の型 → 絞りの種類 (text と bool は絞れない)
    EXT_KINDS = {"choice": "choice", "date": "date_range", "number": "number_range"}
    #: GET の名前の頭
    PREFIX = "f_"
    RANGE_KINDS = ("date_range", "number_range")

    def __init__(self, model, item: dict):
        unknown = [key for key in item if key not in self.KEYS]
        if unknown:
            raise ValueError(f"絞りに書けない鍵があります: {unknown!r}。{' / '.join(self.KEYS)} のどれかにしてください。")
        self.name = item["name"]
        self.kind = item.get("kind")
        self.field = None
        self.column = None
        self.when = None
        self.when_takes_values = False
        self.disabled_when = None
        self.default = False
        if self.kind == "flag":
            self.field_name = None
            self._read_flag(item)
        else:
            misplaced = [key for key in self.FLAG_ONLY_KEYS if key in item]
            if misplaced:
                raise ValueError(f"{misplaced!r} は flag の絞りだけに書けます ({self.name})。")
            self.field_name = item.get("field") or self.name
            if self.field_name.startswith(company_fields.EXT_PREFIX):
                self._read_ext(model)
            elif self.kind not in self.KINDS:
                raise ValueError(f"知らない絞りの種類: {self.kind!r} ({self.name})。{' / '.join(self.KINDS)} のどれかにしてください。")
            else:
                self.field = field_at(model, self.field_name)
        if self.column is not None:
            label = self.column.label
        else:
            label = str(self.field.verbose_name) if self.field is not None else None
        self.label = item.get("label") or label
        self.choices = item.get("choices")
        self.param = self.PREFIX + self.name
        self.param_from = self.param + "_from"
        self.param_to = self.param + "_to"
        self.params = (self.param_from, self.param_to) if self.kind in self.RANGE_KINDS else (self.param,)

    def _read_flag(self, item: dict) -> None:
        """flag の宣言を読む (field と choices は書けない。when は関数。default は True か False)。"""
        extra = [key for key in item if key not in self.FLAG_KEYS]
        if extra:
            raise ValueError(
                f"flag の絞り ({self.name}) に {extra!r} は書けません。条件は when (queryset を受けて queryset を返す関数) に書いてください。"
            )
        if not callable(item.get("when")):
            raise ValueError(f"flag の絞り ({self.name}) に when (queryset を受けて queryset を返す関数) がありません。")
        if not item.get("label"):
            raise ValueError(f"flag の絞り ({self.name}) に label (チェックの横の字) がありません。")
        default = item.get("default", False)
        if not isinstance(default, bool):
            raise ValueError(f"flag の絞り ({self.name}) の default は True か False で書きます ({default!r})。")
        disabled_when = item.get("disabled_when")
        if disabled_when is not None and not callable(disabled_when):
            raise ValueError(
                f"flag の絞り ({self.name}) の disabled_when は関数 (絞りの名前ごとの値の辞書を受けて真か偽を返す) で書きます。"
            )
        self.when = item["when"]
        self.when_takes_values = _takes_values(item["when"])
        self.disabled_when = disabled_when
        self.default = default

    def _read_ext(self, model) -> None:
        """会社ごとの項目 (field = ext__<列の名前>) の宣言を読む。種類は項目の型から決める。"""
        name = self.field_name[len(company_fields.EXT_PREFIX):]
        column = next((c for c in company_fields.columns_for(model) if c.name == name), None)
        if column is None:
            raise ValueError(f"会社ごとの項目 {name} は {model._meta.label} に登録されていません ({self.name})。")
        kind = self.EXT_KINDS.get(column.type)
        if kind is None:
            raise ValueError(
                f"会社ごとの項目 {name} ({column.label}) の型 {column.type} は絞れません ({self.name})。"
                f"絞れるのは {' / '.join(self.EXT_KINDS)} の型です。"
            )
        if self.kind is not None and self.kind != kind:
            raise ValueError(f"会社ごとの項目 {name} ({column.label}) の絞りの種類は {kind} です ({self.name} の kind は {self.kind!r})。")
        self.kind = kind
        self.column = column

    # --- 値を読む (読めない値は None = 絞らない) ---
    def choice_values(self) -> list[tuple[str, str]]:
        choices = self.choices
        if choices is None:
            choices = [(c, c) for c in self.column.choices] if self.column is not None else self.field.flatchoices
        if callable(choices):
            choices = choices()
        return [(str(value), str(text)) for value, text in choices]

    def lookup_values(self) -> list[tuple[str, str]]:
        if self.choices is not None:
            return self.choice_values()
        related = self.field.related_model
        rows = related._default_manager.all()
        if not related._meta.ordering:
            names = {f.name for f in related._meta.get_fields()}
            key = "name" if "name" in names else getattr(related, "USERNAME_FIELD", "pk")
            rows = rows.order_by(key if key in names else "pk")
        return [(str(row.pk), str(row)) for row in rows]

    def read(self, raw: str):
        raw = (raw or "").strip()
        if not raw:
            return None
        if self.kind == "date_range":
            try:
                return date.fromisoformat(raw)
            except ValueError:
                return None
        if self.kind == "number_range":
            try:
                number = Decimal(raw.replace(",", ""))
            except InvalidOperation:
                return None
            return number if number.is_finite() else None
        if self.kind == "lookup":
            related = self.field.related_model
            try:
                pk = related._meta.pk.to_python(raw)
            except ValidationError:
                return None
            if self.choices is not None:
                return pk if str(pk) in {value for value, _ in self.choice_values()} else None
            return pk if related._default_manager.filter(pk=pk).exists() else None
        if self.kind == "flag":
            return {"1": True, "0": False}.get(raw)
        return raw if raw in {value for value, _ in self.choice_values()} else None

    def values(self, params) -> dict:
        """GET の名前ごとの読めた値 (読めない値は入れない)。"""
        result = {}
        for name in self.params:
            value = self.read(params.get(name, ""))
            if value is not None:
                result[name] = value
        return result

    def is_on(self, params) -> bool:
        """flag が入か (鍵が無いか読めない値は default)。disabled_when は見ない (is_disabled)。"""
        return self.values(params).get(self.param, self.default)

    def value(self, params):
        """この絞りの読めた値 1 つ (無ければ None)。範囲は (始め, 終わり) の組 (読めない端は None)、flag は入か (bool)。"""
        if self.kind == "flag":
            return self.is_on(params)
        values = self.values(params)
        if not values:
            return None
        if self.kind in self.RANGE_KINDS:
            return (values.get(self.param_from), values.get(self.param_to))
        return values[self.param]

    @staticmethod
    def values_by_name(specs, params) -> dict:
        """一覧の絞りの名前ごとの読めた値 (flag の when と disabled_when に渡す辞書。読めた値の無い絞りは鍵を入れない)。

        値を受ける flag (2 つの引数の when か disabled_when) が無い一覧は、読まずに空の辞書を返す (lookup の問い合わせを足さない)。
        """
        if not any(spec.when_takes_values or spec.disabled_when is not None for spec in specs):
            return {}
        result = {}
        for spec in specs:
            value = spec.value(params)
            if value is not None:
                result[spec.name] = value
        return result

    def is_disabled(self, values) -> bool:
        """flag を切に描き、when を掛けないか (disabled_when があり、values で真)。"""
        return self.disabled_when is not None and bool(self.disabled_when(values))

    @staticmethod
    def as_is(queryset):
        """印だけを宣言する flag の when (何もしない)。条件は画面の get_queryset が、ほかの条件と合わせて掛ける。"""
        return queryset

    def apply(self, queryset, params, named=None):
        """queryset に絞りを掛ける。flag は入で disabled_when が真でないときだけ when を、ほかは q() を掛ける。

        named は一覧の絞りの名前ごとの読めた値 (values_by_name)。省くと空の辞書 (ほかの絞りに値が無い) として読む。
        """
        if self.kind == "flag":
            named = {} if named is None else named
            if not self.is_on(params) or self.is_disabled(named):
                return queryset
            return self.when(queryset, named) if self.when_takes_values else self.when(queryset)
        q = self.q(params)
        return queryset if q is None else queryset.filter(q)

    def q(self, params) -> Q | None:
        """flag でない絞りの条件 (flag は apply が when を掛けるので None)。"""
        values = self.values(params)
        if not values or self.kind == "flag":
            return None
        if self.kind in self.RANGE_KINDS:
            key = self.field_name + ("__date" if isinstance(self.field, models.DateTimeField) else "")
            q = Q()
            if self.param_from in values:
                q &= Q(**{f"{key}__gte": values[self.param_from]})
            if self.param_to in values:
                q &= Q(**{f"{key}__lte": values[self.param_to]})
            return q
        return Q(**{self.field_name: values[self.param]})

    def context(self, params, named=None) -> dict:
        """テンプレート (core/_filters.html) が描く材料。欄の値は読めた値だけ (読めない字は空の欄にする)。

        flag は、named (values_by_name) で disabled_when が真なら、切 (checked が偽)・押せない (disabled)・絞らない (active が偽) に描き、
        隠しの欄の値 (kept) に印の値を持ち越す。follows は disabled_when があるか (htmx の応答で描き直す印)。
        choice と lookup は width_ch (選ぶ欄の幅。select_width) も入れる。
        """
        values = self.values(params)
        shown ={name: str(value) for name, value in values.items()}
        item = {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "param": self.param,
            "param_from": self.param_from,
            "param_to": self.param_to,
            "value": shown.get(self.param, ""),
            "value_from": shown.get(self.param_from, ""),
            "value_to": shown.get(self.param_to, ""),
            "active": bool(values),
            "checked": False,
            "options": [],
        }
        if self.kind == "choice":
            item["options"] = self.choice_values()
            item["width_ch"] = self.select_width(item["options"])
        elif self.kind == "lookup":
            item["options"] = self.lookup_values()
            item["width_ch"] = self.select_width(item["options"])
        elif self.kind == "flag":
            on = self.is_on(params)
            item.update(kept="0", disabled=False, follows=self.disabled_when is not None)
            if self.is_disabled({} if named is None else named):
                item.update(kept="1" if on else "0", disabled=True, active=False)
            else:
                item.update(value="1" if on else "", checked=on, active=on != self.default)
        return item

    #: 選ぶ欄の頭の選択肢の字 (core/_filters.html の <option value="">。幅を数えるときに候補に足す)
    ALL_TEXT = "すべて"

    @classmethod
    def select_width(cls, options) -> int:
        """選ぶ欄の幅 (ch の数)。候補と「すべて」のうち、いちばん幅の広い字で決める (初見テストの V152。候補 C31 の 3)。

        選んだ値は必ず候補の 1 つなので、欄の幅は選ぶ前と後で変わらず、選んだ名前も切れない。
        字の幅は全角 (east_asian_width が W か F) を 2、ほかを 1 と数える。core/_filters.html が
        width: calc(<数>ch + 2.75rem) と max-width: 100% にする (2.75rem は欄の内側の余白 0.75rem と 1.75rem に、枠の分を足した物)。
        全角の字を 2ch とする見積もりと 2.75rem は推測で、画面で測っていない。少なく見積もると、選んだ名前の末尾が「…」になる。
        """
        texts = [cls.ALL_TEXT, *(text for _, text in options)]
        return max(sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text) for text in texts)

    @staticmethod
    def chip_text(item: dict) -> str:
        """札 (core/_filter_clear.html) の値の字。item は context() の 1 つ (flag でない物)。

        choice と lookup は候補の字。期間と数の範囲は「始め〜終わり」(片方だけなら「始め〜」「〜終わり」)。
        期間の両端が同じ年なら終わりの年を省く (2026-11-01〜11-05。本人の決め 11-05 16:2x の例の形)。
        """
        if item["kind"] in ("choice", "lookup"):
            return dict(item["options"]).get(item["value"], item["value"])
        start, end = item["value_from"], item["value_to"]
        if item["kind"] == "date_range" and start and end and start[:4] == end[:4]:
            end = end[5:]
        return f"{start}〜{end}"


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
    #: この枠を名指しした htmx の要求には画面の全体を返し、htmx が枠だけを選んで入れ替える (欄・タブ・札・表がそろう。N161)
    LIST_TARGET = "list-card"

    # --- 列 ---
    def column_specs(self) -> list[ColumnSpec]:
        specs = [ColumnSpec(self.model, item) for item in self.columns]
        for col in company_fields.list_columns_for(self.model):
            specs.append(ColumnSpec(self.model, (company_fields.EXT_PREFIX + col.name, col.label), ext_type=col.type))
        # 金額の列は、金額の閲覧が無い人には列ごと出さない (見出し・欄・合計の行・並び替え・集計の量・CSV から外れ、「—」も出さない。
        # 本人の決め 11-05 09:4x。N155)。log の形では見せて記録に 1 行 (同じ要求は 1 行)。金額の列の無い一覧は判定しない。
        if any(spec.money for spec in specs) and not self._money_columns_shown():
            specs = [spec for spec in specs if not spec.money]
        return specs

    def _money_columns_shown(self) -> bool:
        """金額の列を見せるか。判定 (記録つき) は 1 要求で 1 回だけ呼び、答えを画面に控える。

        column_specs は 1 要求で何度も呼ばれる (並び替え・get_queryset・行・合計・CSV) ため。記録の行の数は今までと同じ (N174)。
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
        # (行ごとに相手を読む SELECT が行の数だけ出ないため。N174)。部品の get_queryset が同じ名前を足しても害は無い。
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
        """効いている絞りの札 (core/_filter_clear.html。本人の決め 11-05 16:2x。N161)。

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
                    # 状態の列 (choices を持つ status の項目。status_for と同じ読み方) は core/_rows.html が状態の色の表で印にする (N152)
                    "status": spec.name == "status" and bool(getattr(spec.field, "choices", None)),
                    # 真偽の値は、行のテンプレートが bool_mark の印にする (真 = チェックの丸・偽 = 空。本人の決め 11-06 17:1x。C55)
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
        (末尾の欄の名前では何の名前か分からない所に書く。例 出荷の一覧の「得意先の名前」= 初見テスト R5 の候補 C36 の 7)。
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
        # 効いている絞りの札 (core/_filter_clear.html が絞り込みの枠の下に並べる。N161)
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


class BaseDetailView(ScreenMixin, DetailView):
    """詳細の土台。1 件と、関係する物の一覧 (tabs = ((見出し, related_name), ...)。3 つ目の FK の名前の並びは任意。related_tab)。"""

    tabs: tuple = ()
    opens_by_department = False
    template_name = "core/detail.html"

    def field_items(self, obj) -> list[dict]:
        """項目の一覧。1 項目 = {"label", "value", "master", "file", "money", "boolean"} (file はファイルの項目だけ、file_info の形。
        money は金額の項目なら真で、テンプレートが yen_if で ¥ を付ける。テンプレートの filter の引数に使うので、全部の行が鍵を持つ。
        boolean は値が真偽なら真で、テンプレートが bool_mark で印にする。C55)。
        url は、マスターでない相手に詳細の画面があり、部署でその部品を使える人のときだけ、その詳細の URL (無ければ None。全部の行が鍵を持つ)。
        明細は親の伝票 (model の detail_link_target)。テンプレートが link にする (record_url。候補 C80)。"""
        items = []
        hidden = hidden_detail_fields(obj)
        for f in obj._meta.concrete_fields:
            if f.primary_key or f.name in ("password",) or f.name in hidden:
                continue
            if access.is_money_name(f.name) and not access.can_see_money(self.request.user):
                continue  # 金額の閲覧が無い人には、金額の項目を項目ごと出さない (「—」も出さない。N155)
            value = display_value(obj, f.name)
            is_file = isinstance(f, models.FileField)
            items.append(
                {
                    "label": str(f.verbose_name),
                    "value": "" if is_file else value,
                    "boolean": isinstance(value, bool),
                    "money": access.is_money_name(f.name),
                    "master": value if is_master(value) else None,
                    "file": file_info(value, file_url_for(obj, f.name)) if is_file else None,
                    "url": None if is_file else self.record_url(value),
                }
            )
            # 受注の明細のような相手の表が部品を持つなら、部品の行を足して小窓で開けるようにする (初見テスト 2 回目の E-05)
            linked = getattr(value, "item", None) if value is not None and not is_master(value) else None
            if is_master(linked) and not any(c.name == "item" for c in obj._meta.concrete_fields):
                items.append(
                    {"label": "部品", "value": linked, "boolean": False, "master": linked, "file": None, "money": False, "url": None}
                )
        for col in company_fields.columns_for(obj.__class__):
            value = company_fields.ext_value(obj, col.name)
            items.append(
                {"label": col.label, "value": value, "boolean": isinstance(value, bool), "master": None, "file": None, "money": False, "url": None}
            )
        return items

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = context["object"]
        fields = self.field_items(obj)
        context["fields"] = fields
        context["files"] = [item for item in fields if item["file"]]
        context["tabs"] = [
            self.tab_for(obj, label, related, names) for label, related, names in _tab_specs(self.tabs) if hasattr(obj, related)
        ]
        # 右の列のある詳細は、件数のある最初のタブで開く (無ければ 1 つ目。選ばないと何も出ないため = N150。初見テスト R5 の候補 C26 の 2)
        chosen = next((tab for tab in context["tabs"] if tab["count"] >= 1), context["tabs"][0] if context["tabs"] else None)
        for tab in context["tabs"]:
            tab["selected"] = tab is chosen
        context["edit_url"] = model_url(obj.__class__, "update", obj.pk)
        if context["edit_url"] and not access.can_write_model(self.request.user, obj.__class__, "update"):
            context["edit_url"] = None
        context["list_url"] = model_url(obj.__class__, "list")
        context["list_name"] = screen_name_of(context["list_url"])
        context["status"] = self.status_for(obj)
        return context

    def tab_for(self, obj, label: str, related: str, select_related=()) -> dict:
        return related_tab(obj, label, related, select_related)

    def status_for(self, obj) -> str | None:
        """見出しの横の状態の印。choices を持つ status の項目があればその表示名、無ければ None。"""
        field = next((f for f in obj._meta.concrete_fields if f.name == "status" and f.choices), None)
        return display_value(obj, "status") if field is not None else None

    def record_url(self, value) -> str | None:
        """基本の表の値を押した時に移る詳細の URL (field_items の url)。移れなければ None (テンプレートは今の字のまま出す)。

        マスターでない相手 (_meta を持つ物) で、その詳細の画面が引けるときだけ URL。明細は model の detail_link_target
        (親の受注・発注。明細に詳細の画面が無いため) を読む。core は受注と発注の model を知らない (detail_hidden_fields と同じ約束)。
        マスターは小窓 (master_link)、人と ContentType は詳細の URL が引けないので None。
        URL が引けたときだけ、部署でその部品を開けるかを access.can_open の record=False で見る (関わる記録の link の
        orders/timeline.py の _linked_url と同じ判定。log の時に、開いていない画面を開いたと記録に書かない)。
        URL の無い値では self.request を読まない (request の無い BaseDetailView() でも field_items が通る)。
        初見テスト R5 の V69。候補 C80。
        """
        if value is None or getattr(value, "_meta", None) is None or is_master(value):
            return None
        target = getattr(value, "detail_link_target", value)
        url = model_url(target.__class__, "detail", target.pk)
        if url is None or not access.can_open(self.request.user, target._meta.app_label, record=False):
            return None
        return url


def _tab_specs(tabs) -> list[tuple]:
    """tabs の要素を (見出し, related_name, select_related の名前の並び) にそろえる (詳細と小窓が読む)。

    要素は 2 つ組 (見出し, related_name) か、3 つ組 (見出し, related_name, ("FK の名前", ...))。2 つ組の名前の並びは空。
    """
    return [(tab[0], tab[1], tuple(tab[2]) if len(tab) > 2 else ()) for tab in tabs]


def related_tab(obj, label: str, related: str, select_related=()) -> dict:
    """関係する物の 1 つのタブ (詳細と小窓)。{"label", "related", "count", "rows"}。行は最大 100 件、1 行 = {"obj", "url"}。

    select_related は、行の __str__ が読む FK の名前 (tabs の 3 つ目)。名前があれば行と相手を 1 回の SELECT で引く
    (タブの行の数だけ SELECT が出ないため。N174)。親の get_queryset が prefetch した related には書かない (取り直しになる)。
    """
    manager = getattr(obj, related)
    queryset = manager.all()
    if select_related:
        queryset = queryset.select_related(*select_related)
    rows = [{"obj": row, "url": model_url(row.__class__, "detail", row.pk)} for row in queryset[:100]]
    return {"label": label, "related": related, "count": manager.count(), "rows": rows}


class BaseFormView(ScreenMixin, FormView):
    """入力の土台。form の値が通ったら検査の層を通し、service を呼ぶ。土台が ModelForm の save を呼ぶことは無い。

    小窓の形 (決め 11-04 17:5x〜18:0x。一覧の行の操作はページを移らず小窓で入力する): htmx の要求で見出し HX-Target が
    seizou-modal-box (base.html の小窓の箱) のとき、同じ URL の入力を小窓の中身 (<app>/_form_modal.html か core/_form_modal.html)
    で返す。誤り・赤の知らせ・黄の知らせと「このまま保存」(confirmed=1) は、同じ形のカードが小窓の中に描き直される。
    保存したら移らず、本文の無い 204 に HX-Trigger の seizou:saved (知らせの字と色) を付けて返す (messages に残さない)。
    seizou-modal.js が小窓を閉じ、右上の知らせを出し、一覧の行を今の絞りのまま取り直す。
    それ以外の要求 (直の要求・htmx のほかの要求) は今までどおり画面の全体で、保存したら移る。

    開いた元の画面へ戻す口 (11-05。本人の指摘と初見テスト R4): 詳細などの入口が ?next=<開いた画面の道> を付けて開くと、
    「キャンセル」の先と、保存の後に一覧へ戻る形の移り先 (保存した物の詳細が無い物) をその道にする。保存した物の詳細へ移る形は今のまま。
    道は同じサイトの「/」で始まる物だけ (外・絶対 URL は捨てて今までどおり)。form の hidden の next で POST にも引き継ぐ。
    """

    model = None
    form_class = None
    service = None
    template_name = "core/form.html"
    success_message = "保存しました。"
    #: 削除の画面 (service の名前が DELETE_SERVICE_PREFIXES で始まる) の知らせ
    delete_message = "削除しました。"
    DELETE_SERVICE_PREFIXES = ("delete_", "remove_")
    #: 小窓の形の印 (htmx の要求の見出し HX-Target の値 = base.html の小窓の箱の id)
    MODAL_TARGET = "seizou-modal-box"
    #: 開いた元の画面の道を受ける GET の鍵 (form の hidden で POST にも同じ名前で送る)
    NEXT_PARAM = "next"
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

    # --- 小窓の形 ---
    @property
    def in_modal(self) -> bool:
        """小窓の形か。htmx の要求で、差し替える先 (HX-Target) が小窓の箱のときだけ (GET と POST の両方)。"""
        headers = self.request.headers
        return bool(headers.get("HX-Request")) and headers.get("HX-Target") == self.MODAL_TARGET

    def get_template_names(self):
        """小窓の形は <app>/_form_modal.html → core/_form_modal.html (部品が小窓の形だけ別に持てる口)。ほかは template_name。"""
        if self.in_modal:
            app = self.model._meta.app_label if self.model is not None else self.screen_app()
            return [f"{app}/_form_modal.html", "core/_form_modal.html"]
        return super().get_template_names()

    # --- 開いた元の画面 (?next=) ---
    @property
    def next_url(self) -> str | None:
        """開いた元の画面の道。GET の ?next= か、form の hidden の next。同じサイトの「/」で始まる道だけで、ほかは None。

        外の URL・絶対 URL (http://… は同じ host でも)・「//」で始まる物・「/」で始まらない物は捨てる (開いた元が分からない形 = 今までどおり)。
        """
        url = self.request.GET.get(self.NEXT_PARAM) or self.request.POST.get(self.NEXT_PARAM) or ""
        if not url.startswith("/") or url.startswith("//"):
            return None
        if not url_has_allowed_host_and_scheme(url, allowed_hosts={self.request.get_host()}, require_https=self.request.is_secure()):
            return None
        return url

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        # 同じ URL で小窓の中身と画面の全体を返すので、ブラウザの保存が取り違えないように見出しで分ける (一覧の HX-Request と同じ)。
        patch_vary_headers(response, ("HX-Target",))
        # htmx が画面の一部 (HX-Target の箱) だけを差し替える要求 (今はメモの書く枠 = notes/note_form.html の hx-select="#notes")。
        # 右上の知らせ (_toasts.html) が差し替えに入らないので、合図でも返す (決め 11-07 C57-5)。小窓と画面の全体には付けない。
        headers = self.request.headers
        if headers.get("HX-Request") and headers.get("HX-Target") and not self.in_modal:
            add_notice_trigger(response, context.get("notice"), context.get("warnings") or ())
        return response

    def get_initial(self):
        """form の初期値。class の initial の写しに、新規登録の画面の GET の値を足す。

        受注の明細から出荷の新規登録・指示書の発行へ、明細を選んだ形で移る入口のため (例 ?order_line=<pk>)。
        写すのは GET の鍵の全部 (値は字のまま。同じ鍵が 2 つ以上あれば字の list = 注文残の盤で選んだ明細の ?line=<pk>&line=<pk>。N124)。form の欄に無い鍵も写すので、form の __init__ が
        self.initial.get("from_line") のように読める (注残の盤の「発注する」が明細の pk を渡す入口のため。決め 11-03)。
        写さない物: POST の要求 (送られた値が正)・編集の画面 (対象の値が正)・会社ごとの項目 (ext__)・開いた元の画面の道 (next。入力の値でない)。
        値の検査はしない。保存のときに form と検査の層と service が見る (数でない値や無い pk は、選ぶ欄が何も選ばないだけ)。
        """
        initial = super().get_initial()
        if self.request.method != "GET" or self.object is not None:
            return initial
        for name in self.request.GET:
            if not name.startswith(company_fields.EXT_PREFIX) and name != self.NEXT_PARAM:
                values = self.request.GET.getlist(name)
                initial[name] = values if len(values) > 1 else values[-1]
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
        # form の class の属性 last_fields (欄の名前の並び) の欄を、会社ごとの項目を足した後の最後へ回す (V35)。
        # Django の field_order は form の __init__ の中で並べるので、この後に足す会社ごとの項目より後ろへは回せない。
        # 土台が form の属性を読む先例は submit_label (core/_form_card.html)。使う所は masters の ItemForm・PartnerForm・ProcessForm。
        # 属性の無い form と、form に無い名前 (新規登録で外した使用中の欄など) は今の並びのまま。
        last = [name for name in getattr(form, "last_fields", ()) if name in form.fields]
        if last:
            # order_fields は渡した並びを先頭に置くので、全部の欄の並びを渡す。
            form.order_fields([name for name in form.fields if name not in last] + last)
        for name, field in form.fields.items():
            use_decimal_input(field)
            use_clearable_file_widget(field)
            field.widget.attrs.setdefault("class", widget_css(field.widget))
            use_label_required(name, field)
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
        deleted = self.get_service().__name__.startswith(self.DELETE_SERVICE_PREFIXES)
        text = with_saved_note(self.delete_message if deleted else self.success_message, result)
        if self.in_modal:
            return self.saved_in_modal(text, "warning" if getattr(result, "saved_level", "") == "warning" else "success")
        # service の返す物の saved_level が "warning" なら黄 (確認してください) の知らせ。ほかは緑 (成功しました)。
        if getattr(result, "saved_level", "") == "warning":
            messages.warning(self.request, text)
        else:
            messages.success(self.request, text)
        url = self.get_success_url_for(result)
        if self.request.headers.get("HX-Request"):
            response = HttpResponse(status=200)
            response["HX-Redirect"] = url
            return response
        return HttpResponseRedirect(url)

    def form_invalid(self, form):
        """欄の検査で止まった時も、止まった知らせ (赤) を出す (決め 11-06 C57-3。欄の誤りなら欄の下の赤い字と知らせの両方)。

        画面の全体では右上の赤い知らせ (core/_toasts.html)、小窓では小窓の上の赤い帯 (core/_form_modal.html の core/_notices.html)。
        番号は「入力」(決まりの番号でない) なので、番号と「?」は出ない (services の ValueError の断りと同じ道)。
        """
        try:
            name = self.get_service().__name__
        except RuntimeError:
            name = ""
        message = invalid_form_message(form, refusal_head(name, self.DELETE_SERVICE_PREFIXES))
        return self.render_to_response(self.get_context_data(form=form, notice=Notice("入力", message)))

    def saved_in_modal(self, text: str, level: str) -> HttpResponse:
        """小窓の形の保存の後: 本文の無い 204 に、HX-Trigger の seizou:saved (知らせの字 message と色 level) を付ける。

        htmx は 204 では小窓の中身を差し替えず、form に seizou:saved を出す (body まで泡立つ)。messages には残さない
        (残すと、次に画面の全体を開いたときに古い知らせが出る)。JSON は既定の ensure_ascii (日本語は \\u の形) のまま
        (見出しの値は latin-1 の字だけ。日本語を生で入れると Django が =?utf-8?b?…?= の形に変え、htmx が読めない)。
        """
        import json

        response = HttpResponse(status=204)
        response["HX-Trigger"] = json.dumps({"seizou:saved": {"message": text, "level": level}})
        return response

    def get_success_url_for(self, result) -> str:
        """保存の後の移り先。success_url → 保存した物の詳細 → 開いた元の画面 (?next=) → 一覧 → /。

        ?next= は一覧へ戻る形 (保存した物の詳細が無い物) にだけ効く。受注の登録のように保存した物の詳細へ移る形は今のまま。
        """
        if getattr(self, "success_url", None):
            return str(self.success_url)
        if result is not None and hasattr(result, "_meta") and getattr(result, "pk", None) is not None:
            url = model_url(result.__class__, "detail", result.pk)
            if url:
                return url
        if self.next_url:
            return self.next_url
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
        context["in_modal"] = self.in_modal
        # 小窓では「キャンセル」を出さない (閉じるボタンだけ)。開いた元の画面 (?next=) があればそこへ、無ければ一覧へ戻す。
        context["next_url"] = self.next_url
        if self.in_modal:
            context["cancel_url"] = None
        else:
            context["cancel_url"] = context["next_url"] or (model_url(self.model, "list") if self.model is not None else None)
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


def use_clearable_file_widget(field) -> None:
    """ファイルの欄の ClearableFileInput を、input だけを描く ClearableFileWidget に替える (初見テスト R5 の a-flagged-13。候補 C40 の その 2)。

    Django の ClearableFileInput は「現在: <link>」「クリア」「変更:」を input と同じ所に描き、破線の枠 (core/_file_field.html) の
    中に並んでいた。替えた後は、枠の中は新規登録と同じ (input・絵・「ファイルを選ぶ か、ここへドラッグ」) で、
    「現在:」の link と「クリア」の check は枠の上に出る (部品の編集の図面・会社の情報のロゴ)。
    元の widget の attrs (accept・id など) と is_required を写す (use_decimal_input と同じ形)。is_required を写さないと、
    必須の欄にも「クリア」が出て、送られた「クリア」を消す (False) と読む。
    替えるのは ClearableFileInput の欄だけ。FileInput の子 (feedback の FileWidget) は当たらない。
    新規の入力の欄 (注文書の取り込み・CSV の取り込み・添付ファイル) は今のファイルが無いので、画面は今と同じ。
    """
    old = field.widget
    if not isinstance(old, django_forms.ClearableFileInput) or isinstance(old, ClearableFileWidget):
        return
    widget = ClearableFileWidget(attrs=dict(old.attrs))
    widget.is_required = old.is_required
    widget.is_localized = old.is_localized
    field.widget = widget


def use_label_required(name: str, field) -> None:
    """空のまま保存したときの字 (required) を、見出し入りの字にする (初見テスト R5 の V37。候補 C25 の 2)。

    決め 11-06 18:3x C25-1 = a「全部の画面の「空のまま」の字を、まとめて「<見出し>を入れてください。」
    (選ぶ欄は「<見出し>を選んでください。」) に替える」。「このフィールドは必須です。」では何を入れるか分からず、
    「フィールド」は事務の言葉でない (V37)。欄を見つけるたびに直さずに済むよう、入力の土台でまとめて替える。
    替えるのは required の字が Django の既定のままの欄だけ。欄ごとに書いた字 (出荷日・休日・文字コード・受信した注文など) は
    そのまま残す。欄ごとに直したい字は、今までどおり forms.py の error_messages に書く。隠した欄は字が画面に出ないので替えない。
    字は欄の class で選ぶ (widget では選ばない): 1 つの checkbox (BooleanField) は「<見出し>に チェックを入れてください。」、
    選ぶ欄 (ChoiceField の子) とファイルの欄 (FileField の子) は「<見出し>を選んでください。」、ほか (字・数・日付) は
    「<見出し>を入れてください。」。見出しの無い欄は、画面の見出しと同じ pretty_name(name)。
    form ごとの写しの欄の error_messages を書き換えるので、class の欄 (base_fields) と次の要求には残らない。
    届かない所: 受注と発注の明細の行 (formset。get_form は親の form の欄だけを回す) と、BaseFormView を使わない form (ログインなど)。
    """
    if field.widget.is_hidden:
        return
    if str(field.error_messages.get("required", "")) != str(django_forms.Field.default_error_messages["required"]):
        return
    label = field.label if field.label is not None else pretty_name(name)
    if isinstance(field, django_forms.BooleanField):
        message = f"{label}に チェックを入れてください。"
    elif isinstance(field, (django_forms.ChoiceField, django_forms.FileField)):
        message = f"{label}を選んでください。"
    else:
        message = f"{label}を入れてください。"
    field.error_messages["required"] = message


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


#: 検索で - と同じに扱うダッシュ (‐ ‑ ‒ – — ― −。NFKC で - にならない物。全角の「－」は NFKC で - になる)
SEARCH_DASHES = "‐‑‒–—―−"
#: 1 語の揺れの数の上限 (1 語の Q は lookups の数 × この数の icontains の OR まで)
SEARCH_VARIANTS_MAX = 24


def _dash_variant(text: str) -> str:
    """SEARCH_DASHES の字を全部 - にし、長音の「ー」は直前の字が ASCII の英数字のときだけ - にした字
    (品番の「WKー1001」を「WK-1001」に当てる。「メーター」の「ー」は変えない)。"""
    chars = []
    for index, ch in enumerate(text):
        prev = text[index - 1] if index else ""
        if ch in SEARCH_DASHES or (ch == "ー" and prev.isascii() and prev.isalnum()):
            chars.append("-")
        else:
            chars.append(ch)
    return "".join(chars)


def _kana_switch_variants(text: str) -> list[str]:
    """1 語の中の仮名の 1 か所で字の種類を切り替えた揺れ (「ニッケルメッキ」から「ニッケルめっき」を作る。初見テスト 3 回目)。

    隣り合う 2 字がどちらも仮名 (ひらがな・カタカナ・長音の「ー」) の所 k ごとに、カタカナ (text[:k]) + ひらがな (text[k:]) と
    ひらがな (text[:k]) + カタカナ (text[k:]) の 2 つを、左の k から順に返す。仮名の外の字は変えない。2 か所以上の切り替えは作らない。
    """

    def is_kana(ch: str) -> bool:
        return "ぁ" <= ch <= "ゖ" or "ァ" <= ch <= "ヶ" or ch == "ー"

    def kata(part: str) -> str:
        return "".join(chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch for ch in part)

    def hira(part: str) -> str:
        return "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in part)

    variants = []
    for k in range(1, len(text)):
        if is_kana(text[k - 1]) and is_kana(text[k]):
            variants += [kata(text[:k]) + hira(text[k:]), hira(text[:k]) + kata(text[k:])]
    return variants


def search_variants(term: str) -> list[str]:
    """検索の語の揺れ。全角と半角 (NFKC)・ひらがなとカタカナを同じに扱う (初見テスト 2 回目の所見)。

    返す物は重ねない並び (打った字・NFKC・長音とダッシュ (英数字の後の「ー」・‐・− など) を - にした形・カタカナ・ひらがな・
    1 語の中の仮名の 1 か所で字の種類を切り替えた形)。DB の側は変えず、OR で引く。揺れの数は SEARCH_VARIANTS_MAX まで
    (仮名が長く続く語は、右の方の切り替えを落とす)。
    """
    folded = unicodedata.normalize("NFKC", term)
    kata = "".join(chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch for ch in folded)
    hira = "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in folded)
    variants = [term, folded, _dash_variant(folded), kata, hira, *_kana_switch_variants(folded)]
    return list(dict.fromkeys(variants))[:SEARCH_VARIANTS_MAX]


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
    """小窓の土台。見るだけと、詳細への移動のボタンと、編集の権限のある人には編集の link。template は <app>/_modal.html か core/_modal.html。

    項目 (先頭の modal_fields 個) の後に、会社ごとの項目 (一覧に出す物。一覧の列と同じ取り方) を見出しと値で並べる。
    1 項目 = {"label", "value", "money", "boolean"} (boolean は値が真偽なら真で、テンプレートが bool_mark で印にする。C55)。
    tabs = ((見出し, related_name), ...) があれば、関係する物の一覧も出す (詳細のタブと同じ形。3 つ目の FK の名前の並びも同じ)。
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
            if access.is_money_name(f.name) and not access.can_see_money(self.request.user):
                continue  # 金額の閲覧が無い人には、金額の項目を項目ごと出さない (「—」も出さない。N155)
            value = display_value(obj, f.name)
            if isinstance(f, models.FileField):
                info = file_info(value)
                value = info["name"] if info else ""
            items.append(
                {
                    "label": str(f.verbose_name),
                    "value": value,
                    "money": access.is_money_name(f.name),
                    "boolean": isinstance(value, bool),
                }
            )
        ext_items = []
        for col in company_fields.list_columns_for(obj.__class__):
            value = company_fields.ext_value(obj, col.name)
            ext_items.append({"label": col.label, "value": value, "money": False, "boolean": isinstance(value, bool)})
        context["fields"] = items[: self.modal_fields] + ext_items
        context["tabs"] = [
            related_tab(obj, label, related, names) for label, related, names in _tab_specs(self.tabs) if hasattr(obj, related)
        ]
        context["detail_url"] = model_url(obj.__class__, "detail", obj.pk)
        context["edit_url"] = model_url(obj.__class__, "update", obj.pk)
        if context["edit_url"] and not access.can_write_model(self.request.user, obj.__class__, "update"):
            context["edit_url"] = None
        return context


class BaseSelectView(ScreenMixin, View):
    """候補の口。Tom Select の候補を 1 ページ返す (05 の 2 節)。

    columns に項目の名前を並べると、候補ごとに id と text の横にその値も返す (例 部品の unit と standard_price。
    選ぶ欄の JS が、同じ行の空の単位と単価の欄に写す = 05 の 2 節の「選んだら写す」)。金額の項目
    (access.is_money_name) は、金額の閲覧が無い人には返さない。
    候補ごとに text (選んだ値の欄の字。今の 1 行) の横に、2 段の字 title と sub (core.select_options.lines) も返す (N162)。
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
        if not term and page == 1:  # 字を打つ前の 1 ページ目だけ (N162)
            recent = [self.candidate(obj, names) for obj in select_options.recent(qs)]
        create = select_options.create_entry(request.user, self.model)
        return JsonResponse({"items": items, "more": more, "recent": recent, "create": create})

    def candidate(self, obj, names) -> dict:
        """候補の 1 つ。text は今の 1 行の字 (選んだ値の欄の字。変えない)。title と sub は候補の 2 段の字 (N162)。"""
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


def with_saved_note(message: str, result) -> str:
    """入力の土台の知らせの字。service が返した物に saved_message (字) があれば「保存しました。」をその字に置き換え、
    saved_note (1 文の字) があれば知らせの後ろに付ける。

    例: 受信した注文と読み取りを受注にした直後の「明細 n 行を手入力せずに受注にしました。」(orders.services.create_order が付ける)。
    部品の画面は知らせの文を上書きできない (R15 の 11 に success_message が無い) ので、文と色は service の返す物が持つ
    (色は saved_level。"warning" なら form_valid が黄 (確認してください) の知らせにする)。
    """
    saved_message = getattr(result, "saved_message", "")
    if isinstance(saved_message, str) and saved_message:
        message = saved_message
    note = getattr(result, "saved_note", "")
    return f"{message}{note}" if isinstance(note, str) and note else message


def dashboard_stats() -> list[dict]:
    """最初の画面の数字のカード。部品が無い物は出さず、数は本物の集計だけ (DB が読めない物も出さない)。

    1 枚 = {"label", "value", "unit", "note", "help", "icon", "url", "url_name"} (と、数の下の 1 行 "detail" のあるカード)。"url_name" は url を作った URL の名前で、
    行き先のあるカードだけに付く (HomeView が、部署で開けない画面へ行くカードの url を外すのに使う)。金額のカード (支払の見込みと入金) は "money" が真で、
    HomeView が金額を見られない人には並びから外す (カードごと出さない)。
    "help" は数え方の 1 文 (何をどう数えたかと期間)。画面はカードの題の横の (i) の小窓に出す (初見ユーザーテスト R4 の 5-3。UX の案 6-E)。
    文は下の数え方の式と合わせる (式を変えたら文も直す)。
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
                "help": "部品マスターにある部品の数です。使用を止めた部品も数えます。",
                "icon": "box",
                "url": url_or_none("masters:item_list"),
                "url_name": "masters:item_list",
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
                "help": "登録した取引先 (得意先・協力企業・両方) の数です。使用を止めた取引先も数えます。",
                "icon": "users",
                "url": url_or_none("masters:partner_list"),
                "url_name": "masters:partner_list",
            }
        )
    order_model = load_model("seizou.orders.models.Order")
    if order_model is not None:
        this_month = order_model._default_manager.filter(
            order_date__year=today.year, order_date__month=today.month
        ).exclude(status=order_model.STATUS_CANCELLED)
        order_list_url = url_or_none("orders:order_list")
        month_end = date.fromordinal(date(today.year + today.month // 12, today.month % 12 + 1, 1).toordinal() - 1)
        stats.append(
            {
                "label": "今月の受注",
                "value": count_or_none(this_month),
                "unit": "件",
                "note": f"{today.month} 月に受けた受注の件数 (取消を除く)",
                "help": "受注日が今月の受注の件数です。取消は数えません。",
                "icon": "clipboard",
                "url": f"{order_list_url}?f_open=0&f_ordered_from={today:%Y-%m}-01&f_ordered_to={month_end}" if order_list_url else None,
                "url_name": "orders:order_list",
            }
        )
    lines = dashboard.lines_to_ship()
    if lines is not None:
        lines_stat = {
            "label": "出荷の残りがある明細",
            "value": count_or_none(lines),
            "unit": "行",
            "note": "出荷が終わっていない受注の明細の数",
            "help": "受注の数量から出荷した数を引いた残りがある明細の数です。取消の受注は数えません。",
            "icon": "activity",
            "url": url_or_none("orders:orderline_list"),
            "url_name": "orders:orderline_list",
        }
        # 数の下の 1 行 = 受注の件数 (受注の一覧のタブ「出荷の残りがある」と今日やることの出荷の行と同じ数え方。R5 の V78 で、
        # 明細の行の数を受注の件数と読み違えそうと書かれたため)。数えられないときは 1 行ごと出さない (偽の 0 を置かない)。
        open_orders = dashboard.orders_to_ship()
        open_count = count_or_none(open_orders) if open_orders is not None else None
        if open_count is not None:
            lines_stat["detail"] = f"受注 {open_count:,} 件"
        stats.append(lines_stat)
    inbox_model = load_model("seizou.connect.models.Inbox")
    if inbox_model is not None:
        waiting = inbox_model._default_manager.filter(status=inbox_model.STATUS_RECEIVED)
        stats.append(
            {
                "label": "受信した注文",
                "value": count_or_none(waiting),
                "unit": "件",
                "note": "人が受け入れるのを待つ注文の数",
                "help": "受信した注文 (取引先から直接届いた物と、読み取った注文書から入った物) のうち、まだ受け入れも断りもしていない物の数です。",
                "icon": "inbox",
                "url": url_or_none("connect:inbox_list"),
                "url_name": "connect:inbox_list",
            }
        )
    # 6 枚目: 今月の支払の見込み (受入の部品があるとき。受け入れた数 × 発注の単価の合計 = N42 の要望 4)
    payable = dashboard.payable_this_month(today)
    if payable is not None:
        stats.append(
            {
                "label": "今月の支払の見込み",
                "value": payable,
                "unit": "",
                "note": f"{today.month} 月に受け入れた数量 × 発注の単価の合計",
                "help": "受入日が今月の受入の金額 (受け入れた数量 × 発注の単価) の合計です。",
                "icon": "yen",
                "url": url_or_none("purchasing:receipt_list"),
                "url_name": "purchasing:receipt_list",
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
                "unit": "",
                "note": f"{today.month} 月に入金日のある入金の金額の合計 (取消を除く)",
                "help": "入金日が今月の入金の金額の合計です。取消は数えません。",
                "icon": "yen",
                "url": url_or_none("billing:invoice_list"),
                "url_name": "billing:invoice_list",
                "money": True,
            }
        )
    # 8 枚目: 手入力せずに取り込んだ明細 (受注の部品があるとき。読み取りと受信した注文から受注にした今月の明細の行数と、入力を省けた時間 = N83)
    hands_free = dashboard.hands_free_lines(today)
    if hands_free is not None:
        minutes = f"{dashboard.minutes_per_line().normalize():f}"
        note = f"{today.month} 月に、読み取った注文書と受信した注文から受注にした明細の数 (1 行 {minutes} 分で数える)"
        accept = dashboard.accept_minutes_this_month(today)
        if accept is not None:
            note += f"。受付の平均 {dashboard.transcription_text(accept)}"
        stats.append(
            {
                "label": "手入力せずに取り込んだ明細",
                "value": hands_free,
                "unit": "行",
                "detail": f"入力を省けた時間 ≒ {dashboard.transcription_text(dashboard.saved_minutes(hands_free))}",
                "note": note,
                # 分の数は会社の設定 transcription_minutes_per_line (dashboard.minutes_per_line。既定は conf.DEFAULTS)
                "help": (
                    "受注日が今月の受注のうち、読み取った注文書か受信した注文から作った受注の明細の行の数です。取消は数えません。"
                    f"入力を省けた時間は、1 行を手入力する時間を {minutes} 分と置いた目安です。"
                ),
                "icon": "check",
                "url": None,
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
    """最初の画面 (ログインの後の移動先。全員が意識する KPI の置き場)。上から 数字のカード → 今日やること (手を動かす物のある段と移る先) と納期アラート → 出荷と生産の図 → 最近の受注と注文の入口 → よく使う画面 (☆ で登録した画面)。

    使う人: 全員 / いつ: ログイン直後と朝 / 何のために: 今日の数字と、出荷と生産の図、納期の遅れを見る / 次にすること: 今日やることのボタンから、その仕事の一覧へ。遅れの行から生産管理ボードへ。☆ で登録した画面へ
    """

    screen_name = "ダッシュボード"
    screen_help = "今日の数字、今日やること (手を動かす物の数と移る先)、出荷と生産の図、最近の受注と納期の遅れ。上の帯の ☆ で登録した画面が「よく使う画面」に並びます。"
    template_name = "core/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        # 部署で開けない画面へ行くカードは、url を外した写しにする (数は出し、押せない div で描く。決め A-03。初見テスト R5 の U159。
        # 候補 C12 の 3)。判定は今日やることの行を外すのと同じ引き方 (URL の名前の : の前の部品を can_open。記録は書かない)。
        # 並びの中の dict は書き換えず、新しい並びを作る (試験が同じ dict を返す関数に差し替えるため)。写しは下の画面の名前の枝に入らない。
        context["stats"] = [
            {**stat, "url": None}
            if stat.get("url_name") and not access.can_open(user, stat["url_name"].split(":")[0], record=False)
            else stat
            for stat in dashboard_stats()
        ]
        for stat in context["stats"]:
            if stat.get("url"):
                # カードの行き先の link の字「<画面の名前>を見る」の名前。「?」の後ろ (絞りの GET) を付けたままでは引けないので外す。
                # 引けなければ空で、画面は「一覧を見る」を出す (詳細のパンくずの「一覧に戻る」と同じ考え)。R5 の V38
                stat["screen"] = screen_name_of(stat["url"].split("?", 1)[0])
        context["recent_orders"] = recent_orders()
        context["orders_url"] = url_or_none("orders:order_list")
        context["alerts"] = dashboard.due_alerts()
        context["shipping"] = dashboard.shipping_trend()
        context["production"] = dashboard.production_trend()
        if not access.can_open(user, "orders", record=False):
            # 受注の部品を部署で開けない人には、納期アラートの「生産管理ボードを見る」と最近の受注の「受注の一覧を見る」を出さない
            # (テンプレートは値が無ければ link ごと出さない。行の受注番号の link は受注の詳細へ移り、詳細は部署を見ないので残す)。
            # due_alerts の値は書き換えず、写しの list_url だけを外す (候補 C12 の 3)。
            context["orders_url"] = None
            if context["alerts"] is not None:
                context["alerts"] = {**context["alerts"], "list_url": None}
        if not access.can_see_money(self.request.user):
            context["recent_orders"] = access.without_totals(context["recent_orders"])
            context["shipping"] = dashboard.without_amounts(context["shipping"])
            # 金額のカードは並びから外す (カードごと出さない。決め 11-05 09:3x)。並びの中の dict は書き換えず、新しい並びを作る。
            context["stats"] = [stat for stat in context["stats"] if not stat.get("money")]
        return context


def decimal_or_none(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


#: 決まりの一覧 (作品のリポジトリの根の RULES.md。src/seizou/core/views.py の 3 つ上。配布の wheel には入らない)
RULES_MD = Path(__file__).resolve().parents[3] / "RULES.md"
#: 決まりの番号の形 (R と 2 桁)
RULE_CODE = re.compile(r"^R[0-9]{2}$")


def rule_line(code: str) -> dict | None:
    """RULES.md の表の、番号が code の行。{"code", "text" (2 つ目の欄。` を外す), "section" (行の上の ## の見出しの字)}。

    番号の形が違う・RULES.md が無いか読めない・行が無ければ None。読むだけ。
    行の読み方は tests/test_r14_no_raw_sql.py の _rules_md_text と同じ (「| R01 |」で始まる行の 2 つ目の欄)。
    """
    if not isinstance(code, str) or not RULE_CODE.match(code):
        return None
    try:
        text = RULES_MD.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    section = ""
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        elif line.startswith(f"| {code} |"):
            return {"code": code, "text": line.split("|")[2].strip().replace("`", ""), "section": section}
    return None


#: 設定の行の 2 文目 (会社の設定の値ごと)。R01・R04・R06・R07 のどれにも合う字にする (R04 は「超える」でないため)
RULE_SETTING_EFFECTS = {
    "stop": "この決まりに当たると保存しません。",
    "warn": "黄の知らせと「このまま保存」のボタンが出ます。",
    "off": "知らせずに保存します。",
}


def rule_setting_line(code: str) -> str:
    """決まりの番号の「?」の説明に出す、この会社の今の設定の 1 行。設定の鍵の無い番号は空の字。

    例「この会社の設定「受注より多い出荷 (R01・R04)」は「止める」です。この決まりに当たると保存しません。」。
    鍵は検査の層の登録 (checks.setting_key_for) から、見出しと値の字は設定の画面の SETTING_LABELS から引く。
    鍵が SETTING_LABELS に無ければ値を読まない (conf.setting は知らない鍵で KeyError)。値が表に無い字のときも空。
    Check.current_mode は使わない (知らない値で ValueError を投げ、説明が 500 になるため)。読むだけ。
    """
    key = checks.setting_key_for(code)
    if not key:
        return ""
    # settings_screen は module の頭で core.views を import するので、ここで import する (循環を避ける)
    from seizou.core import conf
    from seizou.core.settings_screen import SETTING_LABELS

    label = SETTING_LABELS.get(key)
    if label is None:
        return ""
    title, words = label
    value = conf.setting(key)
    if not isinstance(value, str) or value not in words or value not in RULE_SETTING_EFFECTS:
        return ""
    return f"この会社の設定「{title}」は「{words[value]}」です。{RULE_SETTING_EFFECTS[value]}"


@login_required
@require_GET
def rule_help(request, code):
    """赤の知らせの決まりの番号の横の「?」が開く説明。番号の形が違えば 404。

    htmx の要求 (画面の全体の右上の知らせと、盤やメモの書く枠の帯の「?」。同じタブの小窓で開く) には小窓の中身
    (core/_rule_help.html) を返す。htmx でない要求 (小窓の入力の赤い帯の「?」が開く新しいタブ。決め C20-1 = a。
    同じ小窓に説明を入れると入れた値が消えるため) には画面の全体 (core/rule_help.html。土台の画面の中に同じ中身の箱を置き、
    小窓の閉じるボタンは出さない) を返す。同じ URL で中身と全体を返すので Vary: HX-Request を付ける。
    行が無い (RULES.md が置き場に無い・番号が表に無い) ときも 200 で、無い旨の 1 文を出す (htmx が小窓を開けるように)。
    設定の鍵のある番号 (R01・R04・R06・R07) は、この会社の今の設定の行も出す (rule_setting_line)。読むだけ。
    """
    if not RULE_CODE.match(code):
        raise Http404("決まりの番号の形が違います。")
    context = {"code": code, "rule": rule_line(code), "setting_line": rule_setting_line(code)}
    if request.headers.get("HX-Request"):
        response = render(request, "core/_rule_help.html", context)
    else:
        # 題の段は土台の物 (screen_name と絵)。screen_app は無いので絵を渡す (app_icon に空の app を渡さない)
        context.update(screen_name=f"{code} の決まり", screen_icon="help")
        response = render(request, "core/rule_help.html", context)
    patch_vary_headers(response, ("HX-Request",))
    return response
