/*
 * 入力の小窓 (決め 11-04 17:5x〜18:0x。一覧の行の操作はページを移らず小窓で入力する)。
 * 読み込みは base.html の seizou-file-drop.js の次の 1 行 (script の最後)。素の JS で書く (外の library は同梱の htmx だけ)。
 *
 * 決まり:
 * - 開く: data-modal-form の属性 (入力の画面の URL) を持つ要素 (a か button) を押すと、その URL を htmx で
 *   #seizou-modal-box に読み、#seizou-modal を開く (master_link の小窓と同じ形)。入力の土台は見出し HX-Target で
 *   小窓の形と見分け、小窓の中身だけを返す。a の href はそのまま (JS の無いとき・新しいタブで開くときは画面の全体へ移る)。
 *   document に委譲して付ける (行の差し替えの後も効く)。Ctrl・Cmd・Shift を押した押下と、左のボタンでない押下は、ブラウザに任せる。
 * - 保存の後: 入力の土台は小窓の形で保存すると、本文の無い 204 に HX-Trigger の seizou:saved (message と level) を返す。
 *   受けたら小窓を閉じ、右上の知らせを 1 つ出し、body に seizou:refresh を出す。一覧 (core/list.html) の form は
 *   seizou:refresh で今の絞りのまま行 (#rows) を取り直す。
 * - 知らせ: base.html の template#seizou-toast-template から level の合う 1 つを写し、字は textContent で入れる。
 *   class を触らない (classList・className を使わない)。取った字を HTML として差し込まない。
 *   成功は 5 秒で消える (messages の知らせと同じ)。確認は閉じるボタンで消す (閉じるボタンは base.html の script が受ける)。
 */
(function () {
  "use strict";

  var OPENER = "[data-modal-form]";
  var AUTO_MS = 5000;

  function dialog() {
    return document.getElementById("seizou-modal");
  }

  // 入力の画面を小窓に読む。htmx が無ければ偽 (呼んだ側は画面の全体へ移す)。
  function openForm(url) {
    if (!window.htmx) return false;
    window.htmx.ajax("GET", url, { target: "#seizou-modal-box", swap: "innerHTML" }).then(function () {
      var modal = dialog();
      if (modal && typeof modal.showModal === "function" && !modal.open) modal.showModal();
    });
    return true;
  }

  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
    var opener = event.target.closest ? event.target.closest(OPENER) : null;
    if (!opener) return;
    var url = opener.getAttribute("data-modal-form");
    if (url && openForm(url)) event.preventDefault();
  });

  // 知らせを 1 つ消す。右上の箱に知らせが残っていなければ箱も消す (base.html の script と同じ形)。
  function removeToast(toast) {
    if (!toast.isConnected) return;
    var box = toast.parentNode;
    toast.remove();
    if (box && box.id === "seizou-toasts" && !box.querySelector(".seizou-toast")) box.remove();
  }

  function showToast(message, level) {
    var template = document.getElementById("seizou-toast-template");
    if (!template || !message) return;
    var copy = template.content.cloneNode(true);
    var box = copy.querySelector("[data-toast-box]");
    var wanted = level === "warning" ? "warning" : "success";
    var toast = null;
    box.querySelectorAll("[data-toast-level]").forEach(function (each) {
      if (each.getAttribute("data-toast-level") === wanted) toast = each;
      else each.remove();
    });
    if (!toast) return;
    toast.querySelector("[data-toast-text]").textContent = message;
    var current = document.getElementById("seizou-toasts");
    if (current) {
      current.appendChild(toast);
    } else {
      box.id = "seizou-toasts";
      document.body.appendChild(box);
    }
    if (toast.hasAttribute("data-auto")) {
      setTimeout(function () {
        removeToast(toast);
      }, AUTO_MS);
    }
  }

  document.addEventListener("seizou:saved", function (event) {
    var detail = event.detail || {};
    var modal = dialog();
    if (modal && modal.open) modal.close();
    showToast(typeof detail.message === "string" ? detail.message : "", detail.level);
    document.body.dispatchEvent(new CustomEvent("seizou:refresh"));
  });
})();
