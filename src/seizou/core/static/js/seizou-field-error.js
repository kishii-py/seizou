/*
 * 直した欄の赤枠と誤りの字をその場で消し、止まった赤い知らせを閉じる (初見テスト R5 の V36。候補 C25 の 3 と 4。
 * 決め 11-06 21:4x C25-3 = b「止まった後に同じ画面の欄を直した時に閉じる」)。素の JS (外の library を使わない)。
 * 読み込みは base.html の script の並びの 1 行 (seizou-submit-label.js より後)。段の決まりは作品の CLAUDE.md の「知らせの強さ」。
 *
 * 約束:
 * - document の input と change に委譲する (seizou-submit-label.js と同じ。小窓の中身が htmx で差し替わっても動く)。
 *   扱うのは入力の土台の form (form[data-enter-guard]) の中の欄だけ。人が打った・選んだ時のほか、部品を選んで値を写した時
 *   (seizou-select.js) と「残の全額を入れる」を押した時 (billing/payment-fill.js) も、値が入ったので同じに扱う。
 * - 欄の赤枠 (段 1): 同じ form の中の同じ name の物の全部から aria-invalid を外す。赤枠は core/_form_card.html の欄の箱が
 *   aria-invalid=true の物に付けるので、属性を外せば消える。複数の checkbox と radio は Django が 1 つずつに付けるので全部から外す。
 * - 欄の誤りの字 (段 1): 欄の aria-describedby のうち _error で終わる id の字 (core/_form_card.html の <auto_id>_error) を、
 *   行の高さを残して見えなくする (style.visibility)。aria-describedby に _error の無い欄 (複数の checkbox など) は id_<name>_error で引く。
 *   引けなければ何もしない。説明の字 (_helptext) は消さない。行ごと消さない (打った瞬間に下の欄が 1 行分上へ動くため)。
 * - 止まった知らせ (段 2): 欄の form が小窓の箱 (#seizou-modal-box) の外なら、右上の枠 (#seizou-toasts) の中の止まった箱
 *   ([data-notice="stop"]) の閉じるボタン ([data-toast-close]) を押す (閉じる手続きは base.html の末尾の script の 1 つだけ)。
 *   小窓の箱の中なら、小窓の箱の中の止まった赤い帯 ([data-notice="stop"]。core/_notices.html) を消し、右上の知らせには触らない
 *   (小窓は右上の知らせより手前に出るため。CLAUDE.md の小窓の例外と同じ訳)。誤りの欄が 2 つ以上でも、どれか 1 つの欄を触った時点で閉じる。
 * - 触らない物: 確かめの黄の箱と帯・済んだ知らせ・押すだけの操作の赤い知らせ (どれも止まった印を持たない)。時間では閉じない。
 * - 値を確かめない。空に戻しても誤りを出し直さない (保存のときにサーバーが確かめる)。もう一度押して止まれば、サーバーが描き直す。
 * - class を触らない (classList・className を使わない)。HTML を差し込まない。通信しない。
 *   event は送らない (閉じるボタンの click だけは、今の閉じる手続きを使うために呼ぶ)。
 * - 止まった後に最初の誤りの欄へ移る (指示書 11-07 の見つけ forms-02): 読み込みの時と、小窓の箱 (#seizou-modal-box) の差し替えの後
 *   (htmx:afterSettle) に、入力の土台の form の中の最初の [aria-invalid=true] を scrollIntoView (縦は中央) してから focus (preventScroll)。
 *   字を打って探す欄 (Tom Select) は元の select が隠れているので、同じ id に「-ts-control」を付けた打つ欄へ移す。誤りの欄が無ければ何もしない。
 *   下の保存の帯に隠れない分は scripts/css/input.css の scroll-padding-bottom が見る。
 */
(function () {
  "use strict";

  var FORM = "form[data-enter-guard]";
  var MODAL_BOX = "#seizou-modal-box";
  var MODAL_STOP = '[data-notice="stop"]';
  var TOAST_STOP_CLOSE = '#seizou-toasts [data-notice="stop"] [data-toast-close]';
  var ERROR_ID = /_error$/;

  // 欄の誤りの字 (<p id="<auto_id>_error">) を、欄と同じ form の中から引く。無ければ null。
  function errorLine(form, field) {
    var ids = (field.getAttribute("aria-describedby") || "").split(/\s+/);
    var id = "";
    for (var i = 0; i < ids.length; i++) {
      if (ERROR_ID.test(ids[i])) {
        id = ids[i];
        break;
      }
    }
    if (!id && field.name) id = "id_" + field.name + "_error";
    return id ? form.querySelector("#" + CSS.escape(id)) : null;
  }

  // 欄の赤枠を外し、誤りの字を高さを残して見えなくする。
  function clearField(form, field) {
    var name = field.getAttribute("name");
    if (name) {
      var elements = form.elements;
      for (var i = 0; i < elements.length; i++) {
        if (elements[i].getAttribute("name") === name) elements[i].removeAttribute("aria-invalid");
      }
    } else {
      field.removeAttribute("aria-invalid");
    }
    var line = errorLine(form, field);
    if (line) line.style.visibility = "hidden";
  }

  // 止まった知らせを閉じる。小窓の中の form なら小窓の赤い帯だけ、画面の全体なら右上の止まった箱だけ。
  function closeStop(form) {
    var box = document.querySelector(MODAL_BOX);
    if (box && box.contains(form)) {
      box.querySelectorAll(MODAL_STOP).forEach(function (band) {
        band.remove();
      });
      return;
    }
    document.querySelectorAll(TOAST_STOP_CLOSE).forEach(function (button) {
      button.click();
    });
  }

  function edited(event) {
    var field = event.target;
    if (!(field instanceof Element)) return;
    var form = field.closest(FORM);
    if (!form) return;
    clearField(form, field);
    closeStop(form);
  }

  document.addEventListener("input", edited);
  document.addEventListener("change", edited);

  // 誤りの欄の focus の先。字を打って探す欄 (Tom Select) は、同じ id に -ts-control を付けた打つ欄 (元の select は隠れている)。
  function focusTarget(field) {
    if (field.id) {
      var control = document.getElementById(field.id + "-ts-control");
      if (control) return control;
    }
    return field;
  }

  // root の中の最初の誤りの欄へ移る (見える所まで送ってから focus)。誤りの欄が無ければ何もしない。
  function focusFirstInvalid(root) {
    var field = root.querySelector(FORM + " [aria-invalid=true]");
    if (!field) return;
    var target = focusTarget(field);
    if (typeof target.scrollIntoView === "function") target.scrollIntoView({ block: "center", inline: "nearest" });
    if (typeof target.focus === "function") target.focus({ preventScroll: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      focusFirstInvalid(document);
    });
  } else {
    focusFirstInvalid(document);
  }

  document.addEventListener("htmx:afterSettle", function (event) {
    // 小窓の箱の差し替え (保存で止まって小窓の中身が描き直された時) だけ。ほかの差し替え (一覧の行など) では動かない。
    var box = document.querySelector(MODAL_BOX);
    var elt = event.detail && event.detail.elt;
    if (!box || !elt || !box.contains(elt)) return;
    focusFirstInvalid(box);
  });
})();
