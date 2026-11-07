"""一覧の行の操作のメニュー (static の js/seizou-row-menu.js と base.html の読み込みの 1 行)。

JS は字で約束を見るほか、htmx の差し替えの後の閉じ方だけ node の vm で動かす (document と details と ul だけの作り物の中)。
ブラウザでの確かめは本体の手の確かめと朝の E2E。
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

import seizou.core

JS = Path(seizou.core.__file__).resolve().parent / "static" / "js" / "seizou-row-menu.js"
NO_NODE = "node が無い (JS の字の形は本体の門の node --check で見る)"


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
    # 開く時は ul を body の直下へ移し (止めた 1 列目の欄と行の地がメニューより手前に描かれないため。本人の報告 11-07 18:0x)、閉じる時に戻す。
    # htmx の差し替えの後に、details が document から外れた ul だけ閉じる (ul が body に残らないため)。差し替えの前に全部閉じる形は
    # 使わない (受信した注文の 2 秒ごとの取り直し = 先頭に行を足すだけの afterbegin で、開いた ⋮ が閉じていた。指示書 11-07 の GS-01)。
    assert "document.body.appendChild(list)" in text and "details.appendChild(list)" in text
    assert 'data-row-menu-list' in text and 'document.addEventListener("htmx:afterSwap"' in text
    assert "isConnected" in text
    assert "htmx:beforeSwap" not in text


#: node で JS を動かす台。document (addEventListener・querySelector・querySelectorAll・body・documentElement) と、details と ul の作り物。
#: 1 つの details を summary の押下で開き (ul を body へ移す)、(1) details が残ったままの差し替えの後 → 開いたまま、
#: (2) details が document から外れた差し替えの後 → 閉じて ul が details の中へ戻る (body に残らない)、を返す。
NODE_HARNESS = """
const fs = require("fs");
const vm = require("vm");
const listeners = {};
const rect = { left: 600, top: 100, right: 640, bottom: 124, width: 40, height: 24 };
function element(tag) {
  const attrs = {};
  const el = {
    nodeType: 1, tagName: tag, parentNode: null, children: [], style: {}, isConnected: true,
    offsetWidth: 224, offsetHeight: 120,
    setAttribute(name, value) { attrs[name] = value; },
    removeAttribute(name) { delete attrs[name]; delete el.style; el.style = {}; },
    getAttribute(name) { return name in attrs ? attrs[name] : null; },
    hasAttribute(name) { return name in attrs; },
    appendChild(child) {
      if (child.parentNode) child.parentNode.children = child.parentNode.children.filter((c) => c !== child);
      child.parentNode = el; el.children.push(child); return child;
    },
    remove() { if (el.parentNode) { el.parentNode.children = el.parentNode.children.filter((c) => c !== el); el.parentNode = null; } },
    getBoundingClientRect() { return rect; },
    closest() { return null; },
    matches() { return false; },
    querySelector() { return null; },
  };
  return el;
}
const body = element("BODY");
const main = element("MAIN");
const details = element("DETAILS");
details.open = false;
details.matches = (sel) => sel === "details.row-menu";
const summary = element("SUMMARY");
summary.parentElement = details;
summary.closest = (sel) => (sel === "summary" ? summary : sel === "details.row-menu" ? details : null);
const list = element("UL");
details.appendChild(summary);
details.appendChild(list);
details.querySelector = (sel) => (sel === "ul" ? (list.parentNode === details ? list : null) : sel === "summary" ? summary : null);
const document = {
  body,
  documentElement: { clientWidth: 1280, clientHeight: 800 },
  addEventListener(type, fn) { listeners[type] = fn; },
  querySelector(sel) { return sel === "main" ? main : null; },
  querySelectorAll(sel) {
    if (sel === "details.row-menu[open]") return details.open && details.isConnected ? [details] : [];
    if (sel === "[data-row-menu-list]") return body.children.filter((c) => c.hasAttribute("data-row-menu-list"));
    return [];
  },
};
const context = { document, window: {} };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
function state() {
  return { open: details.open, inBody: list.parentNode === body, inDetails: list.parentNode === details, fixed: list.style.position || null };
}
listeners.click({ target: summary, preventDefault() {} });
const opened = state();
listeners["htmx:afterSwap"]({});
const kept = state();
details.isConnected = false;
listeners["htmx:afterSwap"]({});
const closed = state();
console.log(JSON.stringify({ events: Object.keys(listeners).sort(), opened, kept, closed }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)
def test_js_passes_node_check():
    result = subprocess.run(["node", "--check", str(JS)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)
def test_after_swap_closes_only_detached_menus():
    result = subprocess.run(["node", "-e", NODE_HARNESS, str(JS)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["events"] == ["click", "contextmenu", "htmx:afterSwap", "keydown", "scroll", "toggle"]
    # 開くと ul は body の直下 (fixed)。
    assert out["opened"] == {"open": True, "inBody": True, "inDetails": False, "fixed": "fixed"}
    # details が残る差し替え (受信した注文の afterbegin) の後は開いたまま。
    assert out["kept"] == {"open": True, "inBody": True, "inDetails": False, "fixed": "fixed"}
    # details が消えた差し替え (一覧の innerHTML) の後は閉じ、ul は details の中へ戻る (body に残らない)。
    assert out["closed"] == {"open": False, "inBody": False, "inDetails": True, "fixed": None}


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
