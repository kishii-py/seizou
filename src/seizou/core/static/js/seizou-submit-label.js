/*
 * 保存の釦の字を、入力のたびに押した結果の数と金額に合わせる (N138。決め 11-04 23:0x)。素の JS (外の library を使わない)。
 * 字の型は部品の form が組み (core/forms.py の SubmitLabelMixin)、釦の data-submit-label に JSON の並びで置く。
 * 型の置き場は {rows} (行の数)・{qty} (数量の合計)・{amount} (金額の合計)。全部を埋められる最初の型を使い、
 * どれも埋められなければ data-submit-label-plain の字 (例「出荷する」) にする。サーバーが描く初めの字と同じ決まり。
 *
 * 約束:
 * - document に委譲する (小窓の中身が htmx で差し替わっても動く)。input と change のたびに、その form の釦を埋め直す。
 * - 数量の欄は data-label-qty、単価の欄は data-label-price (同じ行 tr の中)、「この行を出さない」は data-label-skip、
 *   金額の欄は data-label-amount。行 tr が無い form は form の全体を 1 行と読む。
 * - 数量の空の行は数えない。数でない字の数量が 1 つでもあれば、数を書かない (plain の字)。
 * - 単価の空か数でない行が 1 つでもあれば {amount} を埋めない (次の型へ)。
 * - 明細の選ぶ欄 (data-label-line) の値が描いたときの値と違えば、単位が分からないので plain の字にする。
 * - 数は全角を半角に、桁の区切りの「,」を外して読み、小数 2 桁で四捨五入して 3 桁ごとに区切る。
 * - 釦の最後の字の node だけを書き換える (前の印の絵は残す)。class を触らない。取った字を HTML として差し込まない。通信しない。
 * - 明細の合計の枠 (core/_line_totals.html の data-line-totals) があれば、同じ数えで rows・qty・amount の字 (data-line-totals-rows・-qty・-amount) を埋め直す (N149)。
 *   0 行でも数を書く。数でない数量があれば 3 つとも「—」。単価の空か数でない行があれば amount は「—」。金額の組が無い枠 (金額の閲覧が無い人・一括出荷) には書かない。
 */
(function () {
  "use strict";

  var SLOT = /\{(rows|qty|amount)\}/g;

  function readNumber(input) {
    var text = String(input.value || "").normalize("NFKC").replace(/,/g, "").trim();
    if (text === "") return undefined;
    if (!/^-?\d+(\.\d+)?$/.test(text)) return null;
    return Number(text);
  }

  function format(value) {
    // 小数 2 桁の四捨五入は字の指数で行う (1.005 を 1.01 にする。サーバーの ROUND_HALF_UP と同じ字にするため)。
    var rounded = Number(Math.round(Number(value + "e2")) + "e-2");
    if (!isFinite(rounded)) rounded = Math.round(value * 100) / 100;
    return rounded.toLocaleString("ja-JP", { maximumFractionDigits: 2 });
  }

  function values(form) {
    var found = {};
    var rows = 0;
    var qty = 0;
    var amount = 0;
    var priced = true;
    var bad = false;
    form.querySelectorAll("[data-label-qty]").forEach(function (input) {
      var row = input.closest("tr") || form;
      var skip = row.querySelector("[data-label-skip]");
      if (skip && skip.checked) return;
      var count = readNumber(input);
      if (count === undefined) return;
      if (count === null) {
        bad = true;
        return;
      }
      rows += 1;
      qty += count;
      var price = row.querySelector("[data-label-price]");
      if (!price) return;
      var each = readNumber(price);
      if (typeof each === "number") amount += count * each;
      else priced = false;
    });
    found.totals = bad
      ? null
      : { rows: String(rows), qty: format(qty), amount: priced && form.querySelector("[data-label-price]") ? format(amount) : null };
    if (!bad && rows > 0) {
      found.rows = String(rows);
      found.qty = format(qty);
      if (priced && form.querySelector("[data-label-price]")) found.amount = format(amount);
    }
    var direct = form.querySelector("[data-label-amount]");
    if (direct) {
      var paid = readNumber(direct);
      if (typeof paid === "number") found.amount = format(paid);
    }
    return found;
  }

  function fill(templates, found) {
    for (var i = 0; i < templates.length; i++) {
      var complete = true;
      var text = String(templates[i]).replace(SLOT, function (all, name) {
        if (!found[name]) complete = false;
        return found[name] || "";
      });
      if (complete) return text;
    }
    return null;
  }

  function setText(button, text) {
    var node = button.lastChild;
    if (text && node && node.nodeType === 3) node.nodeValue = text;
  }

  function setNode(element, text) {
    var node = element && element.firstChild;
    if (node && node.nodeType === 3) node.nodeValue = text;
  }

  function setTotals(form, totals) {
    var box = form.querySelector("[data-line-totals]");
    if (!box) return;
    setNode(box.querySelector("[data-line-totals-rows]"), totals ? totals.rows : "—");
    setNode(box.querySelector("[data-line-totals-qty]"), totals ? totals.qty : "—");
    setNode(box.querySelector("[data-line-totals-amount]"), totals && totals.amount ? totals.amount : "—");
  }

  function update(form) {
    var found = values(form);
    setTotals(form, found.totals);
    var button = form.querySelector("button[data-submit-label]");
    if (!button) return;
    var templates;
    try {
      templates = JSON.parse(button.getAttribute("data-submit-label"));
    } catch (error) {
      return;
    }
    if (!Array.isArray(templates)) return;
    var line = form.querySelector("[data-label-line]");
    var text = null;
    if (!line || line.value === line.getAttribute("data-label-line")) text = fill(templates, found);
    setText(button, text || button.getAttribute("data-submit-label-plain"));
  }

  function onInput(event) {
    var target = event.target;
    if (!(target instanceof Element)) return;
    var form = target.closest("form");
    if (form) update(form);
  }

  document.addEventListener("input", onInput);
  document.addEventListener("change", onInput);
})();
