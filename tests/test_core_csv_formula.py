"""一覧の CSV の書き出しが、表計算の式として動く字を式にしないこと (Claude Security の走査の候補 11-07 の 1・14・15・16・36・41・44・45)。

意図: 取引先の名前・部署・ロット・相手の注文番号のような人や相手が入れた字が「=」「+」「-」「@」で始まると、
書き出した CSV を表計算で開いたときに式として動く。書き出しは 1 か所の関数 (seizou.core.csvio.neutralize) で頭に「'」を付け、
数の列 (Decimal・int) は付けない。取り込み (csvio.read_table) は、書き出しが付けた「'」を外して、往復で値が変わらないようにする。
名前は作り物。
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.forms.models import model_to_dict
from django.urls import reverse

from seizou.core import csvio, seeds
from seizou.core import fields as company_fields
from seizou.core import summary as list_summary
from seizou.masters import services
from seizou.masters.forms import ItemCsvImportForm, PartnerCsvImportForm
from seizou.masters.models import Item, Partner
from tests.test_masters_csv_import import import_valid


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def no_registration(monkeypatch):
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=HYPERLINK(\"http://example.test\")", "'=HYPERLINK(\"http://example.test\")"),
        ("+1+1", "'+1+1"),
        ("-1+1", "'-1+1"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\t=1+1", "'\t=1+1"),
        ("\r=1+1", "'\r=1+1"),
        ("作り物の商会", "作り物の商会"),
        ("A=1", "A=1"),
        ("", ""),
        (None, ""),
        (True, seeds.TRUE_TEXT),
        (False, seeds.FALSE_TEXT),
        (Decimal("-5.50"), "-5.50"),
        (-3, "-3"),
        (1.5, "1.5"),
    ],
)
def test_to_text_neutralizes_formula_text_only(value, expected):
    assert csvio.to_text(value) == expected


def test_neutralize_and_restore_are_inverse():
    for text in ("=1+1", "+81-3", "-作り物", "@作り物", "\t作り物", "作り物", "'作り物", ""):
        assert csvio.restore(csvio.neutralize(text)) == text
    # 頭の「'」の後が式の字でなければ外さない (人が入れた「'」はそのまま)
    assert csvio.restore("'作り物") == "'作り物"


def test_summary_csv_neutralizes_axis_labels():
    summary = {
        "axis": SimpleNamespace(label="得意先"),
        "measure": SimpleNamespace(label="金額"),
        "rows": [
            {"label": "=作り物の得意先", "value": Decimal("-1200"), "share": Decimal("-50.0")},
            {"label": "作り物の商会", "value": Decimal("3600"), "share": Decimal("150.0")},
        ],
        "total": Decimal("2400"),
    }
    rows = list_summary.csv_rows(summary)
    assert rows[1] == ["'=作り物の得意先", "-1200", "-50.0"]
    assert rows[2] == ["作り物の商会", "3600", "150.0"]


@pytest.mark.django_db
def test_list_csv_export_neutralizes_partner_name(client_logged_in, user, no_registration):
    services.create_partner({"code": "C001", "name": "=作り物の商会", "role": "customer"}, user)

    text = client_logged_in.get(reverse("masters:partner_list"), {"format": "csv"}).content.decode("utf-8-sig")

    cells = [cell for row in csv.reader(io.StringIO(text)) for cell in row]
    assert "'=作り物の商会" in cells
    assert "=作り物の商会" not in cells


@pytest.mark.django_db
def test_formula_text_roundtrips_through_list_export(client_logged_in, user, no_registration):
    services.create_item({"part_no": "P-2001", "name": "=作り物の軸", "unit": "個", "standard_price": Decimal("480.00")}, user)
    services.create_item({"part_no": "P-2002", "name": "-作り物の板", "drawing_no": "@DWG-1"}, user)
    services.create_partner({"code": "C002", "name": "+作り物の得意先", "role": "customer"}, user)
    items = {item.part_no: model_to_dict(item) for item in Item.objects.all()}
    partners = {partner.code: model_to_dict(partner) for partner in Partner.objects.all()}

    exported = client_logged_in.get(reverse("masters:item_list"), {"format": "csv"}).content
    assert "'=作り物の軸" in exported.decode("utf-8-sig")
    _form, result = import_valid(ItemCsvImportForm, exported, user)
    assert result == {"入れた": 0, "更新した": 2}
    assert {item.part_no: model_to_dict(item) for item in Item.objects.all()} == items

    exported = client_logged_in.get(reverse("masters:partner_list"), {"format": "csv"}).content
    _form, result = import_valid(PartnerCsvImportForm, exported, user)
    assert result == {"入れた": 0, "更新した": 1}
    assert {partner.code: model_to_dict(partner) for partner in Partner.objects.all()} == partners
