"""入力の欄の widget (05 の 2 節。枠は core)。

RemoteSelect: 候補をサーバーの候補の口 (BaseSelectView の子) から 1 ページずつ取る <select>。
描くときは選んである値の <option> だけを描き、全件を描かない (候補は static の js/seizou-select.js が
data-select-url の口から取る)。欄の見た目の class は入力の土台 (BaseFormView.get_form) が付ける。
小窓のある model (部品・取引先) では data-modal-url も付け、js が選んだ物の下に「詳細を見る」の link を出す (どの画面からも小窓で読む約束)。
query (辞書) を渡すと候補の口の URL に付ける (例 受注の得意先の欄は {"role": "customer"} で、協力企業を候補に出さない。
js は q と page をこの URL に足すので、query の鍵は残る)。
空の選択肢の字は「選んでください」(core の apps.ready が Django の既定の字を置き換え、field.empty_label がそれを持つ)。

DecimalInput: 数 (小数) の欄。type="text" と inputmode="decimal" (スマホは数の鍵盤)。入力の土台が
DecimalField・FloatField の数の入力をこれに替える。送られた字は全角を半角に、桁の区切りの「,」を外してから form に渡す。
IntegerInput: 整数の欄。同じ形で inputmode="numeric"。入力の土台が IntegerField の数の入力をこれに替える。
"""

from __future__ import annotations

import unicodedata
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from urllib.parse import urlencode

from django.urls import NoReverseMatch, reverse


class RemoteSelect(forms.Select):
    """候補の口の URL の名前 (例 "masters:item_select") を持つ 1 つ選びの欄。query は候補の口の URL に付ける GET の値。"""

    def __init__(self, url_name: str, attrs=None, query: dict | None = None):
        super().__init__(attrs=attrs)
        self.url_name = url_name
        self.query = dict(query or {})

    def build_attrs(self, base_attrs, extra_attrs=None):
        attrs = super().build_attrs(base_attrs, extra_attrs)
        # URL は描くときに引く (import の時点では URL の設定が読めていないことがあるため)。
        url = reverse(self.url_name)
        attrs["data-select-url"] = f"{url}?{urlencode(self.query)}" if self.query else url
        modal = self.modal_url_template()
        if modal is not None:
            attrs["data-modal-url"] = modal
        return attrs

    def modal_url_template(self) -> str | None:
        """選んだ物を小窓で開く口の URL の型 (pk の所が 0。js が選んだ id に置き換える)。

        候補の口 <app>:<model>_select に対する <app>:<model>_modal。小窓の無い model (受注の明細など) は None。
        """
        if not self.url_name.endswith("_select"):
            return None
        try:
            return reverse(self.url_name[: -len("_select")] + "_modal", args=[0])
        except NoReverseMatch:
            return None

    def selected_choices(self, value) -> list[tuple]:
        """描く選択肢: 空の選択肢 (あれば) と、選んである値の物だけ。"""
        wanted = [v for v in value if v not in (None, "")]
        queryset = getattr(self.choices, "queryset", None)
        if queryset is None:
            # model でない選択肢 (固定の並び) は、空と選んである物だけを残す。
            return [(v, label) for v, label in self.choices if v == "" or str(v) in wanted]
        field = self.choices.field
        result = [("", field.empty_label)] if field.empty_label is not None else []
        if not wanted:
            return result
        key = field.to_field_name or "pk"
        try:
            rows = list(queryset.filter(**{f"{key}__in": wanted}))
        except (ValueError, TypeError, ValidationError):
            # 数でない値などが送られてきたときは、選んである物を描かない (欄の誤りは form が出す)。
            return result
        return result + [self.choices.choice(obj) for obj in rows]

    def optgroups(self, name, value, attrs=None):
        groups = []
        for index, (option_value, option_label) in enumerate(self.selected_choices(value)):
            selected = str(option_value) in value
            option = self.create_option(name, option_value, option_label, selected, index, attrs=attrs)
            groups.append((None, [option], index))
        return groups


def normalize_number(text: str) -> str:
    """数の欄の字をそろえる。全角を半角に (NFKC)、桁の区切りの「,」を外し、前後の空白を落とす。「１，２００」→「1200」。"""
    return unicodedata.normalize("NFKC", text).replace(",", "").strip()


class DecimalInput(forms.TextInput):
    """数 (小数) を入れる字の欄。誤り (「12e」など) はブラウザが空にせず、そのままサーバーへ送る (誤りは form が日本語で出す)。"""

    def __init__(self, attrs=None):
        super().__init__(attrs={"inputmode": "decimal", **(attrs or {})})

    def format_value(self, value):
        # 初期値の Decimal が整数なら小数の 0 を出さない (「480.00」でなく「480」。動画の確かめの所見 11-05)。打った字はそのまま。
        if isinstance(value, Decimal) and value == value.to_integral_value():
            return str(int(value))
        return super().format_value(value)

    def value_from_datadict(self, data, files, name):
        value = super().value_from_datadict(data, files, name)
        return normalize_number(value) if isinstance(value, str) else value


class IntegerInput(forms.TextInput):
    """整数を入れる字の欄。DecimalInput と同じく、誤り (「2e」など) をブラウザが空にせず、そのままサーバーへ送る。"""

    def __init__(self, attrs=None):
        super().__init__(attrs={"inputmode": "numeric", **(attrs or {})})

    def value_from_datadict(self, data, files, name):
        value = super().value_from_datadict(data, files, name)
        return normalize_number(value) if isinstance(value, str) else value
