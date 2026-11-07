"""画面の一部だけを差し替える応答の、止まった知らせと確かめを右上の知らせに出す合図 (候補 C109。決め 11-07 C57-5)。

core.views.add_notice_trigger は、応答の見出し HX-Trigger に seizou:notice ({"items": [...]}) を付ける。
受けた core/static/js/seizou-modal.js が、base.html の写す元 (template#seizou-toast-template) の赤 (stop) と黄 (check) の箱を
写して右上に入れる (見た目と印は core/_toasts.html と同じ)。使う所は生産計画の盤 (plan_place)。
見る所: 合図の形・写す元の箱・JS の書き方の約束・作品の CLAUDE.md の文書。JS の動き (写す・入れ替える・「?」) はブラウザが要る (本体が画面で見る)。
"""

import json
from pathlib import Path

import pytest
from django.http import HttpResponse
from django.urls import reverse

from seizou.core.access import AccessDenied
from seizou.core.checks import CheckWarning
from seizou.core.exceptions import RuleViolation
from seizou.core.views import NOTICE_EVENT, Notice, add_notice_trigger

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "src" / "seizou" / "core" / "static" / "js" / "seizou-modal.js"
BASE = ROOT / "src" / "seizou" / "core" / "templates" / "core" / "base.html"


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
