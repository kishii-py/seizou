"""選ぶ欄の部品 (seizou.core.widgets.RemoteSelect と static の js/seizou-select.js)。

RemoteSelect は候補の口の URL を data-select-url に入れ、選んである値の <option> だけを描く。
JS はブラウザで動かさず、字で約束を見る (ブラウザでの確かめは朝の E2E)。
"""

from pathlib import Path

import pytest
from django import forms
from django.test import RequestFactory
from django.urls import reverse

import seizou.core
from seizou.core.views import BaseFormView
from seizou.core.widgets import RemoteSelect
from seizou.masters.models import Item

JS = Path(seizou.core.__file__).resolve().parent / "static" / "js" / "seizou-select.js"


class PickItemForm(forms.Form):
    item = forms.ModelChoiceField(
        queryset=Item.objects.all(), widget=RemoteSelect("masters:item_select"), label="品目"
    )


class PickItemView(BaseFormView):
    form_class = PickItemForm


@pytest.fixture
def items(db):
    return [
        Item.objects.create(part_no="P-1001", name="作り物のブラケット"),
        Item.objects.create(part_no="P-2002", name="作り物のシャフト"),
    ]


@pytest.mark.django_db
def test_renders_url_and_only_selected_option(items):
    chosen, other = items
    html = str(PickItemForm(initial={"item": chosen.pk})["item"])
    assert f'data-select-url="{reverse("masters:item_select")}"' in html
    assert f'value="{chosen.pk}" selected' in html
    assert "P-1001 作り物のブラケット" in html
    assert f'value="{other.pk}"' not in html
    assert "P-2002" not in html


@pytest.mark.django_db
def test_renders_no_rows_without_value_and_with_bad_value(items):
    empty = str(PickItemForm()["item"])
    assert 'value=""' in empty
    assert "P-1001" not in empty and "P-2002" not in empty

    bound = PickItemForm(data={"item": "not-a-number"})
    assert not bound.is_valid()
    html = str(bound["item"])
    assert "P-1001" not in html and "P-2002" not in html


@pytest.mark.django_db
def test_base_form_view_adds_width_class_but_not_daisy_select(items):
    # 字を打って探す欄には daisyUI の select の class を付けない (Tom Select が包みに写して候補の一覧を隠す。
    # 初見テスト 2 回目の所見)。幅の class だけを付け、見た目は input.css の .ts-wrapper で決める。
    request = RequestFactory().get("/")
    view = PickItemView()
    view.setup(request)
    form = view.get_form()
    css = form.fields["item"].widget.attrs["class"].split()
    assert "w-full" in css and "select" not in css
    assert "class=" in str(form["item"]) and "data-select-url" in str(form["item"])


def test_js_file_keeps_the_rules():
    assert JS.is_file()
    text = JS.read_text(encoding="utf-8")
    for word in ("data-select-url", "htmx:afterSettle", "isComposing", "229", "virtual_scroll", "250"):
        assert word in text
    assert "jquery" not in text.lower()
    assert "$(" not in text
    assert ".innerHTML" not in text  # 取った字を HTML として差し込まない (escape は Tom Select の既定の描き方)
