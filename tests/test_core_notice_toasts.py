"""止まった知らせと確かめの知らせを、入力の画面の全体では右上の知らせ (core/_toasts.html) で出す (候補 C57。決め 11-06 C57-3・C57-4)。

入力の土台 (BaseFormView) は、止まった (赤) を notice、確かめ (黄) を warnings として context に置く。画面の全体は base.html の
core/_toasts.html が、止まった箱 (data-notice="stop") と確かめの箱 (data-notice="check") を右上に描く。上の帯は出さない。
欄の検査で止まった時も止まった知らせを出す (form_invalid。欄の誤りなら欄の下の赤い字と知らせの両方)。
小窓の中は base.html を使わないので、今までどおり小窓の上の赤と黄の帯 (core/_form_modal.html の core/_notices.html)。
押すだけの操作の知らせ (messages) は今のまま。段と小窓の例外は作品の CLAUDE.md の「知らせの強さ」。
試験の画面は部品マスターの休日の新規登録 (作り物の休日の名前)。
"""

import re
from pathlib import Path

import pytest
from django import forms
from django.urls import reverse

from seizou.core import checks
from seizou.core.manual import REFUSAL_HEADS
from seizou.core.views import (
    REFUSAL_HEAD_BY_SERVICE,
    REFUSAL_HEAD_CANCEL,
    REFUSAL_HEAD_DELETE,
    REFUSAL_HEAD_SAVE,
    BaseFormView,
    invalid_form_message,
    refusal_head,
)
from seizou.masters.forms import HolidayForm
from seizou.masters.models import Holiday
from tests.test_core_form_modal import ACTION, MODAL, _HolidayStop, holiday_checks, holiday_data  # noqa: F401 - fixture を借りる
from tests.test_masters_csv_import import csv_bytes, no_registration, post_csv  # noqa: F401 - fixture を借りる

ROOT = Path(__file__).resolve().parent.parent
STOP = 'data-notice="stop"'
CHECK = 'data-notice="check"'
FIX_FIELDS = "の欄に誤りがあります。欄の下の赤い字のとおりに直してから、もう一度押してください。"


class _HolidayCheck(checks.Check):
    """作り物の知らせる検査 (名前に「作り物」が入っていたら黄の知らせ)。番号は決まりの形 (R と 2 桁)。"""

    action = ACTION
    mode = "warn"

    def __init__(self, name: str, message: str):
        self.name = name
        self.message = message

    def run(self, data, user):
        return self.message if "作り物" in str(data.get("name", "")) else None


def text(response) -> str:
    return response.content.decode()


def boxes(body: str, mark: str) -> list[str]:
    """右上の知らせの箱 (mark を持つ物) を、開きの tag から箱の閉じまで切り出して並べる (閉じるボタンの後の最初の </div> が箱の閉じ)。"""
    found = []
    at = body.find(mark)
    while at >= 0:
        start = body.rindex("<div", 0, at)
        end = body.index("</div>", body.index("data-toast-close", at)) + len("</div>")
        found.append(body[start:end])
        at = body.find(mark, end)
    return found


def label(name: str) -> str:
    return str(HolidayForm().fields[name].label)


def post_holiday(client, data: dict, **headers):
    return client.post(reverse("masters:holiday_create"), data, **headers)


# --- (a) 画面の全体で欄の誤りで止まる ---


@pytest.mark.django_db
def test_field_error_on_whole_page_shows_red_toast(client_logged_in):
    response = post_holiday(client_logged_in, holiday_data(""))

    assert response.status_code == 200
    body = text(response)
    assert 'id="seizou-toasts"' in body
    stops = boxes(body, STOP)
    assert len(stops) == 1
    box = stops[0]
    assert "失敗しました。" in box
    assert f'<span class="font-semibold">保存できません。</span> 「{label("name")}」{FIX_FIELDS}' in box
    assert "data-auto" not in box and "data-toast-level" not in box
    assert 'id="id_name_error"' in body  # 欄の下の赤い字も出る (1 と 2 の両方)
    assert "alert alert-error" not in body  # 上の帯は出さない
    assert Holiday.objects.count() == 0


@pytest.mark.django_db
def test_field_error_labels_follow_form_order(client_logged_in):
    response = post_holiday(client_logged_in, {"date": "2026-12-29", "name": "", "kind": ""})

    box = boxes(text(response), STOP)[0]
    assert f'</span> 「{label("name")}」「{label("kind")}」{FIX_FIELDS}' in box


