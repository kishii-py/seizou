"""画面の一部を取りに行く要求 (htmx) の失敗を知らせる (指示書 11-07 の見つけ FB-02 と FB-01)。

base.html の末尾の script が、htmx:responseError (4xx・5xx)・htmx:sendError (通信切れ)・htmx:timeout (時間切れ) を document で受け、
右上の赤い知らせ (段 2) を seizou:notice の合図で出す (新しい知らせの部品を作らない)。取り直し (hx-trigger に every) は失敗が続く間
1 回だけ知らせる。小窓の箱 (#seizou-modal-box) を先とする GET は、取りに行く前に箱を空にし、失敗したら小窓を閉じる (前に開いた
別の記録を出さない)。小窓の中の保存 (POST) の失敗では閉じず、seizou-modal.js が小窓の箱の頭に同じ箱を入れる。
JS は字と、base.html の script を node の vm で動かして見る (document と要素の作り物)。ブラウザでの確かめは本体の手。
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from django.urls import reverse

import seizou.core

CORE = Path(seizou.core.__file__).resolve().parent
BASE = CORE / "templates" / "core" / "base.html"
MODAL_JS = CORE / "static" / "js" / "seizou-modal.js"
NO_NODE = "node が無い (JS の字の形は本体の門の node --check で見る)"


def last_script() -> str:
    """base.html の末尾の <script> の中身 (知らせを閉じる処理と、失敗を知らせる処理)。"""
    return re.findall(r"<script>(.*?)</script>", BASE.read_text(encoding="utf-8"), re.S)[-1]


# --- 字 ---


def test_base_script_receives_the_four_events_once():
    script = last_script()
    for event in ("htmx:beforeRequest", "htmx:after-request", "htmx:responseError", "htmx:sendError", "htmx:timeout"):
        assert script.count(f'document.addEventListener("{event}"') == 1, event
    # 小窓を閉じるのは kebab の名前の方 (htmx は camelCase の後に同じ中身の kebab の出来事を出し、要素の hx-on::after-request の showModal()
    # はそちらで動く。document で受ける kebab の方が showModal() より後に来る)。camelCase の afterRequest では要素より先に来て閉じられない。
    assert 'document.addEventListener("htmx:afterRequest"' not in script
    # 出し方は今ある seizou:notice の段 2 (stop)。新しい知らせの部品 (template) を足さない。
    assert 'new CustomEvent("seizou:notice"' in script and '"stop"' in script
    assert BASE.read_text(encoding="utf-8").count("<template") == 1
    # 小窓の箱を先とする GET は、取りに行く前に箱を空にし、失敗したら小窓を閉じる。
    for word in ("seizou-modal-box", "replaceChildren", "modal.close()", "data-seizou-failed", "読み込めません。", "送れません。"):
        assert word in script, word
    # HTML を差し込まない。class を触らない。
    for word in ("innerHTML", "insertAdjacentHTML", ".classList", ".className"):
        assert word not in script, word


def test_modal_js_opens_only_with_content_and_puts_notice_in_open_modal():
    script = MODAL_JS.read_text(encoding="utf-8")
    assert "box.firstElementChild" in script
    assert "modal.open && modalBox" in script
    assert "'.seizou-toast[data-notice=\"' + level + '\"]'" in script


@pytest.mark.django_db
def test_home_has_the_script_and_the_modal_box(client_logged_in):
    body = client_logged_in.get(reverse("core:home")).content.decode()
    assert 'id="seizou-modal-box"' in body
    assert 'document.addEventListener("htmx:responseError"' in body
    assert body.count('document.addEventListener("htmx:timeout"') == 1


# --- 動き (node の vm) ---

#: document (addEventListener・getElementById・querySelectorAll・dispatchEvent) と CustomEvent の作り物の中で base.html の末尾の script を読み、
#: 失敗の出来事を順に送って、出た知らせの字と、小窓の箱と小窓の動きを返す。
NODE_HARNESS = """
const fs = require("fs");
const vm = require("vm");
const listeners = {};
const notices = [];
function element(attrs) {
  const a = Object.assign({}, attrs || {});
  return {
    attrs: a,
    getAttribute(name) { return name in a ? a[name] : null; },
    setAttribute(name, value) { a[name] = value; },
    removeAttribute(name) { delete a[name]; },
    hasAttribute(name) { return name in a; },
  };
}
const box = Object.assign(element(), { cleared: 0, replaceChildren() { this.cleared += 1; } });
const modal = { open: true, closed: 0, close() { this.closed += 1; this.open = false; } };
class CustomEvent { constructor(type, init) { this.type = type; this.detail = (init || {}).detail; } }
const document = {
  addEventListener(type, fn) { listeners[type] = fn; },
  getElementById(id) { return id === "seizou-modal-box" ? box : id === "seizou-modal" ? modal : null; },
  querySelectorAll() { return []; },
  dispatchEvent(event) { notices.push(event.detail.items.map((item) => item.level + ":" + item.message)); return true; },
};
const context = { document, CustomEvent, setTimeout() {} };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
function fire(type, detail, target) { listeners[type]({ detail, target: target || element() }); }
const out = {};
// 1. 小窓の箱を先とする GET: 取りに行く前に箱を空にする。POST では空にしない。
fire("htmx:beforeRequest", { target: box, requestConfig: { verb: "get" } });
fire("htmx:beforeRequest", { target: box, requestConfig: { verb: "post" } });
out.cleared = box.cleared;
// 2. GET の 500: 「読み込めません。」の知らせ 1 つ。
fire("htmx:responseError", { xhr: { status: 500 }, requestConfig: { verb: "get" } });
// 3. 小窓を開く GET が失敗した後の afterRequest: 開いてしまった小窓を閉じる。
fire("htmx:after-request", { successful: false, target: box, requestConfig: { verb: "get" } });
out.closedAfterGet = modal.closed;
// 4. 小窓の中の保存 (POST) の失敗では閉じない。
modal.open = true;
fire("htmx:after-request", { successful: false, target: box, requestConfig: { verb: "post" } });
out.closedAfterPost = modal.closed;
// 5. 通信切れ (POST) と時間切れ、403・404・400。
fire("htmx:sendError", { requestConfig: { verb: "post" } });
fire("htmx:timeout", { requestConfig: { verb: "get" } });
fire("htmx:responseError", { xhr: { status: 403 }, requestConfig: { verb: "get" } });
fire("htmx:responseError", { xhr: { status: 404 }, requestConfig: { verb: "get" } });
fire("htmx:responseError", { xhr: { status: 400 }, requestConfig: { verb: "post" } });
out.beforePolling = notices.length;
// 6. 取り直し (every) は失敗が続く間 1 回だけ。成功で印が消え、次の失敗でまた 1 回。
const poll = element({ "hx-trigger": "every 2s [!document.activeElement]" });
fire("htmx:responseError", { xhr: { status: 500 }, requestConfig: { verb: "get", elt: poll } }, poll);
fire("htmx:responseError", { xhr: { status: 500 }, requestConfig: { verb: "get", elt: poll } }, poll);
fire("htmx:sendError", { requestConfig: { verb: "get", elt: poll } }, poll);
out.pollingOnce = notices.length - out.beforePolling;
out.marked = poll.hasAttribute("data-seizou-failed");
fire("htmx:after-request", { successful: true, requestConfig: { verb: "get", elt: poll } }, poll);
out.unmarked = !poll.hasAttribute("data-seizou-failed");
fire("htmx:responseError", { xhr: { status: 502 }, requestConfig: { verb: "get", elt: poll } }, poll);
out.pollingAgain = notices.length - out.beforePolling;
out.notices = notices;
out.events = Object.keys(listeners).sort();
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)
def test_base_script_notifies_failures(tmp_path):
    script = tmp_path / "base_tail.js"
    script.write_text(last_script(), encoding="utf-8")
    result = subprocess.run(["node", "-e", NODE_HARNESS, str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)

    assert out["events"] == [
        "click",
        "htmx:after-request",
        "htmx:beforeRequest",
        "htmx:configRequest",
        "htmx:responseError",
        "htmx:sendError",
        "htmx:timeout",
    ]
    assert out["cleared"] == 1
    assert out["closedAfterGet"] == 1 and out["closedAfterPost"] == 1
    # 1 つの合図に stop が 1 つ。字は「<操作>できません。<理由>。<次にすること>。」。
    messages = [items for items in out["notices"]]
    assert all(len(items) == 1 and items[0].startswith("stop:") for items in messages)
    texts = [items[0][len("stop:") :] for items in messages]
    assert texts[0] == "読み込めません。サーバーが応えません (誤りの番号 500)。少し待ってから、もう一度押してください。"
    assert texts[1] == "送れません。通信が切れています。電波か LAN を確かめてから、もう一度押してください。"
    assert texts[2] == "読み込めません。時間切れです。少し待ってから、もう一度押してください。"
    assert texts[3].startswith("読み込めません。この操作の権限がありません (誤りの番号 403)。")
    assert texts[4].startswith("読み込めません。記録が見つかりません (誤りの番号 404)。")
    assert texts[5].startswith("送れません。要求が受け付けられません (誤りの番号 400)。")
    assert out["beforePolling"] == 6
    # 取り直しは失敗が続く間 1 回だけ。成功で印が消え、次の失敗でまた 1 回。
    assert out["pollingOnce"] == 1 and out["marked"] and out["unmarked"] and out["pollingAgain"] == 2
    for text in texts:
        assert text.startswith(("読み込めません。", "送れません。")) and text.count("。") >= 3, text
