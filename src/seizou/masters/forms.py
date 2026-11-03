"""masters の入力の form。保存は form でなく services が行う (土台が ModelForm の save を呼ぶことは無い)。

form に service_kwargs() が無いので、土台は cleaned_data を data= (会社ごとの項目は ext=) に分けて
services.create_item(data, user, ext=ext) / update_item(item, data, user, ext=ext) を呼ぶ (01 の 10.4)。
締日と支払日の min・max (1〜31) は土台の ServiceModelForm が表の検査から付ける。ここは欄の下の案内だけ。
"""

import re

from django import forms
from django.utils import timezone

from seizou.core import csvio
from seizou.core.forms import ServiceModelForm

from .models import Item, Partner, Process
from .models import MONTH_END_DAY
from .models import ItemDrawingVersion


class ItemForm(ServiceModelForm):
    class Meta:
        model = Item
        fields = ["part_no", "name", "drawing_no", "drawing", "unit", "standard_price", "note"]

    def __init__(self, *args, hide_money=False, **kwargs):
        super().__init__(*args, **kwargs)
        if hide_money:
            # 金額の閲覧が無い人 (権限の判定の on) には標準単価の欄を出さない。土台は cleaned_data を渡し、
            # update_item は渡した鍵だけを直すので、標準単価は保存済みの値のまま。
            self.fields.pop("standard_price", None)


class PartnerForm(ServiceModelForm):
    class Meta:
        model = Partner
        fields = [
            "code",
            "name",
            "short_name",
            "role",
            "closing_day",
            "payment_month",
            "payment_day",
            "rounding",
            "tax_mode",
            "note",
        ]
        help_texts = {
            "closing_day": "1〜31。月末締めは 31",
            "payment_day": "1〜31。月末は 31",
        }


class ProcessForm(ServiceModelForm):
    """工程を入れる・直す。外注か (is_outsourced) は欄に出さない (入れるときは偽、直すときは今の値のまま)。"""

    class Meta:
        model = Process
        fields = ["code", "name", "sort_order", "use_in_plan"]


# ---------------------------------------------------------------- CSV 取り込み (05 の 8 節)

#: 支払日の字の月の部分 (画面の字, 値)。長い字から合わせる (「翌々月」を「翌月」と読み違えない)。
_MONTH_WORDS = tuple(
    sorted(((str(label), value) for value, label in Partner.PAYMENT_MONTH_CHOICES), key=lambda pair: -len(pair[0]))
)
_DAY_TEXT = re.compile(r"(\d+)\s*日?")


def _text_of(raw):
    return "" if raw is None else str(raw).strip()


def _day_digits(text):
    """日の字 (「20」「20 日」「20日」「月末」) を数の字にする。読めなければ None。"""
    if text == "月末":
        return str(MONTH_END_DAY)
    match = _DAY_TEXT.fullmatch(text)
    return match.group(1) if match else None


def read_closing_day(field, raw, present):
    """締日の字を読む。空は空、「20」「20 日」は 20、「31」「月末」は 31。1〜31 の外れは表の検査が誤りにする。

    一覧の CSV の書き出しは締日を画面の字 (Partner.get_closing_day_display) で出すので、それを読み戻す。
    """
    text = _text_of(raw)
    digits = _day_digits(text) if text else ""
    if digits is None:
        raise ValueError(f"{field.verbose_name}「{text}」の形が違います。「20」「20 日」「月末」のように書いてください。")
    return {field.name: csvio.to_value(field, digits)}


def read_payment_day(field, raw, present):
    """支払日の字を読む。present は CSV にある列の名前の集合。

    支払の月の列が無いとき (一覧の書き出しの形。Partner.get_payment_day_display の字) は、
    「翌月 25 日」「翌月末」「当月末」「25 日」「月末」「翌月」から支払の月と支払日の 2 つを読む。
    支払の月の列があるとき (examples の形) は、日だけ (「25」「25 日」「31」「月末」) を読み、月の字があれば誤り。
    """
    text = _text_of(raw)
    if "payment_month" in present:
        if any(word in text for word, _value in _MONTH_WORDS):
            raise ValueError(
                f"{field.verbose_name}「{text}」に月の字があります。"
                "支払の月の列があるときは、支払日には日だけ (「25」「月末」) を書いてください。"
            )
        digits = _day_digits(text) if text else ""
        if digits is None:
            raise ValueError(f"{field.verbose_name}「{text}」の形が違います。「25」「25 日」「月末」のように書いてください。")
        return {field.name: csvio.to_value(field, digits)}

    month, rest = "", text
    for word, value in _MONTH_WORDS:
        if text.startswith(word):
            month, rest = value, text[len(word) :].strip()
            break
    if not rest:
        digits = ""
    elif month and rest == "末":
        digits = str(MONTH_END_DAY)
    else:
        digits = _day_digits(rest)
    if digits is None:
        raise ValueError(
            f"{field.verbose_name}「{text}」の形が違います。「翌月 25 日」「翌月末」「25」のように書いてください。"
        )
    return {"payment_month": month, field.name: csvio.to_value(field, digits)}


