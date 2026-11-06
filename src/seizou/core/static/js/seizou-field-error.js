/*
 * 直した欄の赤枠と誤りの字をその場で消す (初見テスト R5 の V36。候補 C25 の 3。作品の CLAUDE.md の段 1「欄を直すとその場で消える」)。
 * 素の JS (外の library を使わない)。
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
 * - 値を確かめない。空に戻しても誤りを出し直さない (保存のときにサーバーが確かめる)。もう一度押して止まれば、サーバーが描き直す。
 * - class を触らない (classList・className を使わない)。HTML を差し込まない。通信しない。event は送らない。
 */
(function () {
  "use strict";

  var FORM = "form[data-enter-guard]";
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

  function edited(event) {
    var field = event.target;
    if (!(field instanceof Element)) return;
    var form = field.closest(FORM);
    if (!form) return;
    clearField(form, field);
  }

  document.addEventListener("input", edited);
  document.addEventListener("change", edited);
})();
