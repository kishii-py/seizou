"""一覧の行の操作のメニュー (static の js/seizou-row-menu.js と base.html の読み込みの 1 行)。

JS はブラウザで動かさず、字で約束を見る (ブラウザでの確かめは本体の手の確かめと朝の E2E)。
"""

import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

import seizou.core

JS = Path(seizou.core.__file__).resolve().parent / "static" / "js" / "seizou-row-menu.js"


def test_js_file_keeps_promises():
    assert JS.is_file()
    text = JS.read_text(encoding="utf-8")
    for word in ('"use strict"', "contextmenu", "data-row-menu", "details.row-menu", "Escape", "fixed", "seizouRowMenu", "preventDefault"):
        assert word in text, word
    for word in ("innerHTML", "insertAdjacentHTML", "document.write", "fetch(", "XMLHttpRequest", "http://", "https://", "window.htmx", "X-CSRFToken"):
        assert word not in text, word
    # document に委譲して付ける (toggle と scroll は泡立たないので capture)。
    for event in ("contextmenu", "click", "keydown"):
        assert f'document.addEventListener("{event}"' in text, event
    for event in ("toggle", "scroll"):
        assert re.search(rf'document\.addEventListener\("{event}", \w+, true\)', text), event
    assert "window.seizouRowMenu = { closeAll: closeAll }" in text
    # class を足さない (Tailwind が CSS に入れないため)。
    assert "classList.add" not in text and "className" not in text


@pytest.mark.django_db
def test_base_loads_js_after_select(client):
    user = get_user_model().objects.create_user("tester", password="pass-for-tests-only")
    client.force_login(user)

    body = client.get(reverse("core:home")).content.decode()

    scripts = re.findall(r"<script [^>]*src=\"([^\"]+)\"[^>]*>", body)
    menu = [i for i, src in enumerate(scripts) if src.endswith("js/seizou-row-menu.js")]
    select = [i for i, src in enumerate(scripts) if src.endswith("js/seizou-select.js")]
    assert len(menu) == 1 and len(select) == 1
    assert menu[0] == select[0] + 1
    tag = re.search(r"<script [^>]*js/seizou-row-menu\.js[^>]*>", body).group(0)
    assert "defer" in tag
