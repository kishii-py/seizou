"""部品と取引先の CSV 取り込み (05 の 8 節)。

1 段目: 読み方 (seizou.core.csvio)・取り込みの form (行ごとの確かめ)・services.import_items / import_partners。
form を SimpleUploadedFile で作って is_valid() を見て、services.import_*(**form.service_kwargs(), user=user) で入れる。
試験の DB に拡張の表 (fields.ItemExt) は無いので、会社ごとの項目は save_ext を捕まえて、渡った辞書を見る。
"""

from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms.models import model_to_dict
from django.urls import reverse

from seizou.core import csvio
from seizou.core import fields as company_fields
from seizou.masters import services
from seizou.masters.forms import ItemCsvImportForm, PartnerCsvImportForm, read_payment_day
from seizou.masters.models import Item, Partner

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "companies"
ITEM_FIELDS = {
    "a_machining": [{"label": "材質"}, {"label": "図面の版"}],
    "b_plating": [{"label": "処理の種類"}, {"label": "膜厚 (μm)", "type": "number"}],
}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


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


def csv_bytes(*lines, encoding="utf-8"):
    return ("\r\n".join(lines) + "\r\n").encode(encoding)


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


# ---------------------------------------------------------------- 往復 (一覧の CSV をそのまま入れる)


@pytest.mark.django_db
def test_items_roundtrip_with_list_export(client_logged_in, user, no_registration):
    services.create_item(
        {"part_no": "P-1001", "name": "作り物の軸", "unit": "個", "standard_price": Decimal("480.00"), "note": "書き出しに無い列"},
        user,
    )
    services.create_item({"part_no": "P-1002", "name": "作り物の板", "drawing_no": "DWG-1002", "standard_price": Decimal("12.50")}, user)
    services.create_item({"part_no": "P-1003", "name": "作り物の座金", "unit": "枚"}, user)
    before = snapshot(Item, "part_no")

    exported = client_logged_in.get(reverse("masters:item_list"), {"format": "csv"}).content
    form, result = import_valid(ItemCsvImportForm, exported, user)

    assert result == {"入れた": 0, "更新した": 3}
    assert form.import_counts == {"入れる": 0, "更新する": 3, "飛ばした": 0}
    assert snapshot(Item, "part_no") == before


@pytest.mark.django_db
def test_partners_roundtrip_with_list_export(client_logged_in, user, no_registration):
    rows = [
        {"code": "C001", "name": "作り物の得意先", "role": "customer", "closing_day": 31, "payment_month": "next", "payment_day": 31, "rounding": "ceil"},
        {"code": "C002", "name": "作り物の商会", "role": "both", "closing_day": 20, "payment_month": "next", "payment_day": 25, "tax_mode": "line_floor"},
        {"code": "C003", "name": "作り物の工業", "role": "customer", "payment_month": "current"},
        {"code": "S001", "name": "作り物の協力企業", "short_name": "作り外", "role": "subcontractor", "note": "書き出しに無い列"},
        {"code": "S002", "name": "作り物の塗装", "role": "subcontractor", "closing_day": 25, "payment_month": "after_next", "payment_day": 10},
        {"code": "S003", "name": "作り物の熱処理", "role": "subcontractor", "closing_day": 5, "payment_day": 31},
    ]
    for data in rows:
        services.create_partner(data, user)
    before = snapshot(Partner, "code")

    exported = client_logged_in.get(reverse("masters:partner_list"), {"format": "csv"}).content
    text = exported.decode("utf-8-sig")
    assert "月末" in text and "翌月 25 日" in text and "翌月末" in text and "当月" in text and "翌々月 10 日" in text

    _form, result = import_valid(PartnerCsvImportForm, exported, user)

    assert result == {"入れた": 0, "更新した": 6}
    assert snapshot(Partner, "code") == before


# ---------------------------------------------------------------- 更新と新規


@pytest.mark.django_db
def test_update_and_create_by_key(user, no_registration):
    services.create_item({"part_no": "P-1001", "name": "前の品名", "note": "残る備考"}, user)
    content = csv_bytes("自社品番,品名,単位", "P-1001,新しい品名,個", "P-2002,作り物の新しい部品,枚")

    form, result = import_valid(ItemCsvImportForm, content, user)

    assert form.import_counts == {"入れる": 1, "更新する": 1, "飛ばした": 0}
    assert result == {"入れた": 1, "更新した": 1}
    assert Item.objects.count() == 2
    updated = Item.objects.get(part_no="P-1001")
    assert (updated.name, updated.unit, updated.note) == ("新しい品名", "個", "残る備考")
    assert Item.objects.get(part_no="P-2002").name == "作り物の新しい部品"


