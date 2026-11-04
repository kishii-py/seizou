/*
 * 書きかけの離脱の確認 (決め 11-04 22:0x。入力の磨き)。読み込みは base.html の seizou-modal.js の次の 1 行 (script の最後)。
 * 素の JS で書く (外の library を使わない)。document と window に委譲して付ける。class を触らない (印は dataset だけ)。
 *
 * 決まり:
 * - 印: 入力の土台の form (form[data-enter-guard]) の欄で input か change が起きたら、form に data-seizou-dirty を付ける。
 * - 送る: 印のある form の submit で印を外す (保存へ進むので、次の画面へ移るときに聞かない)。
 * - 画面を離れる: 印のある form が画面にあるまま beforeunload が来たら、ブラウザの確認を出す
 *   (event.preventDefault() と returnValue。確認の文はブラウザが決める)。
 * - 小窓 (#seizou-modal): 中に印のある form があるまま閉じようとすると、confirm で聞く。「キャンセル」なら閉じない。
 *   閉じる道は 3 つ = 閉じるボタンと外側の押下 (どちらも method="dialog" の form の submit) と Esc (dialog の cancel)。
 *   小窓が閉じたら (保存の後の seizou:saved で seizou-modal.js が閉じたときも)、小窓の中の印を外す。
 *   htmx の差し替えで form が消えたときは、印も form と一緒に消える。
 */
(function () {
  "use strict";

  var GUARDED = "form[data-enter-guard]";
  var DIRTY = "form[data-enter-guard][data-seizou-dirty]";
  var MESSAGE = "入力した内容が消えます。閉じますか？";

  function dialog() {
    return document.getElementById("seizou-modal");
  }

  function hasDirty(root) {
    return !!(root && root.querySelector(DIRTY));
  }

  function mark(event) {
    var form = event.target && event.target.closest ? event.target.closest(GUARDED) : null;
    if (form) form.dataset.seizouDirty = "1";
  }
  document.addEventListener("input", mark);
  document.addEventListener("change", mark);

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var modal = dialog();
    if (form.method === "dialog") {
      if (modal && modal.contains(form) && hasDirty(modal) && !window.confirm(MESSAGE)) event.preventDefault();
      return;
    }
    if (form.matches(GUARDED)) delete form.dataset.seizouDirty;
  });

  // cancel と close は泡立たないので、document の capture で受ける。
  document.addEventListener(
    "cancel",
    function (event) {
      var modal = dialog();
      if (event.target !== modal || !event.cancelable || !hasDirty(modal)) return;
      if (!window.confirm(MESSAGE)) event.preventDefault();
    },
    true
  );
  document.addEventListener(
    "close",
    function (event) {
      var modal = dialog();
      if (event.target !== modal) return;
      modal.querySelectorAll(DIRTY).forEach(function (form) {
        delete form.dataset.seizouDirty;
      });
    },
    true
  );

  window.addEventListener("beforeunload", function (event) {
    if (!hasDirty(document)) return;
    event.preventDefault();
    event.returnValue = "";
  });
})();