class _ManyFieldsForm(forms.Form):
    """作り物の form: 見出しのある欄が 5 つと、隠しの欄と見出しの空の欄 (どちらも名前を数えない)。"""

    first = forms.CharField(label="一つ目")
    second = forms.CharField(label="二つ目")
    hidden = forms.CharField(widget=forms.HiddenInput)
    third = forms.CharField(label="三つ目")
    blank = forms.CharField(label="")
    fourth = forms.CharField(label="四つ目")
    fifth = forms.CharField(label="五つ目")


def test_more_than_three_labels_end_with_the_rest_count():
    form = _ManyFieldsForm(data={})
    assert not form.is_valid()

    message = invalid_form_message(form, "保存できません。")

    assert message == f"保存できません。「一つ目」「二つ目」「三つ目」ほか 2 つ{FIX_FIELDS}"


def test_errors_only_in_hidden_fields_use_the_plain_sentence():
    form = _ManyFieldsForm(data={"first": "a", "second": "b", "third": "c", "blank": "d", "fourth": "e", "fifth": "f"})
    assert not form.is_valid()

    message = invalid_form_message(form, "保存できません。")

    assert message == "保存できません。入れた値に誤りがあります。赤い字のとおりに直してから、もう一度押してください。"


class _CrossCheckForm(forms.Form):
    """作り物の form: 欄の外の誤り (2 つ) と欄の誤りを両方持てる。"""

    name = forms.CharField(label="品名", required=False)
    code = forms.CharField(label="品番")

    def clean(self):
        self.add_error(None, "作り物の欄の外の誤りです。")
        self.add_error(None, "もう 1 つの誤りです。")
        return self.cleaned_data


def test_non_field_errors_follow_the_head_and_ask_to_fix_named_fields():
    with_fields = _CrossCheckForm(data={})
    only_outside = _CrossCheckForm(data={"code": "X-1"})
    assert not with_fields.is_valid() and not only_outside.is_valid()

    assert invalid_form_message(only_outside, "保存できません。") == "保存できません。作り物の欄の外の誤りです。 もう 1 つの誤りです。"
    assert (
        invalid_form_message(with_fields, "保存できません。")
        == "保存できません。作り物の欄の外の誤りです。 もう 1 つの誤りです。 「品番」の欄も直してください。"
    )


# --- (b) 小窓では小窓の上の赤い帯 ---


@pytest.mark.django_db
def test_field_error_in_modal_stays_in_the_red_band(client_logged_in):
    response = post_holiday(client_logged_in, holiday_data(""), **MODAL)

    assert response.status_code == 200
    body = text(response)
    assert 'id="seizou-toasts"' not in body
    band = body[body.index("alert alert-error") :]
    band = band[: band.index("</div>")]
    assert f'<span class="font-semibold">保存できません。</span> 「{label("name")}」{FIX_FIELDS}' in band


# --- (c) 止める検査で止まる: 番号と「?」 ---


@pytest.mark.django_db
def test_stop_check_shows_rule_and_help_button(client_logged_in, holiday_checks):  # noqa: F811
    checks.register(_HolidayStop())

    body = text(post_holiday(client_logged_in, holiday_data("作り物の止め")))

    stops = boxes(body, STOP)
    assert len(stops) == 1
    box = stops[0]
    assert "作り物の止めです。" in box
    assert '<span class="font-mono text-xs">R13</span>' in box
    assert "text-base-content/60" not in box
    help_buttons = re.findall(r'<button[^>]*hx-get="/rules/R13/"[^>]*>', box)
    assert len(help_buttons) == 1
    assert 'aria-label="R13 の決まりの説明"' in help_buttons[0]
    assert "pointer-events-auto" in help_buttons[0]
    assert Holiday.objects.count() == 0


# --- (d) 知らせる検査が 2 件: 黄の箱を縦に 2 つ ---