# ---------------------------------------------------------------- 誤り (1 行でもあれば 1 件も入れない)


@pytest.mark.django_db
def test_error_rows_import_nothing(user, no_registration):
    content = csv_bytes(
        "自社品番,品名,単位,標準単価 (円)",
        "P-1001,作り物の軸,個,480",
        "P-1002,作り物の板,枚,abc",
        "P-1003,作り物の座金,個,12",
        "P-1004,作り物の棒,12345678901,30",
    )
    form = make_form(ItemCsvImportForm, content)

    assert not form.is_valid()
    places = [(line, field) for line, field, _reason in form.import_errors]
    assert places == [(3, "標準単価 (円)"), (5, "単位")]
    assert "2 件" in " ".join(form.non_field_errors()) and "1 件も入れていません" in " ".join(form.non_field_errors())
    assert Item.objects.count() == 0


@pytest.mark.django_db
def test_service_refusal_rolls_back_all_rows(user):
    rows = [
        {"line": 2, "data": {"code": "C001", "name": "作り物の得意先", "role": "customer"}, "ext": None},
        {"line": 3, "data": {"code": "C002", "name": "作り物の協力企業", "role": "subcontractor", "payment_day": 32}, "ext": None},
    ]

    with pytest.raises(ValueError, match="3 行目"):
        services.import_partners(rows, user)

    assert Partner.objects.count() == 0


@pytest.mark.django_db
def test_day_out_of_range_is_form_error(no_registration):
    content = csv_bytes("取引先コード,名前,取引先の種類,締日,支払日", "C001,作り物の得意先,得意先,32,0")
    form = make_form(PartnerCsvImportForm, content)

    assert not form.is_valid()
    assert [(line, field) for line, field, _reason in form.import_errors] == [(2, "締日"), (2, "支払日")]
    assert Partner.objects.count() == 0


@pytest.mark.django_db
def test_duplicate_key_in_file_is_error(no_registration):
    content = csv_bytes("自社品番,品名", "P-1001,作り物の軸", "P-1002,作り物の板", "P-1001,同じ品番の行")
    form = make_form(ItemCsvImportForm, content)

    assert not form.is_valid()
    assert len(form.import_errors) == 1
    line, field, reason = form.import_errors[0]
    assert (line, field) == (4, "自社品番")
    assert "2 行目にもあります" in reason
    assert Item.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("header", "word"),
    [
        ("品名,単位", "自社品番"),
        ("自社品番,品名,品名", "品名"),
        ("自社品番,品名,色", "色"),
        ("自社品番,品名,標準単価,標準単価 (円)", "標準単価"),
    ],
)
def test_header_errors(header, word, no_registration):
    width = header.count(",") + 1
    content = csv_bytes(header, ",".join(["P-1001"] + ["作り物"] * (width - 1)))
    form = make_form(ItemCsvImportForm, content)

    assert not form.is_valid()
    assert len(form.import_errors) == 1
    line, _field, reason = form.import_errors[0]
    assert line is None
    assert f"「{word}」" in reason
    assert Item.objects.count() == 0


@pytest.mark.django_db
def test_headerless_column_with_value_is_error(no_registration):
    content = csv_bytes("自社品番,品名,", "P-1001,作り物の軸,", "P-1002,作り物の板,余り")
    form = make_form(ItemCsvImportForm, content)

    assert not form.is_valid()
    assert form.import_errors[0][0] is None
    assert "3 列目" in form.import_errors[0][2] and "3 行目" in form.import_errors[0][2]


# ---------------------------------------------------------------- 文字コード


@pytest.mark.django_db
def test_utf8_with_and_without_bom(user, no_registration):
    with_bom = "﻿自社品番,品名\r\nP-1001,作り物の軸\r\n".encode()
    without_bom = "自社品番,品名\r\nP-1002,作り物の板\r\n".encode()

    import_valid(ItemCsvImportForm, with_bom, user)
    import_valid(ItemCsvImportForm, without_bom, user)

    assert sorted(Item.objects.values_list("part_no", "name")) == [("P-1001", "作り物の軸"), ("P-1002", "作り物の板")]


