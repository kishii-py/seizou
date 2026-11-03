/*
 * 2 回押しの止め (二重送信の防止)。読み込みは base.html の seizou-row-menu.js の次の 1 行。
 * 素の JS で書く (外の library を使わない)。document に委譲し、form ごとに属性を足さない。class は足さない。
 * - POST の form: 1 回目の submit で form に印を付け、押したボタンを「処理中…」にして押せなくする。
 *   ボタンを止めるのは setTimeout の後 (ブラウザが送る値を集めた後。押したボタンの name と value を落とさない)。
 *   印のある form の 2 回目の submit (Enter やほかのボタン) は止める。GET の form と小窓の dialog の form は触らない。
 * - ブラウザの戻る (bfcache) で戻った画面は、pageshow で印とボタンを元に戻す (「処理中…」のまま固まらない)。
 * - htmx の form は、GET でない要求の間だけ送るボタンを押せなくする (hx-disabled-elt と同じ動き。重なっても両方が外す)。
 */
(function () {
  "use strict";
  var HTMX_FORM = "[hx-post], [hx-put], [hx-patch], [hx-delete], [data-hx-post]";
  var SUBMITS = 'button[type="submit"], button:not([type]), input[type="submit"]';
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (event.defaultPrevented || form.method !== "post" || form.matches(HTMX_FORM)) return;
    if (form.dataset.seizouSent) return event.preventDefault();
    form.dataset.seizouSent = "1";
    var button = event.submitter;
    if (!button) return;
    setTimeout(function () {
      button.dataset.seizouLabel = button.innerHTML;
      button.disabled = true;
      button.textContent = "処理中…";
    }, 0);
  });
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    document.querySelectorAll("form[data-seizou-sent]").forEach(function (form) { delete form.dataset.seizouSent; });
    document.querySelectorAll("[data-seizou-label]").forEach(function (button) {
      button.innerHTML = button.dataset.seizouLabel;
      delete button.dataset.seizouLabel;
      button.disabled = false;
    });
  });
  document.addEventListener("htmx:beforeRequest", function (event) {
    var form = event.detail.elt;
    if (!form || form.tagName !== "FORM" || (event.detail.requestConfig || {}).verb === "get") return;
    form.querySelectorAll(SUBMITS).forEach(function (button) {
      if (!button.disabled) { button.disabled = true; button.dataset.seizouHeld = "1"; }
    });
  });
  document.addEventListener("htmx:afterRequest", function (event) {
    var form = event.detail.elt;
    if (!form || !form.querySelectorAll) return;
    form.querySelectorAll("[data-seizou-held]").forEach(function (button) { button.disabled = false; delete button.dataset.seizouHeld; });
  });
})();
