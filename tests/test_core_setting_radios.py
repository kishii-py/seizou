"""設定の変更の画面の丸の選択 (C76。初見テスト R5 の候補)。

選べる値 (conf.CHOICES) と真偽 (分納・生産計画の画面) は丸の選択 (core.forms の Radios) にし、
選ぶと何が起きるかの 1 文 (core.forms の CHOICE_NOTES) を名前の下に小さな薄い字で出す。文の無い値は名前だけ。
権限の判定の 3 つの選ぶ欄・使わない注文の項目の check の箱・字の欄は今のまま。送る値と保存する値は英字の値のまま
(真偽は "true" / "false" で送り、form が真偽に直して保存する)。値の名前は作り物の会社の設定だけ。

書き方の注意: input の tag と名前の <span> の間には改行が入る (Django の input.html の終わりの改行) ので \\s* で受ける。
input の属性の並びは字で決め打ちしない。名前の下の 1 文と同じ class の組の span は左のメニューの頭にもあるので、
「ページに無い」の形では見ない (丸の label の中だけで見る)。
"""

from __future__ import annotations

import re

from django import forms
from django.urls import reverse

from seizou.core import conf
from seizou.core.forms import CHECKBOX_CSS, CHOICE_NOTES, Checkboxes, Radios, SettingForm
from seizou.core.models import SettingOverride
from tests import test_core_settings_edit
from tests.test_core_settings_edit import edit_url

# 設定の変更の試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
# manager = システム管理者 (権限の判定 access_control も変えられる)。manager_client = その人でログインした client。
manager = test_core_settings_edit.manager
manager_client = test_core_settings_edit.manager_client

#: 真偽の設定の鍵 (丸の選択の「true」「false」)
BOOL_KEYS = ("partial_shipment", "production_plan")

#: 名前の下の 1 文の span の頭 (_radio_option.html の字と同じ)
NOTE_SPAN = '<span class="block text-xs text-base-content/60">'

#: 丸の 1 つずつの label (_radio_option.html の字と同じ)
RADIO_LABEL = re.compile(r'<label class="flex cursor-pointer items-start gap-2 py-1"[^>]*>.*?</label>', re.S)


def radios(body: str, key: str) -> list:
    """ページの丸の (送る値, 残りの属性) の並び。"""
    return re.findall(rf'<input type="radio" name="{key}" value="(\w+)"([^>]*)>', body)


def checked_values(body: str, key: str) -> list:
    return [value for value, rest in radios(body, key) if re.search(r"\bchecked\b", rest)]


def named(label: str, note: str = "") -> str:
    """丸の名前の span (1 文があれば名前の下の span を入れ子にする)。"""
    return f"<span>{label}{NOTE_SPAN}{note}</span></span>" if note else f"<span>{label}</span></label>"


# ---------------------------------------------------------------- (a) (b) form と 1 文の表


def test_choice_and_bool_settings_use_radios_and_others_stay(db):
    for key in (*conf.CHOICES, *BOOL_KEYS):
        assert type(SettingForm(key=key).fields[key].widget) is Radios, key

    form = SettingForm(key="access_control")
    for kind in ("department", "write", "money"):
        assert type(form.fields[kind].widget) is forms.Select, kind
    form = SettingForm(key="hidden_fields")
    assert type(form.fields[SettingForm.HIDDEN_FIELDS_FIELD].widget) is Checkboxes
    form = SettingForm(key="assistant_model")
    assert type(form.fields["assistant_model"]) is forms.CharField
    assert type(form.fields["assistant_model"].widget) is forms.TextInput


def test_choice_notes_match_the_choices():
    for key, notes in CHOICE_NOTES.items():
        assert key in conf.CHOICES or isinstance(conf.DEFAULTS[key], bool), key
        values = conf.CHOICES[key] if key in conf.CHOICES else ("true", "false")
        for value, note in notes.items():
            assert value in values, (key, value)
            assert isinstance(note, str) and note.strip(), (key, value)


# ---------------------------------------------------------------- (c) (d) 選べる値の画面


def test_over_shipment_page_shows_three_radios_with_notes(manager_client, settings):
    settings.SEIZOU = {"over_shipment": "stop"}
    body = manager_client.get(edit_url("over_shipment")).content.decode()

    found = radios(body, "over_shipment")
    assert [value for value, _rest in found] == ["warn", "stop", "off"]
    assert checked_values(body, "over_shipment") == ["stop"]
    assert all('class="radio"' in rest for _value, rest in found)
    # 外の枠には丸の見た目を付けない
    assert '<div id="id_over_shipment">' in body
    assert '<div id="id_over_shipment" class=' not in body
    assert '<select name="over_shipment"' not in body
    assert named("知らせる", "黄色の知らせと「このまま保存」のボタンが出て、もう一度押すと保存される。") in body
    assert named("止める", "赤の知らせが出て、保存されない。") in body
    assert named("通す", "知らせを出さずに保存する。") in body

    body = manager_client.get(edit_url("over_workorder")).content.decode()
    assert [value for value, _rest in radios(body, "over_workorder")] == ["warn", "stop", "off"]
    assert named("知らせる", "黄色の知らせと「このまま保存」のボタンが出て、もう一度押すと保存される。") in body
    assert named("止める", "赤の知らせが出て、保存されない。") in body
    assert named("通す", "知らせを出さずに保存する。") in body


