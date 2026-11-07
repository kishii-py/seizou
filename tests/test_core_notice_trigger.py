"""画面の一部だけを差し替える応答の、止まった知らせと確かめを右上の知らせに出す合図 (候補 C109。決め 11-07 C57-5)。

core.views.add_notice_trigger は、応答の見出し HX-Trigger に seizou:notice ({"items": [...]}) を付ける。
受けた core/static/js/seizou-modal.js が、base.html の写す元 (template#seizou-toast-template) の赤 (stop) と黄 (check) の箱を
写して右上に入れる (見た目と印は core/_toasts.html と同じ)。使う所は生産計画の盤 (plan_place)・説明書の画像の枠 (manuals の画像の口)・
メモの書く枠 (入力の土台 BaseFormView が、小窓でない htmx の HX-Target の要求に付ける。写真を送れなかった時は note-editor.js が同じ合図を出す)。
見る所: 合図の形・写す元の箱・JS の書き方の約束・作品の CLAUDE.md の文書・メモの書く枠の合図。
JS の動き (写す・入れ替える・「?」) はブラウザが要る (本体が画面で見る)。メモの付ける先の作り方は tests/test_notes_views.py の fixture を借りる。
"""

import json
from pathlib import Path

import pytest
from django.http import HttpResponse
from django.urls import reverse

from seizou.core import checks
from seizou.core.access import AccessDenied
from seizou.core.checks import CheckWarning
from seizou.core.exceptions import RuleViolation
from seizou.core.views import NOTICE_EVENT, REFUSAL_HEAD_SAVE, Notice, add_notice_trigger
from seizou.notes.forms import MESSAGE_TARGET_NOT_FOUND
from seizou.notes.models import Note
from tests.test_core_form_modal import MODAL
from tests.test_core_notice_toasts import boxes
from tests.test_notes_views import order, target_data, user  # noqa: F401 - fixture を借りる

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "src" / "seizou" / "core" / "static" / "js" / "seizou-modal.js"
BASE = ROOT / "src" / "seizou" / "core" / "templates" / "core" / "base.html"
NOTE_JS = ROOT / "src" / "seizou" / "notes" / "static" / "notes" / "note-editor.js"
NOTE_FORM = ROOT / "src" / "seizou" / "notes" / "templates" / "notes" / "note_form.html"
#: メモの書く枠の form が送る見出し (hx-target="#notes" なので htmx は HX-Target に notes を付ける)
NOTES_HTMX = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "notes"}
NOTE_ACTION = "notes.save_note"


def notice_items(response) -> list[dict]:
    """応答の見出し HX-Trigger の seizou:notice の items。"""
    return json.loads(response["HX-Trigger"])[NOTICE_EVENT]["items"]


def template_part(body: str) -> str:
    """本文の中の写す元 (template#seizou-toast-template) の中身。"""
    start = body.index('<template id="seizou-toast-template">')
    return body[start : body.index("</template>", start)]


def level_box(template: str, level: str) -> str:
    """写す元の中の data-toast-level が level の箱 (開きの tag から閉じるボタンの後の最初の </div> まで)。"""
    at = template.index(f'data-toast-level="{level}"')
    start = template.rindex("<div", 0, at)
    return template[start : template.index("</div>", template.index("data-toast-close", at)) + len("</div>")]


# --- (a) 合図を付ける関数 ---


def test_trigger_has_stop_item_in_ascii_header():
    response = add_notice_trigger(HttpResponse(), Notice("入力", "保存できません。作り物の理由です。"))
    header = response["HX-Trigger"]
    assert header.isascii() and not header.startswith("=?")
    assert notice_items(response) == [{"level": "stop", "message": "保存できません。作り物の理由です。", "rule": "", "help": ""}]


@pytest.mark.django_db
def test_trigger_rule_has_help_and_access_denied_has_none():
    rule = notice_items(add_notice_trigger(HttpResponse(), RuleViolation("R05", "保存できません。作り物の決まりです。")))
    assert rule == [
        {
            "level": "stop",
            "message": "保存できません。作り物の決まりです。",
            "rule": "R05",
            "help": reverse("core:rule_help", args=["R05"]),
        }
    ]
    denied = notice_items(add_notice_trigger(HttpResponse(), AccessDenied("作り物の断る文です。")))
    assert denied == [{"level": "stop", "message": "作り物の断る文です。", "rule": "", "help": ""}]


def test_trigger_puts_checks_after_stop_in_order():
    warnings = [CheckWarning("R31", "作り物の一つ目の確かめです。"), CheckWarning("T01", "作り物の二つ目の確かめです。")]
    items = notice_items(add_notice_trigger(HttpResponse(), Notice("入力", "保存できません。作り物です。"), warnings))
    assert [item["level"] for item in items] == ["stop", "check", "check"]
    assert items[1] == {"level": "check", "message": "作り物の一つ目の確かめです。", "rule": "R31", "help": ""}
    assert items[2] == {"level": "check", "message": "作り物の二つ目の確かめです。", "rule": "", "help": ""}
    only_checks = notice_items(add_notice_trigger(HttpResponse(), None, warnings))
    assert [item["level"] for item in only_checks] == ["check", "check"]


def test_trigger_is_absent_without_notice_and_warnings():
    assert "HX-Trigger" not in add_notice_trigger(HttpResponse())
    assert "HX-Trigger" not in add_notice_trigger(HttpResponse(), None, [])


# --- (b) 写す元の赤と黄の箱 ---


