"""読み取りの一致率 (N83)。注文書の読み取り (Extraction) の明細が、そのまま受注に残ったかを数える。読むだけで、表に書かない。

約束:
- 対象は呼ぶ側が絞る (状態が「受注にした」で受注のある読み取りの行。core の dashboard.reading_match)。
  取消の受注も数える (読み取りの正しさは取消と関係ない)。
- 読んだ値 = extracted["lines"] の i 番目 (0 から)。保存した値 = その受注の明細のうち行番号 i + 1 の物
  (accept_extracted は明細を extracted["lines"] の順に行番号 1 から作る)。保存した明細が無い (人が消した) 行は「直した」。
  人が足した明細 (行番号が読んだ行の数より大きい物) は数えない (n は読んだ行の数)。
- 4 つの欄が全部同じなら「そのまま」。品番 = 読んだ part_no と保存した customer_part_no を _key (NFKC にしてから
  空白を全部除いた字。大文字と小文字はそろえない) で比べる。数量 = Decimal(読んだ qty) と保存した qty。
  単価 = 読んだ unit_price が None (注文書に単価が無く、部品の標準の単価が入った) なら比べない (同じとみなす)。
  字があれば Decimal(読んだ) と保存した unit_price。納期 = 読んだ due_date (YYYY-MM-DD か None) を日付にした物と
  保存した due_date (None どうしは同じ)。
- 読んだ値の形が違う (Decimal に直せない・日付でない・明細が辞書でない) 行は「直した」に数える (例外を投げない)。
- intake の services・forms・views を import しない。受注の明細の model は apps.get_model で名前から引く。
"""

from __future__ import annotations

import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from django.apps import apps

_EMPTY = {"extractions": 0, "lines": 0, "same": 0, "fixed": 0}


def _key(text) -> str:
    """品番を比べる字。NFKC にしてから空白を全部除く (全角の英数字と記号と空白をそろえる)。大文字と小文字はそろえない。"""
    return "".join(unicodedata.normalize("NFKC", str(text)).split())


def _decimal(value):
    """数の字 (か整数) を Decimal にする。直せない物と有限でない物は None。"""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _same_line(read, saved) -> bool:
    """読んだ明細 read (extracted["lines"] の 1 行) が、保存した明細 saved のまま残ったか。

    saved = (行番号, 得意先の品番, 数量, 単価, 納期) の 5 つの値の組か None (人が消した)。形の違う read は偽。
    """
    if saved is None or not isinstance(read, dict):
        return False
    _line_no, part_no, qty, unit_price, due_date = saved
    read_part_no = read.get("part_no")
    if not isinstance(read_part_no, str) or _key(read_part_no) != _key(part_no):
        return False
    read_qty = _decimal(read.get("qty"))
    if read_qty is None or read_qty != qty:
        return False
    if read.get("unit_price") is not None:
        read_price = _decimal(read.get("unit_price"))
        if read_price is None or read_price != unit_price:
            return False
    read_due = read.get("due_date")
    if read_due is None:
        return due_date is None
    if not isinstance(read_due, str):
        return False
    try:
        return date.fromisoformat(read_due) == due_date
    except ValueError:
        return False


def match_counts(extractions) -> dict:
    """読み取りの行の queryset (状態と受注の絞りは呼ぶ側) の、読んだ明細の数と、そのまま受注に残った数と、人が直した数。返す物 = {"extractions": 枚数, "lines": n, "same": m, "fixed": k} (k = n − m)。問い合わせは 2 回まで (読み取りの行の受注の番号と中身・その受注の明細の 6 つの値)。読むだけ。"""
    try:
        line_model = apps.get_model("orders", "OrderLine")
    except LookupError:
        return dict(_EMPTY)
    rows = list(extractions.values_list("order_id", "extracted"))
    order_ids = {order_id for order_id, _extracted in rows if order_id is not None}
    saved = {}
    if order_ids:
        values = line_model._default_manager.filter(order_id__in=order_ids).values_list(
            "order_id", "line_no", "customer_part_no", "qty", "unit_price", "due_date"
        )
        for order_id, *line in values:
            saved[(order_id, line[0])] = tuple(line)
    lines = 0
    same = 0
    for order_id, extracted in rows:
        read_lines = extracted.get("lines") if isinstance(extracted, dict) else None
        if not isinstance(read_lines, list):
            continue
        for index, read in enumerate(read_lines):
            lines += 1
            if _same_line(read, saved.get((order_id, index + 1))):
                same += 1
    return {"extractions": len(rows), "lines": lines, "same": same, "fixed": lines - same}