def test_values_without_notes_show_names_only(manager_client):
    body = manager_client.get(edit_url("order_form")).content.decode()
    assert named("1 件 1 品", "画面が短く、番号が部品と 1 対 1 になる。") in body
    assert named("1 件に明細が複数") in body

    body = manager_client.get(edit_url("order_number")).content.decode()
    assert named("連番") in body
    assert named("年月 + 連番") in body
    labels = RADIO_LABEL.findall(body)
    assert len(labels) == 2
    assert all("text-xs" not in label for label in labels)


# ---------------------------------------------------------------- (e) (f) 真偽


def test_bool_settings_are_two_radios(manager_client, settings):
    settings.SEIZOU = {"partial_shipment": False}
    body = manager_client.get(edit_url("partial_shipment")).content.decode()
    assert [value for value, _rest in radios(body, "partial_shipment")] == ["true", "false"]
    assert checked_values(body, "partial_shipment") == ["false"]
    assert named("許す") in body and named("許さない") in body
    assert "分納: 許す" not in body
    assert "check を外すと" not in body
    assert 'type="checkbox" name="partial_shipment"' not in body

    settings.SEIZOU = {}
    body = manager_client.get(edit_url("production_plan")).content.decode()
    assert checked_values(body, "production_plan") == ["true"]
    assert named("使わない", "メニューに「生産計画」が出ない。") in body


def test_bool_settings_save_true_and_false(manager_client):
    response = manager_client.post(edit_url("partial_shipment"), {"partial_shipment": "false"})
    assert response.status_code == 302 and response["Location"] == reverse("core:settings")
    assert conf.setting("partial_shipment") is False
    assert SettingOverride.objects.get(key="partial_shipment").value is False

    response = manager_client.post(edit_url("partial_shipment"), {"partial_shipment": "true"})
    assert response.status_code == 302
    assert conf.setting("partial_shipment") is True
    assert SettingOverride.objects.get(key="partial_shipment").value is True

    response = manager_client.post(edit_url("production_plan"), {"production_plan": "false"})
    assert response.status_code == 302
    assert conf.setting("production_plan") is False


# ---------------------------------------------------------------- (g) 権限の判定


def test_access_control_page_keeps_selects(manager_client):
    body = manager_client.get(edit_url("access_control")).content.decode()
    for kind in ("department", "write", "money"):
        assert f'<select name="{kind}"' in body, kind
    assert 'type="radio"' not in body


# ---------------------------------------------------------------- (h) 頭の行


#: 設定の変更の画面の頭の行 (settings_form.html の「今の値の出所:」を含む段落。中に tag は無い)
HEAD_LINE = re.compile(r"<p[^>]*>[^<]*今の値の出所:[^<]*</p>")


def test_head_line_has_no_key_or_file_names(manager_client, settings):
    # 出所が初期値になり、導入の時に決めた値の字 (conf.SOURCE_ANSWER) は文の中にだけ出る
    settings.SEIZOU = {}
    for key in ("over_shipment", "partial_shipment", "access_control"):
        body = manager_client.get(edit_url(key)).content.decode()
        found = HEAD_LINE.search(body)
        assert found, key
        line = found.group(0)
        for word in ("settings.py", "SEIZOU", "YAML", "<code", "鍵"):
            assert word not in line, (key, word)
        assert "ここで保存した値は、" in line, key
        assert conf.SOURCE_ANSWER in line, key
        assert f'<code class="font-mono">{key}</code>' not in body, key


# ---------------------------------------------------------------- (i) 複数の checkbox の格子の口


def test_checkboxes_columns_put_grid_only_on_the_outer_box(db):
    """core の Checkboxes は、columns=True のときだけ外の枠に格子の class を付ける (初見テスト R5。候補 C83)。

    1 つずつの check の見た目はどちらも同じ。使わない注文の項目の check は口を開けない (1 列のまま)。
    """
    choices = [("a", "A"), ("b", "B")]

    plain = Checkboxes(attrs={"class": CHECKBOX_CSS}, choices=choices).render("x", [], {"id": "id_x"})
    grid = Checkboxes(attrs={"class": CHECKBOX_CSS}, choices=choices, columns=True).render("x", [], {"id": "id_x"})

    assert '<div id="id_x">' in plain
    assert '<div id="id_x" class=' not in plain
    assert '<div id="id_x" class="grid gap-x-6 sm:grid-cols-2 xl:grid-cols-3">' in grid
    for html in (plain, grid):
        inputs = re.findall(r"<input[^>]*>", html)
        assert len(inputs) == 2
        assert all(f'class="{CHECKBOX_CSS}"' in tag for tag in inputs)

    form = SettingForm(key="hidden_fields")
    assert form.fields[SettingForm.HIDDEN_FIELDS_FIELD].widget.columns is False