@pytest.mark.django_db
def test_cp932(user, no_registration):
    content = csv_bytes("自社品番,品名,単位", "P-1001,作り物の段付き軸,個", encoding="cp932")

    wrong = make_form(ItemCsvImportForm, content, "utf-8-sig")
    assert not wrong.is_valid()
    assert wrong.import_errors[0][0] is None
    assert "文字コードを選び直して" in wrong.import_errors[0][2]
    assert Item.objects.count() == 0

    import_valid(ItemCsvImportForm, content, user, "cp932")
    assert Item.objects.get().name == "作り物の段付き軸"


def test_decode_refuses_unknown_encoding():
    with pytest.raises(ValueError, match="選べません"):
        csvio.decode(b"a", "latin-1")


@pytest.mark.django_db
def test_blank_rows_are_skipped(user, no_registration):
    content = csv_bytes("自社品番,品名,単位,図面番号", "P-1001,作り物の軸,個,", ",,,", "P-1002,作り物の板,枚,")

    form, result = import_valid(ItemCsvImportForm, content, user)

    assert form.import_counts == {"入れる": 2, "更新する": 0, "飛ばした": 1}
    assert result == {"入れた": 2, "更新した": 0}
    assert Item.objects.count() == 2


# ---------------------------------------------------------------- 締日と支払日の字


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", {"payment_month": "", "payment_day": None}),
        ("翌月 25 日", {"payment_month": "next", "payment_day": 25}),
        ("翌月25日", {"payment_month": "next", "payment_day": 25}),
        ("翌月末", {"payment_month": "next", "payment_day": 31}),
        ("当月末", {"payment_month": "current", "payment_day": 31}),
        ("翌々月 10 日", {"payment_month": "after_next", "payment_day": 10}),
        ("25 日", {"payment_month": "", "payment_day": 25}),
        ("25", {"payment_month": "", "payment_day": 25}),
        ("月末", {"payment_month": "", "payment_day": 31}),
        ("翌月", {"payment_month": "next", "payment_day": None}),
    ],
)
def test_payment_day_words_without_month_column(text, expected):
    field = Partner._meta.get_field("payment_day")
    assert read_payment_day(field, text, {"code", "payment_day"}) == expected


@pytest.mark.parametrize("text", ["来月 5 日", "翌月 末日", "末"])
def test_payment_day_words_refused(text):
    field = Partner._meta.get_field("payment_day")
    with pytest.raises(ValueError, match="形が違います"):
        read_payment_day(field, text, {"code", "payment_day"})


def test_payment_day_with_month_column_reads_day_only():
    field = Partner._meta.get_field("payment_day")
    present = {"code", "payment_month", "payment_day"}
    assert read_payment_day(field, "月末", present) == {"payment_day": 31}
    assert read_payment_day(field, "25 日", present) == {"payment_day": 25}
    assert read_payment_day(field, "", present) == {"payment_day": None}
    with pytest.raises(ValueError, match="月の字があります"):
        read_payment_day(field, "翌月 25 日", present)


@pytest.mark.django_db
def test_closing_day_words(user, no_registration):
    content = csv_bytes(
        "取引先コード,名前,取引先の種類,締日",
        "C001,作り物の一,得意先,20",
        "C002,作り物の二,得意先,20 日",
        "C003,作り物の三,得意先,20日",
        "C004,作り物の四,得意先,月末",
        "C005,作り物の五,得意先,",
    )
    import_valid(PartnerCsvImportForm, content, user)

    assert dict(Partner.objects.values_list("code", "closing_day")) == {
        "C001": 20, "C002": 20, "C003": 20, "C004": 31, "C005": None,
    }


# ---------------------------------------------------------------- 会社ごとの項目と examples


@pytest.mark.django_db
def test_company_fields_columns(user, no_registration, captured_ext):
    company_fields.register("masters.Item", "fields.ItemExt", [{"label": "材質"}, {"label": "膜厚", "type": "number"}])
    content = csv_bytes("自社品番,品名,材質,膜厚", "P-1001,作り物の軸,S45C,8")

    import_valid(ItemCsvImportForm, content, user)

    assert captured_ext == [(Item.objects.get().pk, {"ext01": "S45C", "ext02": Decimal("8")})]


