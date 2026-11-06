"""一覧の CSV の書き出しの真偽の字 (初見テスト R5 の候補 C94。決め 11-06 22:0x)。

真偽は「はい」「いいえ」で書き出し、取り込みが読む字の組 (seizou.core.seeds) と同じ言葉にそろえる。
(a) csvio.to_text の字・(b) 書き出しと取り込みの言葉・(c) 工程の一覧の CSV・(d) 会社ごとの真偽の項目の往復。
試験の DB に拡張の表 (fields.ItemExt) は無いので、会社ごとの項目は ext_value と save_ext を置き換える。
"""

import csv
import io
from datetime import date
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models
from django.forms.models import model_to_dict
from django.urls import reverse

from seizou.core import csvio, seeds
from seizou.core import fields as company_fields
from seizou.masters import services
from seizou.masters.forms import ItemCsvImportForm
from seizou.masters.models import Item, Process


@pytest.fixture
def no_registration(monkeypatch):
    """会社ごとの項目の登録を空から始める (ほかの試験の登録を残さない)。"""
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


@pytest.fixture
def captured_ext(monkeypatch):
    """services が拡張の表に書く代わりに、渡った (部品, ext) を並べる。"""
    calls = []

    def capture(obj, ext):
        if ext:
            calls.append((obj.pk, ext))

    monkeypatch.setattr(company_fields, "save_ext", capture)
    return calls


def make_form(form_class, content, encoding="utf-8-sig"):
    upload = SimpleUploadedFile("import.csv", content, content_type="text/csv")
    return form_class(data={"encoding": encoding}, files={"file": upload})


def import_valid(form_class, content, user, encoding="utf-8-sig"):
    form = make_form(form_class, content, encoding)
    assert form.is_valid(), form.import_errors or form.errors
    service = services.import_items if form_class is ItemCsvImportForm else services.import_partners
    return form, service(**form.service_kwargs(), user=user)


def snapshot(model, key):
    return {getattr(obj, key): model_to_dict(obj) for obj in model.objects.all()}


def read_csv(content):
    """書き出した CSV の bytes を、見出しの並びと {鍵の列の字: 行} にする。鍵は 1 列目。"""
    text = content.decode("utf-8-sig")
    header, *rows = list(csv.reader(io.StringIO(text, newline="")))
    return text, header, {row[0]: row for row in rows}


# ---------------------------------------------------------------- (a) 1 つの欄の字


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "はい"),
        (False, "いいえ"),
        (None, ""),
        (Decimal("480.00"), "480.00"),
        (0, "0"),
        (1, "1"),
        (date(2026, 11, 6), "2026-11-06"),
        ("S45C", "S45C"),
    ],
)
def test_to_text_writes_bool_in_japanese_and_others_as_before(value, expected):
    assert csvio.to_text(value) == expected


# ---------------------------------------------------------------- (b) 書き出しと取り込みの言葉


def test_export_words_are_in_import_words():
    assert seeds.TRUE_TEXT == "はい"
    assert seeds.FALSE_TEXT == "いいえ"
    assert seeds.TRUE_TEXT in seeds.TRUE_WORDS
    assert seeds.FALSE_TEXT in seeds.FALSE_WORDS


def test_import_error_uses_same_words_as_export():
    with pytest.raises(ValueError) as error:
        seeds.coerce(models.BooleanField("作り物の印"), "○")

    assert str(error.value) == "作り物の印「○」は はい か いいえ にしてください。"


@pytest.mark.parametrize("value", [True, False])
def test_exported_bool_reads_back_to_same_value(value):
    field = Process._meta.get_field("use_in_plan")

    assert csvio.to_value(field, csvio.to_text(value)) is value


# ---------------------------------------------------------------- (c) 工程の一覧の CSV


@pytest.mark.django_db
def test_process_list_csv_writes_use_in_plan_in_japanese(client_logged_in):
    Process.objects.create(code="TURN", name="作り物の旋削", sort_order=10, use_in_plan=True)
    Process.objects.create(code="INSP", name="作り物の検査", sort_order=20)

    response = client_logged_in.get(reverse("masters:process_list"), {"format": "csv"})

    assert response.status_code == 200
    text, header, rows = read_csv(response.content)
    assert header[0] == Process._meta.get_field("code").verbose_name
    column = header.index("計画に使う")
    assert rows["TURN"][column] == "はい"
    assert rows["INSP"][column] == "いいえ"
    assert "True" not in text and "False" not in text


# ---------------------------------------------------------------- (d) 会社ごとの真偽の項目の往復


@pytest.mark.django_db
def test_company_bool_field_roundtrips_through_item_list_csv(
    client_logged_in, user, no_registration, captured_ext, monkeypatch
):
    company_fields.register("masters.Item", "fields.ItemExt", [{"label": "作り物の印", "type": "bool"}])
    monkeypatch.setattr(company_fields, "ext_value", lambda obj, name: obj.part_no == "P-1001")
    first = services.create_item({"part_no": "P-1001", "name": "作り物の軸"}, user)
    second = services.create_item({"part_no": "P-1002", "name": "作り物の板"}, user)
    before = snapshot(Item, "part_no")

    exported = client_logged_in.get(reverse("masters:item_list"), {"format": "csv"}).content
    text, header, rows = read_csv(exported)
    assert header[0] == Item._meta.get_field("part_no").verbose_name
    column = header.index("作り物の印")
    assert rows["P-1001"][column] == "はい"
    assert rows["P-1002"][column] == "いいえ"
    assert "True" not in text and "False" not in text

    form, result = import_valid(ItemCsvImportForm, exported, user)

    assert [row["ext"] for row in form.cleaned_data["rows"]] == [{"ext01": True}, {"ext01": False}]
    assert result == {"入れた": 0, "更新した": 2}
    assert captured_ext == [(first.pk, {"ext01": True}), (second.pk, {"ext01": False})]
    assert snapshot(Item, "part_no") == before
