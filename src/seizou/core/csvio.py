"""CSV の読み方の共通の関数 (CSV の取り込みの設計の決めの「入れる」)。

部品の取り込みの form が使う。core の部品なので、ほかの部品を import しない (model は引数で受ける)。
一覧の土台の CSV の書き出し (core.views の csv_response) も、真偽の字を to_text で作る (書き出しと取り込みの字を 1 か所にそろえるため)。
「字 → 値」の決まりは種と CSV の読み方の置き場 (seizou.core.seeds) の関数をそのまま使う (決まりを 2 か所に書かない)。

- 文字コードは人が選ぶ (自動の判定はしない)。読めない字は黙って捨てず、日本語の ValueError にする。
- 見出しは表の verbose_name (日本語の項目名)・画面の字を直す前の見出し (FORMER_HEADERS)・会社ごとの項目の名前。
  対応の表は手で書かず、model の _meta と会社ごとの項目の登録から引く。
- 行の番号は見出しを 1 行目に数える。全部の欄が空の行 (Excel が残す ,,, の行) は誤りにせず、呼ぶ側が「飛ばした」に数える。
- 書き出しは、表計算の式として動く字 (FORMULA_HEADS で始まる字) の頭に「'」を付ける (neutralize)。取り込みは、
  その「'」を外してから読む (restore)。一覧の CSV をそのまま入れ直しても値が変わらないように。
- ここは読むだけ。表に書くのは部品の services。
- 取り込みの上限は MAX_BYTES・MAX_ROWS・MAX_COLUMNS (決め 11-08 00:26)。末尾の空の欄は数える前に落とす。
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from decimal import Decimal

from django.core.exceptions import ValidationError

from seizou.core import fields as company_fields
from seizou.core import seeds

#: 文字コードの選択肢 (値, 画面の字)。utf-8-sig は BOM があれば外し、無くても読む。
ENCODINGS = (("utf-8-sig", "UTF-8 (BOM あり・なし)"), ("cp932", "Shift_JIS (Excel の既定)"))
DEFAULT_ENCODING = "utf-8-sig"
#: 読まない見出し (値があっても入れない。図面は部品の編集の画面で上げる)。
SKIP_HEADERS = ("図面ファイル",)
#: 表計算 (Excel・LibreOffice など) が、欄の頭にあると式として読む字 (OWASP の CSV injection の一覧)。
#: 「=」「+」「-」「@」と、その前に置いて式を隠せるタブと改行 (CR)。
FORMULA_HEADS = ("=", "+", "-", "@", "\t", "\r")
#: 書き出しが式の字の頭に付ける字 (表計算が字として読む印)
FORMULA_GUARD = "'"
#: 取り込みの上限。決め 11-08 00:26。ファイルの大きさ (バイト)
MAX_BYTES = 5 * 1024 * 1024
#: 取り込みの上限。決め 11-08 00:26。行の数 (見出しを除く。全部の欄が空の行も数える)
MAX_ROWS = 20_000
#: 取り込みの上限。決め 11-08 00:26。列の数 (末尾の空の欄を落とした後)
MAX_COLUMNS = 200


def _size_text(size):
    """バイトの数を断りの字にする。1MB の倍なら「5MB」、ほかは「1,234 バイト」。"""
    megabytes, rest = divmod(size, 1024 * 1024)
    return f"{megabytes}MB" if megabytes and not rest else f"{size:,} バイト"


def decode(data, encoding):
    """bytes を選んだ文字コードで読む。読めない字があれば、行の番号を添えた日本語の ValueError。

    ファイルが MAX_BYTES を超えたら、読む前に日本語の ValueError で断る (呼ぶ側の form は 1 件も入れない)。
    訳: CSV の取り込みは 5MB を上限にし、超えたら読む前に日本語の ValueError で断り、1 件も入れない。
    町工場の部品や取引先の一覧には十分なため (行と列の上限は read_table。同じ決め)。
    出所 = 本人の決め 11-08 00:26 (5MB・2 万行・200 列 (おすすめ))。
    変えてよい条件 = 本人が決め直したとき、または上限に当たる会社が出たとき。
    """
    labels = dict(ENCODINGS)
    if encoding not in labels:
        raise ValueError(
            f"文字コード「{encoding}」は選べません。{'・'.join(labels.values())} のどれかを選んでください。"
        )
    size = len(bytes(data))
    if size > MAX_BYTES:
        raise ValueError(
            f"CSV のファイルが {_size_text(MAX_BYTES)} を超えています ({size:,} バイト)。分けてから取り込み直してください。"
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
    return all(seeds.is_blank(value) for value in values)


def _quoted(labels):
    return "「" + "」「".join(labels) + "」"


def _trim(row):
    """行の後ろの空の欄 (seeds.is_blank が真の欄) を落とす。行の途中の空の欄は残す。"""
    end = len(row)
    while end and seeds.is_blank(row[end - 1]):
        end -= 1
    return row[:end]


def read_table(text, model, *, required, skip=SKIP_HEADERS):
    """CSV の字を読み、見出しを確かめる。

    返す物は (見出しの並び, {見出し: 表の列}, {見出し: 会社ごとの項目の列}, [(行の番号, {見出し: 字})])。
    見出しの誤り (必須が無い・同じ見出しが 2 つ・どれにも当たらない・見出しの無い列に値がある) は、
    全部を 1 つの日本語の ValueError にまとめる。skip の見出しは列の対応に入れない (値も読まない)。
    行 (MAX_ROWS) か列 (MAX_COLUMNS) が上限を超えたら、見出しを確かめる前に、その断りだけの ValueError にする。
    訳: CSV の取り込みは 5MB・2 万行 (見出しを除く)・200 列 (末尾の空の欄を落とした後) を上限にし、
    超えたら何行目・何列目からかを書いた日本語の ValueError で断り、1 件も入れない。
    末尾の空の欄は数える前に落とし、列の上限はその後に掛ける。数え方は Counter にする。
    町工場の部品や取引先の一覧には十分で、列の多い CSV で手間が列の数の 2 乗に増えるのを止めるため。
    出所 = 本人の決め 11-08 00:26 (5MB・2 万行・200 列 (おすすめ))。
    変えてよい条件 = 本人が決め直したとき、または上限に当たる会社が出たとき。
    """
    reader = csv.reader(io.StringIO(text, newline=""))
    records = []
    try:
        # 1 行ずつ読み、後ろの空の欄を落としてから数える。上限を超えたら、そこで読むのをやめて断る。
        for row in reader:
            line = reader.line_num
            if len(records) > MAX_ROWS:
                raise ValueError(
                    f"CSV の行が {MAX_ROWS:,} 行 (見出しを除く) を超えています ({line:,} 行目から)。"
                    "分けてから取り込み直してください。"
                )
            cells = _trim(row)
            if len(cells) > MAX_COLUMNS:
                raise ValueError(
                    f"{line:,} 行目に {MAX_COLUMNS + 1} 列目から先の値があります。列は {MAX_COLUMNS} 列までです。"
                    "列を減らしてから取り込み直してください。"
                )
            records.append((line, cells))
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
    by_label = {label: f for label, f in seeds.fields_by_label(model).items() if f.editable}
    registration = company_fields.registration_for(model)
    ext_by_label = {c.label: c for c in registration.columns} if registration else {}

    problems = []
    for index, header in enumerate(headers):
        if header:
            continue
        line = next((line for line, row in body if index < len(row) and not seeds.is_blank(row[index])), None)
        if line is not None:
            problems.append(
                f"{index + 1} 列目は見出しが空なのに、{line} 行目に値があります。見出しを書くか、その列を消してください。"
            )
    named = [h for h in headers if h]
    counts = Counter(named)
    doubled = [h for h in dict.fromkeys(named) if counts[h] > 1]
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
        rows.append((line, {h: restore(cells[i]) for i, h in enumerate(headers) if h}))
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
    """1 つの欄の字を表の列の値にする。型の変換 (seeds.coerce) の後に、列の検査 (例 締日の 1〜31) も通す。"""
    value = seeds.coerce(field, raw)
    try:
        field.run_validators(value)
    except ValidationError as exc:
        raise ValueError(f"{field.verbose_name}「{str(raw).strip()}」は使えません。{_message(exc)}") from None
    return value


def to_ext_value(column, raw):
    """1 つの欄の字を会社ごとの項目の値にする。型の変換 (seeds.coerce_ext) の後に、入力の欄と同じ検査も通す。"""
    if column.required and seeds.is_blank(raw):
        raise ValueError(f"{column.label}が空です。値を入れてください。")
    value = seeds.coerce_ext(column, raw)
    if value in (None, "") or isinstance(value, bool):
        return value
    try:
        column.form_field().clean(value)
    except ValidationError as exc:
        raise ValueError(f"{column.label}「{str(raw).strip()}」は使えません。{_message(exc)}") from None
    return value


def neutralize(text: str) -> str:
    """CSV の 1 つの欄の字が式の字 (FORMULA_HEADS) で始まれば、頭に「'」を付ける。ほかはそのまま。

    書き出しの欄の字は全部ここを通す (一覧の csv_response は to_text から、集計の CSV は core.summary の csv_rows から)。
    数の値 (Decimal・int・float) は呼ぶ側 (to_text) が通さない (負の数「-5」を字にしないため)。
    訳: 人や取引先が入れた字 (取引先の名前・部署・ロット・相手の注文番号など) が「=」などで始まると、書き出した CSV を
    表計算で開いたときに式として動き、別のサイトへのリンクや外の命令を起こせるため、字として読ませる。
    出所 = Claude Security の走査の指摘 (11-07) と OWASP の CSV injection の対策。
    変えてよい条件 = 書き出しの形を表計算で開かない形 (JSON など) に替える時か、式の字の一覧 (FORMULA_HEADS) を表計算の変化に合わせる時。
    取り込みの restore と 1 対で変える。
    """
    if text.startswith(FORMULA_HEADS):
        return FORMULA_GUARD + text
    return text


def restore(text: str) -> str:
    """neutralize の逆。「'」の後が式の字 (FORMULA_HEADS) のときだけ「'」を外す。ほかはそのまま (人が入れた「'」は残す)。

    例外: 人が「'=」のように「'」の後に式の字を続けて入れた正しい値は、書き出して取り込み直すと頭の「'」が 1 字欠ける
    (書き出しが付けた「'」と見分けられないため)。
    訳: 一覧の CSV をそのまま取り込み直したときに、書き出しが付けた「'」が値に入らないようにするため。
    出所 = Claude Security の走査の指摘 (11-07) の対策 (neutralize) に合わせた AI の案。
    変えてよい条件 = neutralize の付け方を変える時 (1 対で変える)。
    """
    if text.startswith(FORMULA_GUARD) and text[len(FORMULA_GUARD):].startswith(FORMULA_HEADS):
        return text[len(FORMULA_GUARD):]
    return text


def to_text(value):
    """表の値を、一覧の CSV の書き出しの 1 つの欄の字にする (to_value の逆の向き)。

    None は空の字、真偽は取り込みが読む字の組にある書き出しの字 (seeds.TRUE_TEXT・FALSE_TEXT。決め 11-06 22:0x)、ほかは str。
    真偽は isinstance で見る (数の 0 と 1 は数の字のまま)。
    数 (Decimal・int・float) は str のまま。ほかの字は neutralize を通す (式の字で始まる字の頭に「'」)。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return seeds.TRUE_TEXT if value else seeds.FALSE_TEXT
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    return neutralize(str(value))