@pytest.mark.django_db
def test_company_fields_required_and_type_errors(no_registration):
    company_fields.register(
        "masters.Item", "fields.ItemExt", [{"label": "材質", "required": True}, {"label": "膜厚", "type": "number"}]
    )
    content = csv_bytes("自社品番,品名,材質,膜厚", "P-1001,作り物の軸,,厚い")
    form = make_form(ItemCsvImportForm, content)

    assert not form.is_valid()
    assert [(line, field) for line, field, _reason in form.import_errors] == [(2, "材質"), (2, "膜厚")]


@pytest.mark.django_db
@pytest.mark.parametrize("company", sorted(ITEM_FIELDS))
def test_examples_csv_import(company, user, no_registration, captured_ext):
    company_fields.register("masters.Item", "fields.ItemExt", ITEM_FIELDS[company])
    items = (EXAMPLES / company / "items.csv").read_bytes()
    partners = (EXAMPLES / company / "partners.csv").read_bytes()

    _form, made_items = import_valid(ItemCsvImportForm, items, user)
    _form, made_partners = import_valid(PartnerCsvImportForm, partners, user)

    assert made_items == {"入れた": 30, "更新した": 0}
    assert made_partners == {"入れた": 5, "更新した": 0}
    assert Item.objects.count() == 30 and Partner.objects.count() == 5
    assert len(captured_ext) == 30
    assert Partner.objects.exclude(rounding="").exists() and Partner.objects.exclude(tax_mode="").exists()

    _form, again = import_valid(ItemCsvImportForm, items, user)
    assert again == {"入れた": 0, "更新した": 30}
    _form, again = import_valid(PartnerCsvImportForm, partners, user)
    assert again == {"入れた": 0, "更新した": 5}


# ---------------------------------------------------------------- 画面 (2 段目)

SCREEN_URLS = ("masters:item_import", "masters:partner_import", "masters:item_import_sample", "masters:partner_import_sample")


def post_csv(client, url_name, content, encoding="utf-8-sig"):
    upload = SimpleUploadedFile("import.csv", content, content_type="text/csv")
    return client.post(reverse(url_name), {"encoding": encoding, "file": upload})


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", SCREEN_URLS)
def test_import_screens_require_login(client, url_name):
    response = client.get(reverse(url_name))

    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("list_name", "import_name"),
    [("masters:item_list", "masters:item_import"), ("masters:partner_list", "masters:partner_import")],
)
def test_lists_have_import_button(client_logged_in, list_name, import_name):
    body = client_logged_in.get(reverse(list_name)).content.decode()

    assert "CSV 取り込み" in body
    assert f'href="{reverse(import_name)}"' in body
    assert "新規登録" in body
    assert body.count("btn-primary") == 1


@pytest.mark.django_db
def test_import_screen_shows_file_and_encoding_only(client_logged_in, no_registration):
    company_fields.register("masters.Item", "fields.ItemExt", [{"label": "材質"}])

    response = client_logged_in.get(reverse("masters:item_import"))

    body = response.content.decode()
    assert response.status_code == 200
    assert "部品マスターの CSV 取り込み" in body and 'enctype="multipart/form-data"' in body
    assert 'name="file"' in body and 'name="encoding"' in body and "Shift_JIS" in body
    assert "ext__ext01" not in body  # 会社ごとの項目の欄は出ない (model を書かない)
    assert f'href="{reverse("masters:item_list")}"' in body and "一覧に戻る" in body
    assert f'href="{reverse("masters:item_import_sample")}"' in body
    assert "自社品番・品名・図面番号・単位・標準単価 (円)・備考・材質" in body
    assert body.count("btn-primary") == 1


@pytest.mark.django_db
def test_item_import_post_saves_and_redirects(client_logged_in, no_registration):
    content = csv_bytes("自社品番,品名,単位", "P-1001,作り物の軸,個", "P-1002,作り物の板,枚")

    response = post_csv(client_logged_in, "masters:item_import", content)

    assert response.status_code == 302
    assert response["Location"] == "/"
    assert sorted(Item.objects.values_list("part_no", flat=True)) == ["P-1001", "P-1002"]