class CsvImportForm(forms.Form):
    """CSV 取り込みの form の共通の親。子は下の属性を決める。

    clean() がファイルを読み、行ごとに字を値に直して確かめる。誤りは (行の番号, 欄 = 見出しの字, 理由) で
    import_errors に集め、1 件でもあれば form を通さない (1 件も入れない)。通れば cleaned_data["rows"] に
    services.import_* に渡す行を置き、import_counts (入れる・更新する・飛ばした の数) を置く。表には書かない。
    """

    #: 取り込む表 (子が決める)
    model = None
    #: 同じ行かどうかを決める鍵の列の名前 (自社品番 = part_no・取引先コード = code)
    key_name = ""
    #: 無ければ誤りにする見出し
    required_headers = ()
    #: テンプレートが「一覧に戻る」と「見本の CSV をダウンロード」の link を作る URL の名前
    list_url_name = ""
    sample_url_name = ""
    #: 列の名前 → 字を読む関数 (field, 字, CSV にある列の名前の集合) → {列の名前: 値}。無い列は csvio.to_value。
    cell_readers = {}

    file = forms.FileField(label="CSV のファイル")
    encoding = forms.ChoiceField(
        label="文字コード",
        choices=csvio.ENCODINGS,
        initial=csvio.DEFAULT_ENCODING,
        help_text=(
            "Excel で「CSV (コンマ区切り)」で保存したファイルは Shift_JIS、「CSV UTF-8」で保存したファイルと"
            "一覧の「CSV」のボタンで落としたファイルは UTF-8 を選びます。"
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.import_errors = []
        self.import_counts = None
        self.header_hint = "・".join(csvio.header_labels(self.model))

    def clean(self):
        cleaned = super().clean()
        upload = cleaned.get("file")
        encoding = cleaned.get("encoding")
        if upload is None or not encoding:
            return cleaned
        errors = []
        rows, skipped = [], 0
        upload.seek(0)
        try:
            text = csvio.decode(upload.read(), encoding)
        except ValueError as exc:
            errors.append((None, "文字コード", str(exc)))
        else:
            try:
                _headers, columns, ext_columns, table = csvio.read_table(
                    text, self.model, required=self.required_headers
                )
            except ValueError as exc:
                errors.append((None, "", str(exc)))
            else:
                rows, skipped = self._read_rows(table, columns, ext_columns, errors)
        if errors:
            self.import_errors = errors
            raise forms.ValidationError(
                f"CSV に誤りが {len(errors)} 件あります。1 件も入れていません。"
                "下の表を見て直してから、もう一度選んでください。"
            )
        cleaned["rows"] = rows
        self.import_counts = self._counts(rows, skipped)
        return cleaned

    def _read_rows(self, table, columns, ext_columns, errors):
        """行ごとに字を値に直す。誤りは errors に足す。返す物は (行の並び, 飛ばした空の行の数)。"""
        present = {field.name for field in columns.values()}
        key_header = next((h for h, field in columns.items() if field.name == self.key_name), "")
        first_line = {}
        rows, skipped = [], 0
        for line, cells in table:
            if csvio.is_blank_row(cells.values()):
                skipped += 1
                continue
            data, ext = {}, {}
            for header, raw in cells.items():
                try:
                    if header in columns:
                        field = columns[header]
                        reader = self.cell_readers.get(field.name)
                        values = reader(field, raw, present) if reader else {field.name: csvio.to_value(field, raw)}
                        data = {**data, **values}
                    elif header in ext_columns:
                        column = ext_columns[header]
                        ext[column.name] = csvio.to_ext_value(column, raw)
                except ValueError as exc:
                    errors.append((line, header, str(exc)))
            key = data.get(self.key_name)
            if key and key in first_line:
                errors.append((line, key_header, f"{key} は {first_line[key]} 行目にもあります。1 行にまとめてください。"))
            elif key:
                first_line[key] = line
            rows.append({"line": line, "data": data, "ext": ext or None})
        return rows, skipped

    def _counts(self, rows, skipped):
        """入れる・更新する・飛ばした の数 (読むだけ)。"""
        existing = set(self.model.objects.values_list(self.key_name, flat=True))
        updating = sum(1 for row in rows if row["data"][self.key_name] in existing)
        return {"入れる": len(rows) - updating, "更新する": updating, "飛ばした": skipped}

    def service_kwargs(self):
        return {"rows": self.cleaned_data["rows"]}


class ItemCsvImportForm(CsvImportForm):
    model = Item
    key_name = "part_no"
    required_headers = ("自社品番", "品名")
    list_url_name = "masters:item_list"
    sample_url_name = "masters:item_import_sample"


class PartnerCsvImportForm(CsvImportForm):
    model = Partner
    key_name = "code"
    required_headers = ("取引先コード", "名前", "取引先の種類")
    list_url_name = "masters:partner_list"
    sample_url_name = "masters:partner_import_sample"
    cell_readers = {"closing_day": read_closing_day, "payment_day": read_payment_day}


# ---------------------------------------------------------------- 図面を版に戻す (第 3 波)


class _VersionChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        saved = timezone.localtime(obj.created_at) if timezone.is_aware(obj.created_at) else obj.created_at
        text = f"版 {obj.version_no} ({saved:%Y-%m-%d %H:%M})"
        return f"{text} {obj.note}" if obj.note else text


class DrawingRestoreForm(forms.Form):
    """図面を版に戻す確認の form。選べる版はこの部品の版だけ (新しい順)。

    土台は instance (部品) を渡し、services.restore_drawing_version(item, user=…, version=…, note=…) を呼ぶ。
    """

    version = _VersionChoiceField(label="戻す版", queryset=ItemDrawingVersion.objects.none(), empty_label=None)
    note = forms.CharField(
        label="備考",
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="戻す前の今の図面を版として残すときの備考になります。",
    )

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        if instance is not None:
            self.fields["version"].queryset = instance.drawing_versions.order_by("-version_no")

    def service_kwargs(self):
        return {"version": self.cleaned_data["version"], "note": self.cleaned_data["note"]}
