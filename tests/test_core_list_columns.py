"""一覧の土台 (ColumnSpec・BaseListView) の列の印: annotate の列を数・金額・日付の欄として扱う口。

試験の中で BaseListView を継承した画面を作り、RequestFactory で直に呼ぶ (tests/test_core_ux_fixes.py の 17 と同じ形)。
SQLite は annotate の Decimal を桁なしで返し、PostgreSQL は 2 桁で返すので、数は Decimal に直して比べる。
"""

import csv
import io
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db.models import DateField, DecimalField, ExpressionWrapper, F, IntegerField, Value
from django.db.models.functions import Length
from django.test import RequestFactory

from seizou.core import access
from seizou.core.views import BaseListView, ColumnSpec
from seizou.masters.models import Item

MONEY_ON = {"access_control": {"department": "off", "write": "off", "money": "on"}}


class _AnnotatedItemList(BaseListView):
    model = Item
    screen_name = "作り物の部品の一覧"
    columns = (
        "part_no",
        {"name": "name_len", "label": "名前の長さ", "kind": "number", "sortable": True, "total": True},
        {"name": "price_x2", "label": "倍の単価", "kind": "money", "sortable": True, "total": True},
        {"name": "moved_on", "label": "動きの日", "kind": "date", "sortable": True},
        {"name": "no_such_value", "label": "無い値", "kind": "number"},
    )
    search_fields = ("part_no",)
    template_name = "core/list.html"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .annotate(
                name_len=ExpressionWrapper(Length("name") - Value(5), output_field=IntegerField()),
                price_x2=ExpressionWrapper(
                    F("standard_price") * 2, output_field=DecimalField(max_digits=12, decimal_places=2)
                ),
                moved_on=Value(date(2026, 11, 3), output_field=DateField()),
            )
        )


class _TotalItemList(BaseListView):
    model = Item
    screen_name = "作り物の単価の一覧"
    columns = ("part_no", {"name": "standard_price", "label": "単価の合計", "total": True})
    template_name = "core/list.html"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def items(db):
    # name_len = 名前の字の数 - 5 (−4・0・5)。price_x2 = 単価 × 2 (200・41・0)
    return [
        Item.objects.create(part_no="P-1001", name="軸", standard_price=Decimal("100")),
        Item.objects.create(part_no="P-1002", name="作り物の軸", standard_price=Decimal("20.50")),
        Item.objects.create(part_no="P-1003", name="作り物の長い名前の板", standard_price=Decimal("0")),
    ]


def call(view, user, **params):
    request = RequestFactory().get("/", params)
    request.user = user
    return view.as_view()(request)


def body(response) -> str:
    # RequestFactory で直接呼ぶと TemplateResponse は未描画のまま返る。CSV は素の HttpResponse で render が無い。
    if hasattr(response, "render"):
        response.render()
    return response.content.decode()


def csv_rows(response) -> list[list[str]]:
    return list(csv.reader(io.StringIO(response.content.decode("utf-8").lstrip("﻿"))))


def column_values(response, index) -> list:
    return [row["cells"][index]["value"] for row in response.context_data["rows"]]


def _dec(value) -> Decimal:
    return Decimal(str(value).replace(",", ""))


def tfoot(text: str) -> str:
    return text[text.index("<tfoot>") : text.index("</tfoot>")]


def th_tag(text: str, label: str) -> str:
    """見出しの字の前にある th の開きの tag。"""
    head = text[: text.index(label)]
    start = head.rindex("<th ")
    return head[start : head.index(">", start) + 1]


def test_column_spec_reads_marks():
    spec = ColumnSpec(Item, "part_no")
    assert spec.sortable and not spec.numeric and spec.is_code and not spec.money and not spec.annotated
    assert spec.kind is None

    spec = ColumnSpec(Item, ("x_total", "X"))
    assert not spec.sortable and not spec.numeric and not spec.annotated

    spec = ColumnSpec(Item, {"name": "standard_price", "label": "単価の合計", "total": True})
    assert spec.numeric and spec.money and not spec.annotated and spec.total

    spec = ColumnSpec(Item, {"name": "shipped_total", "label": "出荷済み", "kind": "number", "sortable": True})
    assert spec.numeric and spec.sortable and spec.annotated and not spec.money
    assert spec.kind == "number"

    spec = ColumnSpec(Item, {"name": "board__days_left", "label": "納期まで", "kind": "number"})
    assert spec.numeric and not spec.sortable and not spec.annotated

    spec = ColumnSpec(Item, {"name": "x", "label": "X", "kind": "money"})
    assert spec.money and spec.numeric

    spec = ColumnSpec(Item, {"name": "x", "label": "X", "kind": "date"})
    assert spec.is_date and not spec.numeric

    with pytest.raises(ValueError) as error:
        ColumnSpec(Item, {"name": "x", "label": "X", "kind": "bogus"})
    assert "bogus" in str(error.value) and "number / money / date" in str(error.value)

    spec = ColumnSpec(Item, ("ext__weight", "重さ"), ext_type="number")
    assert spec.numeric and spec.kind == "number" and spec.sortable and not spec.annotated


def test_annotated_number_cell_is_right_aligned_and_red(user, items):
    response = call(_AnnotatedItemList, user)
    text = body(response)
    cells = {row["obj"].part_no: row["cells"] for row in response.context_data["rows"]}

    assert all(row_cells[1]["numeric"] for row_cells in cells.values())
    assert cells["P-1001"][1]["negative"]
    assert not cells["P-1002"][1]["negative"] and not cells["P-1003"][1]["negative"]
    for label in ("名前の長さ", "倍の単価", "無い値"):
        assert th_tag(text, label).startswith('<th class="whitespace-nowrap font-medium text-right"'), label
    assert th_tag(text, "動きの日").startswith('<th class="whitespace-nowrap font-medium"')
    assert "text-right" not in th_tag(text, "動きの日")
    assert '<td class="whitespace-nowrap text-right tabular-nums"><span class="text-error">-4</span></td>' in text
    # Python の値の代わりの列 (display_value が None) は空の字のまま (「—」にしない)
    assert all(row_cells[4]["value"] is None for row_cells in cells.values())
    assert '<td class="whitespace-nowrap text-right tabular-nums"></td>' in text


