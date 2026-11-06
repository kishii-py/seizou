"""直した欄の赤枠と誤りの字をその場で消し、止まった赤い知らせを閉じる (初見テスト R5 の V36。候補 C25 の 3 と 4。決め 11-06 21:4x C25-3 = b)。

core の js/seizou-field-error.js が、入力の土台の form (form[data-enter-guard]) の欄の input と change を document で受け、
(3) その欄の aria-invalid を外して誤りの字 (<auto_id>_error) を高さを残して隠し、(4) 画面の全体では右上の止まった知らせ
(#seizou-toasts の data-notice="stop") の閉じるボタンを押し、小窓では小窓の箱の中の止まった赤い帯 (data-notice="stop") を消す。
確かめの黄・済んだ知らせ・押すだけの操作の赤には触らず、時間では閉じない。
JS は動かさず、読み込みの並びと字と node --check を見る (ブラウザの確かめは tests/e2e/test_e55_field_error.py・test_e56_stop_notice_close.py と本体の手)。
試験の画面は部品マスターの休日の新規登録 (作り物の休日の名前。tests/test_core_notice_toasts.py と同じ)。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

import seizou.core
from seizou.core.checks import CheckWarning
from seizou.core.views import Notice
from tests.test_core_form_modal import MODAL, holiday_data
from tests.test_core_notice_toasts import STOP, boxes, post_holiday

CORE = Path(seizou.core.__file__).resolve().parent
ROOT = Path(__file__).resolve().parent.parent
FIELD_ERROR_JS = CORE / "static" / "js" / "seizou-field-error.js"
TOASTS = CORE / "templates" / "core" / "_toasts.html"
SCRIPT_SRC = re.compile(r"<script [^>]*src=\"([^\"]+)\"[^>]*>")
RED_BAND_OPEN = '<div class="alert alert-error alert-soft mb-4" role="alert"'


def text(response) -> str:
    return response.content.decode()


# --- 3: 読み込みと JS の字 ---


def test_base_loads_field_error_js_once_after_submit_label(client_logged_in):
    body = text(client_logged_in.get(reverse("core:home")))
    scripts = SCRIPT_SRC.findall(body)
    found = [i for i, src in enumerate(scripts) if src.endswith("js/seizou-field-error.js")]
    label = [i for i, src in enumerate(scripts) if src.endswith("js/seizou-submit-label.js")]
    assert len(found) == 1 and len(label) == 1
    # すぐ次かは見ない (今は間に seizou-number.js と seizou-scroll-hint.js があり、並びの最後に 1 行を足すほかの依頼と順が入れ替わっても通るように)
    assert found[0] > label[0]
    assert "defer" in re.search(r"<script [^>]*js/seizou-field-error\.js[^>]*>", body).group(0)


def test_field_error_js_words():
    script = FIELD_ERROR_JS.read_text(encoding="utf-8")
    for word in (
        "form[data-enter-guard]",
        'addEventListener("input"',
        'addEventListener("change"',
        "aria-invalid",
        "aria-describedby",
        "_error",
        "visibility",
    ):
        assert word in script, word
    # class を触らない (seizou-modal.js の約束)。HTML を差し込まない。
    for word in (".classList", ".className", ".innerHTML"):
        assert word not in script, word


@pytest.mark.skipif(shutil.which("node") is None, reason="node が無い (JS の字の形は本体の門の node --check で見る)")
def test_field_error_js_passes_node_check():
    result = subprocess.run(["node", "--check", str(FIELD_ERROR_JS)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


# --- 4: 止まった知らせを閉じる ---


def test_field_error_js_closes_only_the_stop_notice():
    script = FIELD_ERROR_JS.read_text(encoding="utf-8")
    for word in ("#seizou-toasts", "#seizou-modal-box", '[data-notice="stop"]', "[data-toast-close]"):
        assert word in script, word
    # 確かめの黄は閉じない。時間では閉じない。
    for word in ('data-notice="check"', "setTimeout"):
        assert word not in script, word


def test_notices_red_band_has_the_stop_mark_and_yellow_does_not():
    html = render_to_string(
        "core/_notices.html",
        {
            "notice": Notice("入力", "保存できません。作り物の理由です。"),
            "warnings": [CheckWarning("R01", "作り物の状況の文です。作り物の数の文です。")],
        },
    )

    assert html.count(STOP) == 1
    assert f'{RED_BAND_OPEN} {STOP}>' in html
    yellow = html[html.index("alert alert-warning") :]
    assert "data-notice" not in yellow[: yellow.index(">")]


@pytest.mark.django_db
def test_whole_page_stop_toast_has_one_close_button(client_logged_in):
    body = text(post_holiday(client_logged_in, holiday_data("")))

    stops = boxes(body, STOP)
    assert len(stops) == 1
    assert stops[0].count("data-toast-close") == 1
    assert "alert alert-error" not in body


@pytest.mark.django_db
def test_modal_stop_band_has_the_stop_mark(client_logged_in):
    body = text(post_holiday(client_logged_in, holiday_data(""), **MODAL))

    assert 'id="seizou-toasts"' not in body
    start = body.index(RED_BAND_OPEN)
    assert STOP in body[start : body.index(">", start)]


# --- 4: 決まりの字 ---


def _row(section: str, head: str) -> str:
    return next(line for line in section.splitlines() if line.startswith(head))


def test_claude_md_says_fixing_a_field_closes_the_stop_notice():
    claude_md = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    section = claude_md[claude_md.index("## 知らせの強さ (5 つの段)") :]
    section = section[: section.index("\n## ", 1)]

    stop = _row(section, "| 2 止まった |")
    assert "欄を直すと閉じる" in stop
    assert "時間では消えない" in stop
    assert _row(section, "| 3 確かめ |").endswith("| 自分で閉じるまで残る |")
    assert _row(section, "| 1 欄の誤り |").endswith("| 欄を直すとその場で消える |")


def test_toasts_note_names_the_js_that_closes_the_stop_notice():
    note = TOASTS.read_text(encoding="utf-8").splitlines()[2]

    assert "seizou-field-error.js" in note
    assert "閉じるまで残る" in note