@pytest.mark.django_db
def test_toast_template_has_stop_and_check_boxes(logged_in):
    body = logged_in.get(reverse("masters:holiday_list")).content.decode()
    template = template_part(body)
    assert template.count('data-toast-level="stop"') == 1
    assert template.count('data-toast-level="check"') == 1
    assert body.count('data-toast-level="success"') == 1
    assert body.count('data-toast-level="warning"') == 1
    assert body.count("data-toast-text") == 2
    assert "data-notice" not in body
    stop = level_box(template, "stop")
    for word in ("data-toast-head", "data-toast-rest", "data-toast-rule", "data-toast-help", "失敗しました。", 'role="alert"'):
        assert word in stop, word
    check = level_box(template, "check")
    for word in ("data-toast-head", "data-toast-rest", "data-toast-rule", "確認してください。", 'role="alert"'):
        assert word in check, word
    assert "data-toast-help" not in check
    for box in (stop, check):
        assert "data-auto" not in box
        assert "text-base-content/50" not in box
        assert "data-toast-close" in box
    assert "data-notice" not in BASE.read_text(encoding="utf-8")


# --- (c) 受けて写す JS の書き方 ---


def test_modal_js_handles_notice_without_class_or_html():
    script = JS.read_text(encoding="utf-8")
    for word in ("seizou:notice", "data-notice", "setAttribute", "htmx.process", "data-toast-head", "data-toast-help", "textContent", "cloneNode"):
        assert word in script, word
    for word in (".classList", ".className", ".innerHTML", "insertAdjacentHTML", "outerHTML"):
        assert word not in script, word


# --- (f) 文書 ---


def test_claude_md_names_the_notice_trigger():
    claude_md = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    section = claude_md[claude_md.index("## 知らせの強さ (5 つの段)") :]
    section = section[: section.index("\n## ", 1)]
    assert "seizou:notice" in section
    assert "add_notice_trigger" in section


# --- メモの書く枠 (入力の土台の合図と note-editor.js) ---


class _NoteCheck(checks.Check):
    """作り物の知らせる検査 (いつも黄の知らせ)。番号は決まりの形 (R と 2 桁)。"""

    name = "R41"
    action = NOTE_ACTION
    mode = "warn"

    def run(self, data, account):  # 引数の名前は借りた fixture の user と分ける (検査の層は位置で渡す)
        return "作り物の確かめです。確かめてから保存してください。"


@pytest.fixture
def note_checks():
    before = checks.registered(NOTE_ACTION)
    yield
    checks.clear(NOTE_ACTION)
    for check in before:
        checks.register(check)


def note_data(target, **extra) -> dict:
    return {**target_data(target), "body_html": "<p>作り物のメモです。</p>", **extra}


def notes_section(body: str) -> str:
    start = body.index('<section id="notes"')
    return body[start : body.index("</section>", start)]


# --- (g) 止まった知らせは合図で右上に出し、メモの枠の中に帯を出さない ---


@pytest.mark.django_db
def test_note_stop_is_trigger_without_band(client_logged_in, order):  # noqa: F811 - 借りた fixture
    response = client_logged_in.post(reverse("notes:note_edit"), note_data(order, object_id=999999), **NOTES_HTMX)

    assert response.status_code == 200
    assert notice_items(response) == [
        {"level": "stop", "message": REFUSAL_HEAD_SAVE + MESSAGE_TARGET_NOT_FOUND, "rule": "", "help": ""}
    ]
    section = notes_section(response.content.decode())
    assert "alert alert-error" not in section
    assert "data-note-error" not in section
    assert Note.objects.count() == 0


# --- (h) 画面の全体と小窓の形には合図を付けない ---


@pytest.mark.django_db
def test_note_stop_whole_page_and_modal_have_no_trigger(client_logged_in, order):  # noqa: F811 - 借りた fixture
    data = note_data(order, object_id=999999)

    whole = client_logged_in.post(reverse("notes:note_edit"), data, HTTP_HX_REQUEST="true")
    assert "HX-Trigger" not in whole
    [stop] = boxes(whole.content.decode(), 'data-notice="stop"')
    assert MESSAGE_TARGET_NOT_FOUND in stop

    modal = client_logged_in.post(reverse("notes:note_edit"), data, **MODAL)
    assert "HX-Trigger" not in modal
    assert Note.objects.count() == 0


# --- (i) 確かめ (知らせるの検査) も合図で右上の黄の知らせに出す ---


@pytest.mark.django_db
def test_note_check_is_trigger(client_logged_in, order, note_checks):  # noqa: F811 - 借りた fixture
    checks.register(_NoteCheck())

    response = client_logged_in.post(reverse("notes:note_edit"), note_data(order), **NOTES_HTMX)

    assert response.status_code == 200
    assert notice_items(response) == [
        {"level": "check", "message": "作り物の確かめです。確かめてから保存してください。", "rule": "R41", "help": ""}
    ]
    assert Note.objects.count() == 0


# --- (j) メモの書く枠のテンプレートと JS の字 ---


def test_note_editor_sends_notice_without_band():
    script = NOTE_JS.read_text(encoding="utf-8")
    for word in ("seizou:notice", "CustomEvent"):
        assert word in script, word
    for word in ("data-note-error", "htmx.", ".classList", ".className", ".innerHTML"):
        assert word not in script, word
    form = NOTE_FORM.read_text(encoding="utf-8")
    assert "core/_notices.html" not in form
    assert "data-note-error" not in form
