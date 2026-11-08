"""一覧の列 (ColumnSpec) と絞り (FilterSpec)。BaseListView が読む (core.views の package)。"""

from __future__ import annotations

import inspect
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from seizou.core import access
from seizou.core import fields as company_fields

from .base import CODE_NAMES, CODE_SUFFIXES, DATE_FIELDS, NUMERIC_FIELDS


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
      default と違う値のときを active とする。札 (BaseListView.filter_chips) は active のうち狭める物 (flag は入) だけで、「絞りを外す」は札があるときだけ出す (開発の記録 docs/nights/20261105/N161.md)。
      values: 同じ一覧の絞りの名前ごとの読めた値の辞書 (FilterSpec.values_by_name。読めた値の無い絞りは鍵が無い)。
      when が 2 つの引数を受ける関数なら when(queryset, values) で呼ぶ (1 つの関数は今までどおり when(queryset)。inspect.signature で見る)。
      disabled_when が真の間は、隠しの欄が印の値を持ち越す (入なら 1)。従う絞りを外すと、印は元の値に戻る。
      htmx の応答では、disabled_when のある印だけを core/_filter_clear.html が oob で描き直す (チェックと表を合わせる)。
    会社ごとの項目: field に ext__<列の名前> を書く。型が choice なら choice (候補は項目の choices)、date なら date_range、
      number なら number_range。text と bool は絞れない (ValueError)。kind は省ける (書くなら同じ種類)。
      BaseListView は一覧に出す会社ごとの項目のうち絞れる物を、宣言の後ろに自動で足す (GET の名前は f_<列の名前>)。
    金額の名前 (access.is_money_name) の絞りは、金額の閲覧の無い人には出さず効かせない (列と同じ。決め 11-05 09:4x)。印は money。
    """

    #: kind に書ける値
    KINDS = ("choice", "date_range", "number_range", "lookup", "flag")
    #: 期間の絞りの近道 (鍵と字。並びは画面の左から。受け渡しの seizou-部品の6案-20261107/指示書.md の 1。本人の決め 11-07 18:2x「いつからいつまでは良くあるパーツ」)
    DATE_PRESETS = (("today", "今日"), ("week", "今週"), ("month", "今月"), ("last_month", "先月"))
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
        # 金額の名前の絞り (ColumnSpec の money と同じ判定)。flag は field_name が None なので偽
        self.money = self.field_name is not None and access.is_money_name(self.field_name)
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
        elif self.kind == "date_range":
            item["presets"] = self.date_presets(item["value_from"], item["value_to"])
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

    @staticmethod
    def preset_range(key: str, today: date) -> tuple[date, date]:
        """期間の近道 key の始めと終わり (両端を含む)。今日 = その日、今週 = 月曜から日曜、今月 = 1 日から末日、先月 = 先月の 1 日から末日。

        日付は呼び手が会社の時刻 (timezone.localdate) で渡す。訳: ブラウザの時刻の差で 1 日ずれないように、サーバーで決めて
        ボタンの data 属性に書くため (受け渡しの seizou-部品の6案-20261107/指示書.md の 1)。知らない鍵は ValueError。
        """
        if key == "today":
            return today, today
        if key == "week":
            start = today - timedelta(days=today.weekday())
            return start, start + timedelta(days=6)
        if key == "month":
            start = today.replace(day=1)
            # 末日 = 次の月の 1 日の前の日 (32 日足すと必ず次の月に入る)
            return start, (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        if key == "last_month":
            end = today.replace(day=1) - timedelta(days=1)
            return end.replace(day=1), end
        raise ValueError(f"知らない期間の近道: {key!r}。{' / '.join(k for k, _ in FilterSpec.DATE_PRESETS)} のどれかにしてください。")

    @classmethod
    def date_presets(cls, value_from: str, value_to: str, today: date | None = None) -> list[dict]:
        """期間の欄の下の近道のボタンの材料 [{"key", "label", "start", "end", "selected"}] (core/_date_presets.html)。

        start と end は ISO の字 (欄の value と同じ形。JS がそのまま欄に入れる)。selected は今の欄の読めた値の字が始めも終わりも
        近道と同じとき (選んだ印はサーバーが描く。行の部分の応答では core/_filter_clear.html が oob で描き直す)。
        today を省くと会社の時刻の今日 (timezone.localdate)。
        """
        today = today or timezone.localdate()
        presets = []
        for key, label in cls.DATE_PRESETS:
            start, end = cls.preset_range(key, today)
            start_text, end_text = start.isoformat(), end.isoformat()
            presets.append(
                {"key": key, "label": label, "start": start_text, "end": end_text, "selected": value_from == start_text and value_to == end_text}
            )
        return presets

    #: 選ぶ欄の頭の選択肢の字 (core/_filters.html の <option value="">。幅を数えるときに候補に足す)
    ALL_TEXT = "すべて"

    @classmethod
    def select_width(cls, options) -> int:
        """選ぶ欄の幅 (ch の数)。候補と「すべて」のうち、いちばん幅の広い字で決める (作者の決め)。

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
