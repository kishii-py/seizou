"""会社ごとの字の欄に、既にある値を候補 (<datalist>) で出す (初見テスト 5 回目の V83。候補 C28 の 3。持ち主の答え C28-3 = a)。

欄は自由に打てるまま、候補に無い字もそのまま保存する (断らない)。候補の元は拡張の表の 1 列 (core.fields.ext_suggestions)。
試験の DB に拡張の表 (fields.ItemExt) は無いので、候補は ext_suggestions を差し替えて渡し、保存は save_ext を捕まえて見る。
"""

import re

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core import fields as company_fields
from seizou.core.fields import frequent_values
from seizou.core.widgets import DatalistInput
from seizou.masters.models import Item

# 字・数・日付・選ぶ欄・真偽の 5 つの型 (字を 1 番目に置いて ext01 にする。名前は作り物)
FIVE_TYPES = [
    {"label": "処理の種類", "type": "text"},
    {"label": "作り物の厚さ", "type": "number"},
    {"label": "検査日", "type": "date"},
    {"label": "表面の仕上げ", "type": "choice", "choices": ["めっき", "塗装"]},
    {"label": "作り物の印", "type": "bool"},
]
SUGGESTIONS = ["白アルマイト", "黒アルマイト"]


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


def tag_of(body, element_id):
    """id の欄の開きの tag (input・select・textarea) の字。"""
    match = re.search(rf'<(?:input|select|textarea)\b[^>]*\bid="{element_id}"[^>]*>', body)
    assert match, element_id
    return match.group(0)


def datalist_options(body, list_id):
    match = re.search(rf'<datalist id="{list_id}">(.*?)</datalist>', body, re.S)
    assert match, list_id
    return re.findall(r'<option value="([^"]*)"></option>', match.group(1))


def item_form_pages(client):
    """部品の新規登録と編集の HTML。"""
    item = Item.objects.create(part_no="P-1001", name="作り物のブラケット", unit="個")
    pages = []
    for url in (reverse("masters:item_create"), reverse("masters:item_update", args=[item.pk])):
        response = client.get(url)
        assert response.status_code == 200
        pages.append(response.content.decode())
    return pages


# ---------------------------------------------------------------- (a) 候補の元


@pytest.mark.django_db
def test_frequent_values_skips_blank_and_counts_inactive():
    notes = ["急ぎ", "急ぎ", "検査あり", "支給品", "検査あり", "", "急ぎ"]
    for i, note in enumerate(notes, start=1):
        Item.objects.create(part_no=f"P-{i:04d}", name="作り物の部品", note=note, active=i != 7)
    assert Item.objects.filter(active=False, note="急ぎ").count() == 1

    assert frequent_values(Item.objects.all(), "note") == ["急ぎ", "検査あり", "支給品"]
    assert frequent_values(Item.objects.all(), "note", limit=2) == ["急ぎ", "検査あり"]


# ---------------------------------------------------------------- (b)(c) widget だけ


def test_widget_adds_datalist_after_input_and_reads_values_when_rendering():
    calls = []

    def values():
        calls.append(1)
        return SUGGESTIONS

    widget = DatalistInput(values=values)
    assert calls == []  # 作った時には読まない

    html = widget.render("ext__ext01", "", attrs={"id": "id_ext__ext01"})
    assert calls == [1]  # 1 回描くと 1 回読む
    field_tag, rest = html.split(">", 1)
    assert field_tag.startswith("<input") and 'list="id_ext__ext01_list"' in field_tag
    assert rest.startswith('<datalist id="id_ext__ext01_list">')  # input の直後
    assert datalist_options(html, "id_ext__ext01_list") == SUGGESTIONS


def test_widget_without_values_is_plain_text_input():
    attrs = {"id": "id_ext__ext01", "class": "input w-full", "maxlength": "100"}
    plain = forms.TextInput().render("ext__ext01", "作り物", attrs=attrs)
    assert DatalistInput(values=lambda: []).render("ext__ext01", "作り物", attrs=attrs) == plain
    assert "list=" not in plain and "<datalist" not in plain
    # id の無い欄には候補を付けない (list の先を指せないため)
    assert "<datalist" not in DatalistInput(values=lambda: SUGGESTIONS).render("ext__ext01", "")


def test_widget_escapes_values():
    html = DatalistInput(values=lambda: ['a"<b']).render("ext__ext01", "", attrs={"id": "id_ext__ext01"})
    assert '<option value="a&quot;&lt;b"></option>' in html
    assert 'a"<b' not in html


# ---------------------------------------------------------------- (d)(e)(f) 部品の入力の画面


@pytest.mark.django_db
def test_only_company_text_field_gets_suggestions(client_logged_in, no_registration, monkeypatch):
    company_fields.register("masters.Item", "fields.ItemExt", FIVE_TYPES)
    asked = []

    def suggestions(model, name):
        asked.append((model, name))
        return SUGGESTIONS

    monkeypatch.setattr(company_fields, "ext_suggestions", suggestions)
    for body in item_form_pages(client_logged_in):
        text_tag = tag_of(body, "id_ext__ext01")
        assert 'list="id_ext__ext01_list"' in text_tag and 'maxlength="100"' in text_tag
        assert datalist_options(body, "id_ext__ext01_list") == SUGGESTIONS
        assert body.count("<datalist") == 1
        for element_id in ("id_ext__ext02", "id_ext__ext03", "id_ext__ext04", "id_ext__ext05", "id_unit"):
            assert "list=" not in tag_of(body, element_id), element_id
        assert 'name="unit"' in tag_of(body, "id_unit")
    # 候補を読むのは字の欄の分だけ (画面ごとに 1 回)
    assert asked == [(Item, "ext01"), (Item, "ext01")]


@pytest.mark.django_db
def test_no_suggestions_keeps_plain_field(client_logged_in, no_registration, monkeypatch):
    company_fields.register("masters.Item", "fields.ItemExt", FIVE_TYPES)
    monkeypatch.setattr(company_fields, "ext_suggestions", lambda model, name: [])
    body = client_logged_in.get(reverse("masters:item_create")).content.decode()
    assert "<datalist" not in body
    assert "list=" not in tag_of(body, "id_ext__ext01")


@pytest.mark.django_db
def test_site_without_ext_table_renders_without_suggestions(client_logged_in, no_registration):
    # 試験の置き場には拡張の表 (fields.ItemExt) が無い。候補の読みは LookupError で空になり、画面は今と同じに描ける
    company_fields.register("masters.Item", "fields.ItemExt", [{"label": "処理の種類"}])
    assert company_fields.ext_suggestions(Item, "ext01") == []
    for body in item_form_pages(client_logged_in):
        assert "<datalist" not in body
        assert "list=" not in tag_of(body, "id_ext__ext01")


# ---------------------------------------------------------------- (g) 候補に無い字も断らない


@pytest.mark.django_db
def test_value_outside_suggestions_is_saved_as_typed(client_logged_in, no_registration, monkeypatch, captured_ext):
    company_fields.register("masters.Item", "fields.ItemExt", FIVE_TYPES)
    monkeypatch.setattr(company_fields, "ext_suggestions", lambda model, name: SUGGESTIONS)
    response = client_logged_in.post(
        reverse("masters:item_create"),
        {"part_no": "P-3003", "name": "作り物のカバー", "unit": "個", "ext__ext01": "亜鉛めっき"},
    )
    item = Item.objects.get(part_no="P-3003")
    assert response.status_code == 302
    assert response["Location"] == reverse("masters:item_detail", args=[item.pk])
    [(pk, ext)] = captured_ext
    assert pk == item.pk and ext["ext01"] == "亜鉛めっき"