@pytest.mark.django_db
def test_two_warnings_are_two_yellow_toasts_in_order(client_logged_in, holiday_checks):  # noqa: F811
    checks.register(_HolidayCheck("R91", "1 つ目の作り物の知らせです。確かめてください。"))
    checks.register(_HolidayCheck("R92", "2 つ目の作り物の知らせです。確かめてください。"))

    body = text(post_holiday(client_logged_in, holiday_data("作り物の休み")))

    found = boxes(body, CHECK)
    assert len(found) == 2
    assert "1 つ目の作り物の知らせです。" in found[0] and "R91" in found[0]
    assert "2 つ目の作り物の知らせです。" in found[1] and "R92" in found[1]
    for box in found:
        assert "確認してください。" in box
        assert "/rules/" not in box
        before_close = box[: box.index("data-toast-close")]
        assert before_close.count("<button") == 1  # 閉じるボタンだけ (「?」は出さない)
    assert "このまま保存" in body
    assert 'name="confirmed" value="1"' in body
    assert "alert alert-warning" not in body
    assert Holiday.objects.count() == 0


# --- (e) 欄の外の誤り: カードの中の帯が消え、字は知らせに 1 回だけ ---


@pytest.mark.django_db
def test_non_field_error_moves_into_the_toast(client_logged_in, no_registration):  # noqa: F811
    content = csv_bytes("自社品番,品名,標準単価 (円)", "P-1001,作り物の軸,480", "P-1002,作り物の板,abc")

    response = post_csv(client_logged_in, "masters:item_import", content)

    assert response.status_code == 200
    body = text(response)
    words = "CSV に誤りが 1 件あります。1 件も入れていません。下の表を見て直してから、もう一度選んでください。"
    box = boxes(body, STOP)[0]
    assert f'<span class="font-semibold">保存できません。</span> {words}' in box
    assert body.count(words) == 1
    assert "alert alert-error" not in body


# --- (f) 1 文目の表 ---


@pytest.mark.parametrize(
    ("service_name", "head"),
    [
        ("cancel_order", "取り消せません。"),
        ("delete_holiday", "削除できません。"),
        ("remove_attachment", "削除できません。"),
        ("create_invoice", "発行できません。"),
        ("record_payment", "記録できません。"),
        ("ship_lines", "出荷できません。"),
        ("receive", "受け入れられません。"),
        ("reject", "断れません。"),
        ("create_item", "保存できません。"),
        ("", "保存できません。"),
    ],
)
def test_refusal_head_by_service_name(service_name, head):
    assert refusal_head(service_name, BaseFormView.DELETE_SERVICE_PREFIXES) == head


def test_refusal_heads_are_in_the_shared_set():
    chosen = {REFUSAL_HEAD_SAVE, REFUSAL_HEAD_CANCEL, REFUSAL_HEAD_DELETE, *REFUSAL_HEAD_BY_SERVICE.values()}
    assert chosen <= set(REFUSAL_HEADS)


# --- (g) 押すだけの操作の赤い知らせは今のまま ---


@pytest.mark.django_db
def test_button_only_error_toast_is_unchanged(user, client_logged_in):
    # 押すだけの操作の口は上の帯の ☆ (bookmarks:toggle)。指示書の着手の口は無くなった (初見テスト R5 の候補 C27)。
    # 番号の付く画面は登録できないので、services が「保存できません。…」の ValueError を出し、view が赤の知らせにする。
    back_url = reverse("workorders:workorder_list")

    client_logged_in.post(
        reverse("bookmarks:toggle"), {"url_name": "workorders:workorder_detail", "on": "1", "next": back_url}
    )
    body = text(client_logged_in.get(back_url))

    assert "data-notice" not in body
    start = body.index('id="seizou-toasts"')
    toasts = body[start : body.index("</div>", body.index("data-toast-close", start))]
    assert "失敗しました" in toasts
    assert '<p class="mt-1 break-words">保存できません。番号の付く画面です。' in toasts


# --- (h) 文書 ---


def test_claude_md_has_the_notice_levels_and_base_has_no_band():
    claude_md = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "## 知らせの強さ (5 つの段)" in claude_md
    section = claude_md[claude_md.index("## 知らせの強さ (5 つの段)") :]
    section = section[: section.index("\n## ", 1)]
    for word in ("欄の誤り", "止まった", "確かめ", "済んだ", "状態"):
        assert f"| {word} |" in section or f" {word} |" in section, word
    assert "小窓" in section
    base = (ROOT / "src" / "seizou" / "core" / "templates" / "core" / "base.html").read_text(encoding="utf-8")
    assert '{% include "core/_notices.html" %}' not in base