def test_annotated_sort_applies_after_part_queryset(user, items):
    response = call(_AnnotatedItemList, user, sort="name_len")
    text = body(response)
    assert column_values(response, 1) == [-4, 0, 5]
    assert response.context_data["sort"] == "name_len"
    assert 'aria-sort="ascending"' in text and "sort=-name_len" in text

    response = call(_AnnotatedItemList, user, sort="-name_len")
    text = body(response)
    assert column_values(response, 1) == [5, 0, -4]
    assert 'aria-sort="descending"' in text

    # 印の無い列 (Python の値) は並び替えない。既定の -pk の順
    response = call(_AnnotatedItemList, user, sort="no_such_value")
    body(response)
    assert response.status_code == 200
    assert response.context_data["sort"] == ""
    assert column_values(response, 0) == ["P-1003", "P-1002", "P-1001"]

    # 表の項目の並び替えは今までどおり (get_ordering の道)
    response = call(_AnnotatedItemList, user, sort="-part_no")
    body(response)
    assert column_values(response, 0) == ["P-1003", "P-1002", "P-1001"]
    response = call(_AnnotatedItemList, user, sort="part_no")
    body(response)
    assert column_values(response, 0) == ["P-1001", "P-1002", "P-1003"]


def test_annotated_total_row(user, items):
    response = call(_AnnotatedItemList, user)
    text = body(response)
    totals = response.context_data["totals"]
    assert totals is not None
    assert _dec(totals[1]["value"]) == Decimal("1") and totals[1]["numeric"]
    assert _dec(totals[2]["value"]) == Decimal("241") and totals[2]["numeric"]
    # 日付と Python の値の列は合計に足さない
    assert not totals[3]["numeric"] and not totals[4]["numeric"]
    foot = tfoot(text)
    assert "合計" in foot and ">1<" in foot and ("241<" in foot or "241.00<" in foot)

    # 絞り込みに追従する (P-1001 だけ = −4 と 200)
    response = call(_AnnotatedItemList, user, q="P-1001")
    body(response)
    totals = response.context_data["totals"]
    assert _dec(totals[1]["value"]) == Decimal("-4")
    assert _dec(totals[2]["value"]) == Decimal("200")


def test_money_kind_masks_cells_totals_and_csv(user, items, settings):
    response = call(_AnnotatedItemList, user)
    body(response)
    assert all(_dec(row["cells"][2]["value"]) >= 0 for row in response.context_data["rows"])
    assert _dec(response.context_data["totals"][2]["value"]) == Decimal("241")
    header = csv_rows(call(_AnnotatedItemList, user, format="csv"))[0]
    assert "倍の単価" in header

    settings.SEIZOU = MONEY_ON
    response = call(_AnnotatedItemList, user)
    text = body(response)
    rows = response.context_data["rows"]
    assert all(row["cells"][2]["value"] == access.MASK for row in rows)
    assert all(not row["cells"][2]["negative"] for row in rows)
    totals = response.context_data["totals"]
    assert totals[2]["value"] == access.MASK
    assert ">—<" in tfoot(text)
    # number の列は隠れない
    assert _dec(totals[1]["value"]) == Decimal("1")
    assert sorted(row["cells"][1]["value"] for row in rows) == [-4, 0, 5]

    header = csv_rows(call(_AnnotatedItemList, user, format="csv"))[0]
    assert "倍の単価" not in header
    assert header == ["自社品番", "名前の長さ", "動きの日", "無い値"]


def test_date_kind_renders_iso(user, items):
    response = call(_AnnotatedItemList, user)
    text = body(response)
    cell = response.context_data["rows"][0]["cells"][3]
    assert cell["date"] and not cell["numeric"]
    assert '<td class="whitespace-nowrap font-mono tabular-nums">2026-11-03</td>' in text

    rows = csv_rows(call(_AnnotatedItemList, user, format="csv"))
    assert rows[0][3] == "動きの日"
    assert all(row[3] == "2026-11-03" for row in rows[1:])


def test_csv_follows_annotated_sort(user, items):
    screen = call(_AnnotatedItemList, user, sort="-name_len")
    body(screen)
    rows = csv_rows(call(_AnnotatedItemList, user, format="csv", sort="-name_len"))
    assert [row[0] for row in rows[1:]] == column_values(screen, 0) == ["P-1003", "P-1002", "P-1001"]

    rows = csv_rows(call(_AnnotatedItemList, user, format="csv", sort="name_len"))
    assert [row[0] for row in rows[1:]] == ["P-1001", "P-1002", "P-1003"]


def test_existing_column_forms_unchanged(user, items):
    response = call(_TotalItemList, user)
    text = body(response)
    spec = response.context_data["columns"][1]
    assert spec.kind is None and not spec.annotated and spec.money
    foot = tfoot(text)
    assert "合計" in foot and ("120.5<" in foot or "120.50<" in foot)

    response = call(_TotalItemList, user, sort="standard_price")
    body(response)
    assert response.context_data["sort"] == "standard_price"
    assert column_values(response, 0) == ["P-1003", "P-1002", "P-1001"]
    response = call(_TotalItemList, user, sort="-standard_price")
    body(response)
    assert column_values(response, 0) == ["P-1001", "P-1002", "P-1003"]
