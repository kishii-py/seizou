"""2 回押しの止め (static の js/seizou-guard.js と base.html の読み込みの 1 行)。

JS は動かさず、字と読み込みの並びを見る (ブラウザの確かめは本体の手)。
"""

import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

import seizou.core

JS = Path(seizou.core.__file__).resolve().parent / "static" / "js" / "seizou-guard.js"
SCRIPT_SRC = re.compile(r"<script [^>]*src=\"([^\"]+)\"[^>]*>")


def test_guard_js_is_small_and_plain():
    text = JS.read_text(encoding="utf-8")

    assert len(text.splitlines()) <= 50
    for word in (
        'addEventListener("submit"',
        '"htmx:beforeRequest"',
        '"htmx:afterRequest"',
        '"pageshow"',
        "persisted",
        "setTimeout",
        "submitter",
        "defaultPrevented",
        "requestConfig",
        '"post"',
        "処理中…",
    ):
        assert word in text, word
    for word in ("classList", "className", "fetch(", "XMLHttpRequest"):
        assert word not in text, word
    # CSRF の見出しの名前は土台のテンプレートの 1 か所だけ (R18)。試験のファイルにも名前の字をそのまま書かない。
    header = "x-" + "csrftoken"
    assert header not in text.lower()


@pytest.mark.django_db
def test_base_loads_guard_after_row_menu(client):
    user = get_user_model().objects.create_user("tester", password="pass-for-tests-only")
    client.force_login(user)

    body = client.get(reverse("core:home")).content.decode()

    scripts = SCRIPT_SRC.findall(body)
    guard = [i for i, src in enumerate(scripts) if src.endswith("js/seizou-guard.js")]
    menu = [i for i, src in enumerate(scripts) if src.endswith("js/seizou-row-menu.js")]
    assert len(guard) == 1 and len(menu) == 1
    assert guard[0] == menu[0] + 1
    tag = re.search(r"<script [^>]*js/seizou-guard\.js[^>]*>", body).group(0)
    assert "defer" in tag


@pytest.mark.django_db
def test_login_page_loads_guard_too(client):
    body = client.get(reverse("core:login")).content.decode()

    scripts = [src for src in SCRIPT_SRC.findall(body) if src.endswith("js/seizou-guard.js")]
    assert len(scripts) == 1
