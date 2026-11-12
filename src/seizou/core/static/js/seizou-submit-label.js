/*
 * 保存のボタンの字を、入力のたびに押した結果の数と金額に合わせる (N138。決め 11-04 23:0x)。素の JS (外の library を使わない)。
 * 字の型は部品の form が組み (core/forms.py の SubmitLabelMixin)、ボタンの data-submit-label に JSON の並びで置く。
 * 型の置き場は {rows} (行の数)・{qty} (数量の合計)・{amount} (金額の合計)。全部を埋められる最初の型を使い、
 * どれも埋められなければ data-submit-label-plain の字 (例「出荷する」) にする。サーバーが描く初めの字と同じ決まり。
 *
 * 約束:
 * - document に委譲する (小窓の中身が htmx で差し替わっても動く)。input と change のたびに、その form のボタンを埋め直す。
 * - 数量の欄は data-label-qty、単価の欄は data-label-price (同じ行 tr の中)、「この行を出さない」は data-label-skip、
 *   金額の欄は data-label-amount。行 tr が無い form は form の全体を 1 行と読む。
 * - 一式の行は、同じ行の data-label-lump の欄の値が属性の値と同じとき (単価の単位の「式」)。その行は数量を掛けずに単価を金額に足す。
 *   core の JS は部品の値の字を書かず、属性の値と比べる (core は部品の値を持たないため)。
 * - 印を付けた行だけ数える checkbox は data-label-pick (請求書の新規登録の出荷。候補 C74)。数量は行 tr の data-label-pick-qty、
 *   金額は行 tr の data-label-pick-amount (サーバーが 1 円に丸めた行の金額。JS は掛け算をしない) の字を読む。
 *   印の無い行と、隠れた行 (tr の hidden。得意先で絞った行) は数えない。data-label-pick-amount の無い行があれば {amount} を埋めない。
 * - 数量の空の行は数えない。数でない字の数量が 1 つでもあれば、数を書かない (plain の字)。
 * - 単価の空か数でない行が 1 つでもあれば {amount} を埋めない (次の型へ)。
 * - 明細の選ぶ欄 (data-label-line) の値が描いたときの値と違えば、単位が分からないので plain の字にする。
 * - 数は全角を半角に、桁の区切りの「,」を外して読み、小数 2 桁で四捨五入して 3 桁ごとに区切る。
 * - 型の「¥{amount}」にマイナスの数が入った字「¥-660」は「-¥660」にする (画面の yen の字・サーバーが描く初めの字と同じ形。調整の記録)。
 * - ボタンの最後の字の node だけを書き換える (前の印の絵は残す)。class を触らない。取った字を HTML として差し込まない。通信しない。
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

  function readAttribute(element, name) {
    // 行の data- の属性の素の数の字 (サーバーが書く。区切りの「,」は無い)。属性が無ければ undefined、数でなければ null。
    if (!element.hasAttribute(name)) return undefined;
    var text = String(element.getAttribute(name) || "").trim();
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
      var lump = row.querySelector("[data-label-lump]");
      var once = !!lump && lump.value === lump.getAttribute("data-label-lump");
      if (typeof each === "number") amount += once ? each : count * each;
      else priced = false;
    });
    form.querySelectorAll("[data-label-pick]").forEach(function (input) {
      if (!input.checked) return;
      var row = input.closest("tr") || form;
      if (row.hidden) return;
      var count = readAttribute(row, "data-label-pick-qty");
      if (typeof count !== "number") {
        bad = true;
        return;
      }
      rows += 1;
      qty += count;
      var each = readAttribute(row, "data-label-pick-amount");
      if (typeof each === "number") amount += each;
      else priced = false;
    });
    var money = form.querySelector("[data-label-price], [data-label-pick-amount]");
    found.totals = bad
      ? null
      : { rows: String(rows), qty: format(qty), amount: priced && money ? format(amount) : null };
    if (!bad && rows > 0) {
      found.rows = String(rows);
      found.qty = format(qty);
      if (priced && money) found.amount = format(amount);
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
      if (complete) return text.replace(/¥-/g, "-¥");
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
