"""CSV の読み方の共通の関数 (05 の 8 節の「入れる」)。

部品の取り込みの form が使う。core の部品なので、ほかの部品を import しない (model は引数で受ける)。
「字 → 値」の決まりは作り物のデータの読み込み (seizou.core.demo) の関数をそのまま使う (決まりを 2 か所に書かない)。

- 文字コードは人が選ぶ (自動の判定はしない)。読めない字は黙って捨てず、日本語の ValueError にする。
- 見出しは表の verbose_name (日本語の項目名)・画面の字を直す前の見出し (FORMER_HEADERS)・会社ごとの項目の名前。
  対応の表は手で書かず、model の _meta と会社ごとの項目の登録から引く。
- 行の番号は見出しを 1 行目に数える。全部の欄が空の行 (Excel が残す ,,, の行) は誤りにせず、呼ぶ側が「飛ばした」に数える。
- ここは読むだけ。表に書くのは部品の services。
"""

from __future__ import annotations

import csv
import io

from django.core.exceptions import ValidationError

from seizou.core import fields as company_fields
from seizou.core.demo import _coerce, _coerce_ext, _fields_by_label, _is_blank

#: 文字コードの選択肢 (値, 画面の字)。utf-8-sig は BOM があれば外し、無くても読む。
ENCODINGS = (("utf-8-sig", "UTF-8 (BOM あり・なし)"), ("cp932", "Shift_JIS (Excel の既定)"))
DEFAULT_ENCODING = "utf-8-sig"
#: 読まない見出し (値があっても入れない。図面は部品の編集の画面で上げる)。
SKIP_HEADERS = ("図面ファイル",)


def decode(data, encoding):
    """bytes を選んだ文字コードで読む。読めない字があれば、行の番号を添えた日本語の ValueError。"""
    labels = dict(ENCODINGS)
    if encoding not in labels:
        raise ValueError(
            f"文字コード「{encoding}」は選べません。{'・'.join(labels.values())} のどれかを選んでください。"
        )
    try:
        return bytes(data).decode(encoding)
    except UnicodeDecodeError as exc:
        # 改行の字 (0x0A) は UTF-8 と Shift_JIS の 2 バイト目に現れないので、前の改行を数えると行の番号になる。
        line = bytes(data)[: exc.start].count(b"\n") + 1
        raise ValueError(
            f"選んだ文字コード ({labels[encoding]}) で読めない字があります ({line} 行目)。"
            "文字コードを選び直してください。"
        ) from None


def is_blank_row(values):
    """全部の欄が空の行か (Excel が残す ,,, の行)。"""
    return all(_is_blank(value) for value in values)


def _quoted(labels):
    return "「" + "」「".join(labels) + "」"


def read_table(text, model, *, required, skip=SKIP_HEADERS):
    """CSV の字を読み、見出しを確かめる。

    返す物は (見出しの並び, {見出し: 表の列}, {見出し: 会社ごとの項目の列}, [(行の番号, {見出し: 字})])。
    見出しの誤り (必須が無い・同じ見出しが 2 つ・どれにも当たらない・見出しの無い列に値がある) は、
    全部を 1 つの日本語の ValueError にまとめる。skip の見出しは列の対応に入れない (値も読まない)。
    """
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        records = [(reader.line_num, row) for row in reader]
    except csv.Error:
        raise ValueError(
            f"{reader.line_num} 行目を CSV として読めません。「\"」の閉じ忘れが無いかを見てください。"
        ) from None
    if not records:
        raise ValueError("CSV が空です。1 行目に見出しを書いてください。")

    headers = [cell.strip() for cell in records[0][1]]
    body = records[1:]
    width = max([len(headers), *(len(row) for _line, row in body)])
    headers += [""] * (width - len(headers))

    # 画面で入れられない列 (editable=False。例 図面の表示用) は CSV からも書かせない。
    by_label = {label: f for label, f in _fields_by_label(model).items() if f.editable}
    registration = company_fields.registration_for(model)
    ext_by_label = {c.label: c for c in registration.columns} if registration else {}

    problems = []
    for index, header in enumerate(headers):
        if header:
            continue
        line = next((line for line, row in body if index < len(row) and not _is_blank(row[index])), None)
        if line is not None:
            problems.append(
                f"{index + 1} 列目は見出しが空なのに、{line} 行目に値があります。見出しを書くか、その列を消してください。"
            )
    named = [h for h in headers if h]
    doubled = sorted({h for h in named if named.count(h) > 1}, key=named.index)
    if doubled:
        problems.append(f"見出し{_quoted(doubled)}が 2 つあります。1 つにしてください。")
    missing = [h for h in required if h not in named]
    if missing:
        problems.append(f"見出しに{_quoted(missing)}がありません。1 行目に足してください。")
    unknown = [h for h in named if h not in by_label and h not in ext_by_label and h not in skip]
    if unknown:
        problems.append(
            f"見出し{_quoted(unknown)}は、{model._meta.verbose_name}の表にも会社ごとの項目にもありません。"
            "見出しの字を直すか、その列を消してください。"
        )

    columns = {h: by_label[h] for h in dict.fromkeys(named) if h in by_label and h not in skip}
    ext_columns = {h: ext_by_label[h] for h in dict.fromkeys(named) if h not in by_label and h in ext_by_label}
    same_field = {}
    for header, field in columns.items():
        same_field.setdefault(field.name, []).append(header)
    for group in same_field.values():
        if len(group) > 1:
            problems.append(f"見出し{_quoted(group)}は同じ列です。1 つにしてください。")
    if problems:
        raise ValueError(" ".join(problems))

    rows = []
    for line, row in body:
        cells = list(row) + [""] * (width - len(row))
        rows.append((line, {h: cells[i] for i, h in enumerate(headers) if h}))
    return named, columns, ext_columns, rows


def header_labels(model, *, skip=SKIP_HEADERS):
    """見本の CSV の見出し。表の列 (主キーを除く) の今の verbose_name を表の並びで、その後に会社ごとの項目の名前。"""
    labels = [str(f.verbose_name) for f in model._meta.concrete_fields if not f.primary_key and f.editable]
    registration = company_fields.registration_for(model)
    labels += [c.label for c in registration.columns] if registration else []
    return [label for label in labels if label not in skip]


def _message(exc):
    return " ".join(str(m) for m in exc.messages)


def to_value(field, raw):
    """1 つの欄の字を表の列の値にする。型の変換 (demo._coerce) の後に、列の検査 (例 締日の 1〜31) も通す。"""
    value = _coerce(field, raw)
    try:
        field.run_validators(value)
    except ValidationError as exc:
        raise ValueError(f"{field.verbose_name}「{str(raw).strip()}」は使えません。{_message(exc)}") from None
    return value


def to_ext_value(column, raw):
    """1 つの欄の字を会社ごとの項目の値にする。型の変換 (demo._coerce_ext) の後に、入力の欄と同じ検査も通す。"""
    if column.required and _is_blank(raw):
        raise ValueError(f"{column.label}が空です。値を入れてください。")
    value = _coerce_ext(column, raw)
    if value in (None, "") or isinstance(value, bool):
        return value
    try:
        column.form_field().clean(value)
    except ValidationError as exc:
        raise ValueError(f"{column.label}「{str(raw).strip()}」は使えません。{_message(exc)}") from None
    return value
