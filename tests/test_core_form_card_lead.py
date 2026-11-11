"""入力のカード (core/_form_card.html) の頭の 1 行 (form の属性 lead_text。本体の決め 11-11 13:4x。N400b の追記)。

欄の無い確かめの入力 (受注と発注の明細の「完了にする」) が、明細ごとの字 (「残り n は出さずに終えます。…」) を出す所。
form が lead_text を持ち、字が空でないときだけ、今ある class だけで灰の字の 1 行を描く (字は autoescape のまま)。
属性の無い form と、字が空の form では何も描かない (ほかの画面の見え方は変わらない)。
"""

import re

import pytest
from django import forms
from django.template.loader import get_template
from django.test import RequestFactory

LEAD_LINE = '<p class="mb-4 text-sm text-base-content/70" data-form-lead>'


class PlainForm(forms.Form):
    """欄も lead_text も無い form (今ある確かめの form の形)。"""

    submit_label = "送り直す"


class LeadForm(forms.Form):
    submit_label = "完了にする"
    lead_text = "残り 2 は出さずに終えます。<b>太字にしない</b>"


class EmptyLeadForm(forms.Form):
    submit_label = "完了にする"
    lead_text = ""


def _render(form, *, in_modal=False):
    request = RequestFactory().get("/orders/lines/1/complete/")
    return get_template("core/_form_card.html").render({"form": form, "request": request, "in_modal": in_modal})


@pytest.mark.parametrize("in_modal", [False, True])
def test_lead_line_is_drawn_and_escaped(in_modal):
    html = _render(LeadForm(), in_modal=in_modal)
    assert f"{LEAD_LINE}残り 2 は出さずに終えます。&lt;b&gt;太字にしない&lt;/b&gt;</p>" in html
    assert "<b>" not in html
    # 1 行は form の直下 (欄の無い確かめのカードの枠を消す CSS の規則に当たらず、白いカードの中に出る)
    form_html = re.search(r"<form .*?</form>", html, re.S).group(0)
    assert LEAD_LINE in form_html
    assert form_html.index(LEAD_LINE) < form_html.index("<button")


@pytest.mark.parametrize("in_modal", [False, True])
@pytest.mark.parametrize("form_class", [PlainForm, EmptyLeadForm])
def test_no_lead_draws_nothing(form_class, in_modal):
    html = _render(form_class(), in_modal=in_modal)
    assert "data-form-lead" not in html
    # 字の無い form は、属性の無い form と同じ HTML (ほかの画面の見え方は変わらない)
    assert html == _render(PlainForm(), in_modal=in_modal).replace("送り直す", form_class.submit_label)
