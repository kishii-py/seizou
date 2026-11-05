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
 * - 保存の帯 (core/_save_bar.html の data-save-bar。N154。案 5-B): 画面の全体の入力 (小窓の外) に印のある form がある間だけ出す。
 *   出し入れは帯の hidden の属性だけ (syncBar。印を付ける所と外す所で呼ぶ = 書きかけの判定は印の 1 か所)。
 *   帯の「保存」は、その form の保存のボタン (type="submit" の最後の物 = Ctrl+Enter と同じ) を押す。
 *   帯の「破棄」は MESSAGE で聞き、OK なら印を外して同じ URL を GET で開き直す (止まった POST を送り直さない。離れるときの確認も出ない)。
 */
(function () {
  "use strict";

  var GUARDED = "form[data-enter-guard]";
  var DIRTY = "form[data-enter-guard][data-seizou-dirty]";
  var MESSAGE = "入力した内容が消えます。閉じますか？";
  var BAR = "[data-save-bar]";

  function dialog() {
    return document.getElementById("seizou-modal");
  }

  function hasDirty(root) {
    return !!(root && root.querySelector(DIRTY));
  }

  // 画面の全体の入力 (小窓の外) のうち、印のある form (無ければ null)。保存の帯はこの form のためだけに出る。
  function dirtyPageForm() {
    var modal = dialog();
    var forms = document.querySelectorAll(DIRTY);
    for (var i = 0; i < forms.length; i++) {
      if (!modal || !modal.contains(forms[i])) return forms[i];
    }
    return null;
  }

  // 保存の帯 (N154) の出し入れ。印を読むだけ (数えない)。帯の無い画面 (小窓だけの要求・黄の知らせの後) では何もしない。
  function syncBar() {
    var bar = document.querySelector(BAR);
    if (bar) bar.hidden = !dirtyPageForm();
  }

  function mark(event) {
    var form = event.target && event.target.closest ? event.target.closest(GUARDED) : null;
    if (form) form.dataset.seizouDirty = "1";
    if (form) syncBar();
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
    syncBar();
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

  // 保存の帯のボタン (N154)。「保存」は Ctrl+Enter と同じ (保存のボタン = type="submit" の最後の物を押す。押したボタンが submitter になる)。
  // 「破棄」は MESSAGE で聞き、OK なら印を外して同じ URL を GET で開き直す (reload は止まった POST を送り直すので使わない)。
  document.addEventListener("click", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    var button = target ? target.closest("[data-save-bar-save], [data-save-bar-discard]") : null;
    var form = button ? dirtyPageForm() : null;
    if (!form) return;
    if (button.hasAttribute("data-save-bar-save")) {
      var submits = form.querySelectorAll('button[type="submit"]');
      var save = submits[submits.length - 1];
      if (save && !save.disabled) save.click();
      return;
    }
    if (!window.confirm(MESSAGE)) return;
    delete form.dataset.seizouDirty;
    syncBar();
    window.location.assign(window.location.pathname + window.location.search);
  });

  window.addEventListener("beforeunload", function (event) {
    if (!hasDirty(document)) return;
    event.preventDefault();
    event.returnValue = "";
  });
})();
