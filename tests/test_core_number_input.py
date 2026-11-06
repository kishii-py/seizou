"""数の欄の字 (初見テスト 5 回目 U43・V73・U124・a-flagged-12。候補 C43)。

1. 数の欄 (core の DecimalInput) の開いた時の字を、詳細と一覧と同じ字 (seizou_tags.comma) にする。印 data-number を付ける。
   送って止まった画面は、使う人が打った字のまま (全角と「,」は外れた字)。
作り物のデータだけを使う。ブラウザでの確かめ (離れた時の字・書きかけの知らせ) は本体の手。
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.core import fields as company_fields
from seizou.core.widgets import DecimalInput
from seizou.masters import services as masters_services


@pytest.fixture
def no_registration(monkeypatch):
    """会社ごとの項目の登録を空から始める (ほかの試験の登録を残さない)。"""
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


def text(response) -> str:
    return response.content.decode()


def tag_of(body: str, name: str) -> str:
    """name の欄の <input …> の字。"""
    start = body.index(f'name="{name}"')
    return body[body.rindex("<input", 0, start) : body.index(">", start) + 1]


# --- 1. 開いた時の字 ---


@pytest.mark.parametrize(("price", "shown"), [("1200", "1,200"), ("12.5", "12.5"), ("480", "480"), ("1234567.8", "1,234,567.8")])
def test_item_update_shows_price_like_detail(logged_in, user, price, shown):
    item = masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "standard_price": Decimal(price)}, user
    )
    tag = tag_of(text(logged_in.get(reverse("masters:item_update", args=[item.pk]))), "standard_price")
    assert f'value="{shown}"' in tag
    assert 'type="text"' in tag and 'inputmode="decimal"' in tag
    assert " data-number" in tag


def test_item_create_field_has_number_mark(logged_in):
    tag = tag_of(text(logged_in.get(reverse("masters:item_create"))), "standard_price")
    assert " data-number" in tag and "value=" not in tag


def test_widget_formats_decimal_like_comma():
    widget = DecimalInput()
    assert widget.format_value(Decimal("2.50")) == "2.5"
    assert widget.format_value(Decimal("1200.00")) == "1,200"
    assert widget.format_value(Decimal("480.00")) == "480"
    assert widget.format_value(Decimal("0.00")) == "0"
    assert widget.format_value(Decimal("-1500.25")) == "-1,500.25"
    # 字 (打った字・字の初期値) と None は今のまま。
    assert widget.format_value("12.50") == "12.50"
    assert widget.format_value(None) is None
    assert widget.attrs["data-number"] is True and widget.attrs["inputmode"] == "decimal"


def test_company_number_field_shows_like_detail(logged_in, user, monkeypatch, no_registration):
    # B 社の膜厚 (会社ごとの項目の数)。試験の置き場に拡張の表は無いので、拡張の表の値を読む関数を作り物の値に差し替える。
    company_fields.register("masters.Item", "fields.ItemExt", [{"label": "膜厚", "type": "number"}])
    monkeypatch.setattr(company_fields, "ext_value", lambda obj, name: Decimal("2.50") if name == "ext01" else None)
    item = masters_services.create_item({"part_no": "T-110", "name": "作り物のめっきの軸"}, user)
    tag = tag_of(text(logged_in.get(reverse("masters:item_update", args=[item.pk]))), "ext__ext01")
    assert 'value="2.5"' in tag and " data-number" in tag


@pytest.mark.parametrize(("typed", "kept"), [("12.50", "12.50"), ("1,200", "1200"), ("１２．５０", "12.50")])
def test_stopped_form_keeps_typed_text(logged_in, typed, kept):
    # 品名が空で止まる。欄は打った字のまま (全角は半角に、「,」は外れた字。サーバーの normalize_number を通した後の字)。
    response = logged_in.post(
        reverse("masters:item_create"), {"part_no": "P-4004", "name": "", "standard_price": typed}
    )
    assert response.status_code == 200
    assert f'value="{kept}"' in tag_of(text(response), "standard_price")
