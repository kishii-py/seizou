"""送られなかった選ぶ欄を既定の値とみなす欄 (core.forms.DefaultChoiceField)。

受注と発注の明細の「単価の単位」が使う。欄を送らない POST と、何も入れていない足す行を、初めの値と同じとみなす。
作り物の choices だけを使う。
"""

from django import forms

from seizou.core.forms import DefaultChoiceField

CHOICES = [("a", "作り物の甲"), ("b", "作り物の乙")]


class _Form(forms.Form):
    pick = DefaultChoiceField(label="作り物の選ぶ欄", choices=CHOICES, default="a")


def test_missing_value_is_default_and_unchanged():
    form = _Form(data={})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["pick"] == "a"
    assert not form.has_changed()


def test_sent_value_is_kept_and_changed():
    form = _Form(data={"pick": "b"})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["pick"] == "b"
    assert form.has_changed()


def test_unknown_value_is_field_error():
    form = _Form(data={"pick": "z"})
    assert not form.is_valid()
    assert "pick" in form.errors


def test_open_form_has_no_empty_choice_and_default_is_selected():
    html = str(_Form()["pick"])
    assert '<option value="">' not in html
    assert '<option value="a" selected>' in html
    assert '<option value="b">' in html
    assert "required" not in html
