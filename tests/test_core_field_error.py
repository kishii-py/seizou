"""直した欄の赤枠と誤りの字をその場で消す (初見テスト R5 の V36。候補 C25 の 3)。

core の js/seizou-field-error.js が、入力の土台の form (form[data-enter-guard]) の欄の input と change を document で受け、
その欄の aria-invalid を外して誤りの字 (<auto_id>_error) を高さを残して隠す。
JS は動かさず、読み込みの並びと字と node --check を見る (ブラウザの確かめは tests/e2e/test_e55_field_error.py と本体の手)。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from django.urls import reverse

import seizou.core

CORE = Path(seizou.core.__file__).resolve().parent
FIELD_ERROR_JS = CORE / "static" / "js" / "seizou-field-error.js"
SCRIPT_SRC = re.compile(r"<script [^>]*src=\"([^\"]+)\"[^>]*>")


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
