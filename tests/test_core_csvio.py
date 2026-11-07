"""CSV の取り込みの上限 (5MB・2 万行・200 列) と、末尾の空の欄と、同じ見出しの数え方 (seizou.core.csvio)。

意図: 本人の決め 11-08 00:26 (5MB・2 万行・200 列)。超えたら何行目・何列目からかを書いた日本語の ValueError で断り、
取り込みの form は 1 件も入れない。末尾の空の欄 (Excel が残す ,,, の列) は数える前に落とし、列の上限はその後に掛ける。
品番と名前は作り物。行と大きさの上限は、定数を monkeypatch で小さくして見る。
大きな字を作るのは、5MB の字の断りと、末尾の空の欄の多い CSV (約 24MB の字を read_table に直に渡す) の 2 本だけ。
時間は測らない (末尾の空の欄の多い CSV も、ValueError にならずに行を返すことだけを見る)。
"""

from __future__ import annotations

import pytest

from seizou.core import csvio
from seizou.core import fields as company_fields
from seizou.masters.forms import ItemCsvImportForm
from seizou.masters.models import Item
from tests.test_masters_csv_import import csv_bytes, make_form

REQUIRED = ("自社品番", "品名")


@pytest.fixture
def empty_registration(monkeypatch):
    """会社ごとの項目の登録を空から始める (ほかの試験の登録を残さない)。"""
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


def read(*lines):
    return csvio.read_table("\r\n".join(lines) + "\r\n", Item, required=REQUIRED)


def error_of(*lines):
    with pytest.raises(ValueError) as caught:
        read(*lines)
    return str(caught.value)


# ---------------------------------------------------------------- 上限の定数


def test_limits_are_the_decided_values():
    assert csvio.MAX_BYTES == 5 * 1024 * 1024
    assert csvio.MAX_ROWS == 20_000
    assert csvio.MAX_COLUMNS == 200


# ---------------------------------------------------------------- 大きさ (decode)


def test_decode_refuses_bytes_over_the_limit(monkeypatch):
    monkeypatch.setattr(csvio, "MAX_BYTES", 100)

    with pytest.raises(ValueError) as caught:
        csvio.decode(b"a" * 101, "utf-8-sig")

    message = str(caught.value)
    assert "100 バイト" in message
    assert "101 バイト" in message
    assert "分けて" in message
    assert csvio.decode(b"a" * 100, "utf-8-sig") == "a" * 100


def test_decode_names_the_limit_in_megabytes():
    with pytest.raises(ValueError) as caught:
        csvio.decode(b"a" * (5 * 1024 * 1024 + 1), "cp932")

    assert "CSV のファイルが 5MB を超えています" in str(caught.value)


@pytest.mark.django_db
def test_form_refuses_file_over_the_limit(monkeypatch, empty_registration):
    content = csv_bytes("自社品番,品名", "P-1001,作り物の軸", "P-1002,作り物の板", "P-1003,作り物の座金")
    monkeypatch.setattr(csvio, "MAX_BYTES", len(content) - 1)
    form = make_form(ItemCsvImportForm, content)

    assert not form.is_valid()
    assert len(form.import_errors) == 1
    assert "分けて" in form.import_errors[0][2]
    assert Item.objects.count() == 0


# ---------------------------------------------------------------- 列 (read_table)


def test_header_over_column_limit_names_line_and_column(empty_registration):
    header = ",".join(["自社品番", "品名"] + [f"作り物の見出し{n}" for n in range(199)])

    message = error_of(header, "P-1001,作り物の軸")

    assert "1 行目" in message
    assert "201 列目" in message
    assert "列は 200 列までです" in message


def test_header_at_column_limit_is_not_refused_by_the_limit(empty_registration):
    header = ",".join(["自社品番", "品名"] + [f"作り物の見出し{n}" for n in range(198)])

    message = error_of(header, "P-1001,作り物の軸")

    assert "201 列目" not in message
    assert "列は 200 列までです" not in message
    assert "作り物の見出し0" in message


def test_row_over_column_limit_names_line_and_column(empty_registration):
    row = ",".join(["P-1001", "作り物の軸"] + [""] * 198 + ["余り"])

    message = error_of("自社品番,品名", row, "P-1002,作り物の板")

    assert "2 行目" in message
    assert "201 列目" in message
    assert "見出しが空" not in message


def test_trailing_blank_cells_are_dropped_before_counting(empty_registration):
    blanks = "," * 16_000
    lines = ["自社品番,品名" + blanks] + [f"P-{1000 + n},作り物の部品{blanks}" for n in range(1_500)]

    headers, columns, _ext_columns, rows = read(*lines)

    assert headers == ["自社品番", "品名"]
    assert set(columns) == {"自社品番", "品名"}
    assert len(rows) == 1_500
    assert rows[0] == (2, {"自社品番": "P-1000", "品名": "作り物の部品"})


def test_blank_cell_in_the_middle_is_kept(empty_registration):
    _headers, _columns, _ext_columns, rows = read("自社品番,図面番号,品名, ,", "P-1001,,作り物の軸,,")

    assert rows == [(2, {"自社品番": "P-1001", "図面番号": "", "品名": "作り物の軸"})]


# ---------------------------------------------------------------- 行 (read_table)


def test_rows_over_the_limit_name_the_line(monkeypatch, empty_registration):
    monkeypatch.setattr(csvio, "MAX_ROWS", 3)

    message = error_of("自社品番,品名", "P-1001,作り物の軸", "P-1002,作り物の板", "P-1003,作り物の座金", "P-1004,作り物の筒")

    assert "5 行目" in message
    assert "3 行 (見出しを除く)" in message
    assert "分けて" in message


def test_rows_at_the_limit_are_read(monkeypatch, empty_registration):
    monkeypatch.setattr(csvio, "MAX_ROWS", 3)

    _headers, _columns, _ext_columns, rows = read("自社品番,品名", "P-1001,作り物の軸", "P-1002,作り物の板", "P-1003,作り物の座金")

    assert [line for line, _cells in rows] == [2, 3, 4]


@pytest.mark.django_db
def test_form_refuses_rows_over_the_limit(monkeypatch, empty_registration):
    monkeypatch.setattr(csvio, "MAX_ROWS", 3)
    content = csv_bytes("自社品番,品名", "P-1001,作り物の軸", "P-1002,作り物の板", "P-1003,作り物の座金", "P-1004,作り物の筒")
    form = make_form(ItemCsvImportForm, content)

    assert not form.is_valid()
    assert len(form.import_errors) == 1
    assert "5 行目" in form.import_errors[0][2]
    assert Item.objects.count() == 0


# ---------------------------------------------------------------- 見出しの誤り (今の字と並びのまま)


def test_doubled_headers_keep_first_seen_order(empty_registration):
    message = error_of("自社品番,品名,品名,自社品番", "P-1001,作り物の軸,作り物の板,P-1002")

    assert "見出し「自社品番」「品名」が 2 つあります。1 つにしてください。" in message


def test_headerless_column_in_the_middle_is_still_an_error(empty_registration):
    message = error_of("自社品番,品名,", "P-1001,作り物の軸,", "P-1002,作り物の板,余り")

    assert "3 列目は見出しが空なのに、3 行目に値があります。" in message
