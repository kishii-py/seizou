"""入力の欄の widget (選ぶ欄の設計の決め。枠は core)。

RemoteSelect: 候補をサーバーの候補の口 (BaseSelectView の子) から 1 ページずつ取る <select>。
描くときは選んである値の <option> だけを描き、全件を描かない (候補は static の js/seizou-select.js が
data-select-url の口から取る)。欄の見た目の class は入力の土台 (BaseFormView.get_form) が付ける。
小窓のある model (部品・取引先) では data-modal-url も付け、js が選んだ物の下に「詳細を見る」の link を出す (どの画面からも小窓で読む約束)。
query (辞書) を渡すと候補の口の URL に付ける (例 受注の得意先の欄は {"role": "customer"} で、協力企業を候補に出さない。
js は q と page をこの URL に足すので、query の鍵は残る)。
空の選択肢の字は「選んでください」(core の apps.ready が Django の既定の字を置き換え、field.empty_label がそれを持つ)。

DecimalInput: 数 (小数) の欄。type="text" と inputmode="decimal" (スマホは数の鍵盤)。入力の土台が
DecimalField・FloatField の数の入力をこれに替える。送られた字は全角を半角に、桁の区切りの「,」を外してから form に渡す。
初期値の Decimal は詳細と同じ字 (seizou_tags.comma。「1,200」「12.5」) で出し、印 data-number を付ける
(js/seizou-number.js が、この印の欄だけを、欄を離れた時に 3 桁ごとの区切りの字にそろえる)。
IntegerInput: 整数の欄。同じ形で inputmode="numeric"。入力の土台が IntegerField の数の入力をこれに替える。
DatalistInput: 字の欄に、既にある値の候補 (<datalist>) を付ける。候補は描くときに読む。候補に無い字もそのまま送る (断らない)。作者の決め
IsoDateInput: 日付の欄。type="date" と ISO の字 (2026-11-04) に、年の上限 max="9999-12-31" を付ける。作者の決め
上限が無いと、chromium (Chrome・Edge) の日付の欄は年の区切りに 6 桁 (275760 年) まで受け、4 桁で月へ進まない。
そのため 8 桁を続けて打つと「202611/02/0」のように崩れる。一覧の絞りの期間の欄 (core/_filters.html) も同じ上限を付ける。
Safari の系統は、Playwright の webkit (Safari そのものではない) で上限を付けても直らなかった。Mac と iPad の Safari そのものでは試していない。
ClearableFileWidget: 今のファイルを「クリア」で外せるファイルの欄。<input type=file> だけを描き、「現在:」の link と「クリア」は core/_file_field.html が枠の上に描く。作者の決め
"""

from __future__ import annotations

import unicodedata
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from urllib.parse import urlencode

from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html, format_html_join

from seizou.core.templatetags.seizou_tags import comma


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
        # data-number: 欄を離れた時に区切りを付ける JS (js/seizou-number.js) と、部品の単価を写す JS (js/seizou-select.js) が見る印。
        super().__init__(attrs={"inputmode": "decimal", "data-number": True, **(attrs or {})})

    def format_value(self, value):
        # 初期値の Decimal は詳細と同じ字 (comma。3 桁ごとの区切り・小数の末尾の 0 を落とす) で出す (1200.00 →「1,200」・12.50 →「12.5」)。
        # 作者の決め。打った字 (字で来る bound の値) はそのまま。
        if isinstance(value, Decimal):
            return comma(value)
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


class DatalistInput(forms.TextInput):
    """字の欄に、既にある値の候補を HTML の <datalist> で付ける (作者の決め)。

    欄は自由に打てるまま。候補に無い字もそのまま送り、知らせも出さない (断らない)。
    values は候補の字の並びを返す関数 (引数無し)。描くとき (render) に 1 回だけ呼び、作る時には呼ばない。
    widget を class の定義で作る使い方 (Meta.widgets など) で、起動の時に DB を読まないため (RemoteSelect が URL を描くときに引くのと同じ考え)。
    候補が 0 か、欄に id が無いときは、今の字の欄と同じ HTML (list の属性も <datalist> も出さない)。
    """

    def __init__(self, attrs=None, *, values):
        super().__init__(attrs=attrs)
        self.values = values

    def render(self, name, value, attrs=None, renderer=None):
        options = [str(option) for option in self.values()]
        element_id = self.build_attrs(self.attrs, attrs).get("id")
        if not options or not element_id:
            return super().render(name, value, attrs, renderer)
        list_id = f"{element_id}_list"
        field_html = super().render(name, value, {**(attrs or {}), "list": list_id}, renderer)
        option_html = format_html_join("", '<option value="{}"></option>', ((option,) for option in options))
        return format_html('{}<datalist id="{}">{}</datalist>', field_html, list_id, option_html)


class IsoDateInput(forms.DateInput):
    """日付の欄 (type="date")。値は ISO の字 (2026-11-04) で描き、年の上限 max="9999-12-31" を付ける (作者の決め)。

    上限の理由は頭の注記のとおり (chromium で 8 桁を続けて打てるようにするため)。
    Django の DateField が受ける年は 9999 年まで (Python の date の上限) なので、上限で入らなくなる日付は無い (断る処理ではない)。
    渡された attrs (class など) は残し、上限を足す。明細の行の form と雛形 (empty_form) の行も、この widget を使えば同じ上限が付く。
    """

    input_type = "date"
    #: 年の上限 (Python の date の上限と同じ日)
    MAX = "9999-12-31"

    def __init__(self, attrs=None, format="%Y-%m-%d"):
        super().__init__(attrs={"max": self.MAX, **(attrs or {})}, format=format)


class ClearableFileWidget(forms.ClearableFileInput):
    """今のファイルを「クリア」で外せるファイルの欄。描くのは <input type=file> だけにする (作者の決め)。

    Django の ClearableFileInput は「現在: <link>」「クリア」「変更:」を input と同じ所に描くので、破線の枠
    (core/_file_field.html) の中に入り、枠の中に字と check が並んでいた。この子は input だけを描き、
    「現在:」の行 (link と「クリア」の check) は core/_file_field.html が枠の上に描く。
    名前の末尾を Widget にして、BoundField.widget_type を今と同じ "clearablefile" に保つ
    (core/_form_card.html の分かれ道がそのまま当たる。先例 = feedback/forms.py の FileWidget)。
    文脈の値 (checkbox_name・checkbox_id・is_initial・initial_text・clear_checkbox_label・required・attrs.checked) は親のまま。
    テンプレートからは field.subwidgets.0.data で読む。「クリア」を読むのは親の value_from_datadict で、今と同じ。
    入力の土台 (BaseFormView.get_form の use_clearable_file_widget) が ClearableFileInput の欄をこれに替える。
    """

    template_name = "django/forms/widgets/file.html"

    def format_value(self, value):
        # file の input には今のファイルを value として出さない (FileInput と同じ)。親は今のファイルを返すため、
        # そのままでは <input type="file" value="drawings/…"> になる。
        return None

    def get_context(self, name, value, attrs):
        # 今のファイルの URL と名前だけ (drawings/ を落とした字。切り方は core.views.file_info と同じ) を文脈に足す。
        # value は format_value を通す前の元の値 (親の is_initial も同じ値で見ている)。
        context = super().get_context(name, value, attrs)
        if context["widget"]["is_initial"]:
            context["widget"]["current_url"] = value.url
            context["widget"]["current_name"] = value.name.rsplit("/", 1)[-1]
        return context