@pytest.mark.django_db
def test_item_import_post_errors_show_table(client_logged_in, no_registration):
    content = csv_bytes("自社品番,品名,標準単価 (円)", "P-1001,作り物の軸,480", "P-1002,作り物の板,abc")

    response = post_csv(client_logged_in, "masters:item_import", content)

    body = response.content.decode()
    assert response.status_code == 200
    assert 'data-notice="stop"' in body and "3 行目" in body and "標準単価 (円)" in body
    assert "1 件も入れていません" in body
    assert Item.objects.count() == 0


@pytest.mark.django_db
def test_item_import_post_header_error_shows_header_row(client_logged_in, no_registration):
    response = post_csv(client_logged_in, "masters:item_import", csv_bytes("品名", "作り物の軸"))

    body = response.content.decode()
    assert response.status_code == 200
    assert "<td class=\"font-mono whitespace-nowrap\">見出し</td>" in body and "「自社品番」" in body
    assert Item.objects.count() == 0


@pytest.mark.django_db
def test_partner_import_post_reads_display_days(client_logged_in, no_registration):
    content = csv_bytes("取引先コード,名前,取引先の種類,締日,支払日", "C001,作り物の得意先,得意先,月末,翌月 25 日")

    response = post_csv(client_logged_in, "masters:partner_import", content)

    assert response.status_code == 302
    partner = Partner.objects.get(code="C001")
    assert (partner.closing_day, partner.payment_month, partner.payment_day) == (31, "next", 25)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("url_name", "model", "fallback"),
    [("masters:item_import_sample", Item, "item_sample.csv"), ("masters:partner_import_sample", Partner, "partner_sample.csv")],
)
def test_sample_csv(client_logged_in, no_registration, url_name, model, fallback):
    response = client_logged_in.get(reverse(url_name))

    text = response.content.decode("utf-8")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert f'filename="{fallback}"' in response["Content-Disposition"]
    assert "filename*=UTF-8''" in response["Content-Disposition"]
    assert text.startswith("﻿")
    assert text[1:].split("\r\n") == [",".join(csvio.header_labels(model)), ""]
    assert "図面ファイル" not in text
    assert client_logged_in.post(reverse(url_name)).status_code == 405


@pytest.mark.django_db
def test_sample_csv_imports_back(client_logged_in, user, no_registration):
    item_values = {"自社品番": "P-1001", "品名": "作り物の軸", "図面番号": "DWG-1001", "単位": "個", "標準単価 (円)": "480", "備考": "見本から"}
    partner_values = {
        "取引先コード": "C001", "名前": "作り物の得意先", "略称": "作り得", "取引先の種類": "得意先", "締日": "月末",
        "支払の月": "翌月", "支払日": "25", "金額の端数の扱い": "切捨", "消費税の端数の扱い": "各行切捨", "備考": "",
    }
    for url_name, form_class, values in (
        ("masters:item_import_sample", ItemCsvImportForm, item_values),
        ("masters:partner_import_sample", PartnerCsvImportForm, partner_values),
    ):
        sample = client_logged_in.get(reverse(url_name)).content
        labels = sample.decode("utf-8-sig").splitlines()[0].split(",")
        content = sample + ",".join(values[label] for label in labels).encode() + b"\r\n"
        _form, result = import_valid(form_class, content, user)
        assert result == {"入れた": 1, "更新した": 0}

    item = Item.objects.get()
    assert (item.part_no, item.unit, item.standard_price, item.note) == ("P-1001", "個", Decimal("480"), "見本から")
    partner = Partner.objects.get()
    assert (partner.closing_day, partner.payment_month, partner.payment_day) == (31, "next", 25)
    assert (partner.rounding, partner.tax_mode) == ("floor", "line_floor")


def test_screens_in_catalog():
    from seizou.core.screens import all_screens

    screens = {screen.url_name: screen for screen in all_screens()}

    assert screens["masters:item_import"].name == "部品マスターの CSV 取り込み"
    assert screens["masters:partner_import"].name == "取引先の CSV 取り込み"
    assert screens["masters:item_import"].path == "/masters/items/import/"
    assert screens["masters:partner_import"].path == "/masters/partners/import/"


def test_non_editable_columns_are_not_csv_headers():
    """画面で入れられない列 (図面の表示用・表示用の誤り) は、見本の見出しに出ず、CSV から書かせない。"""
    from seizou.core import csvio
    from seizou.masters.models import Item

    labels = csvio.header_labels(Item)
    assert "図面の表示用" not in labels
    assert not any("誤り" in label for label in labels)
