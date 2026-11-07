/*
 * 受注の入力の「行を足す」(orders/order_form.html)。素の JS。
 * <template id="order-line-template"> の空の行を写し、__prefix__ を次の番号に替えて表の末尾に足す。
 * formset の管理の欄 (lines-TOTAL_FORMS) を 1 つ増やし、足した行の選ぶ欄に seizou-select.js を載せる。
 * 行を消すのは「消す」の印 (formset の DELETE) で、保存のときに services が明細を外す。
 * 明細の行の金額 (数量 × 単価) を、数量か単価の欄が変わるたびに data-line-amount の枡に出す
 * (保存の前に見るための目安。保存の値は services が得意先の端数の扱いで丸める = R03)。字は textContent で出す。
 * 単価の単位が式の行は数量を掛けない (同じ行の単価の単位の選ぶ欄の値が、その欄の属性 data-label-lump の値と同じとき)。
 * 明細 1 行の会社 (「行を足す」のボタンが無い) でも読む。ボタンが無ければ「行を足す」の動きは何もしない。
 */
(function () {
  "use strict";

  var PREFIX = "lines";
  var ATTRS = ["name", "id", "for", "aria-describedby"];

  function addLine(button) {
    var form = button.closest("form");
    if (!form) return;
    var template = form.querySelector("#order-line-template");
    var body = form.querySelector("#order-lines");
    var total = form.querySelector('input[name="' + PREFIX + '-TOTAL_FORMS"]');
    var max = form.querySelector('input[name="' + PREFIX + '-MAX_NUM_FORMS"]');
    if (!template || !body || !total) return;
    var index = parseInt(total.value, 10);
    if (isNaN(index)) return;
    if (max && index >= parseInt(max.value, 10)) return;

    var fragment = template.content.cloneNode(true);
    fragment.querySelectorAll("*").forEach(function (element) {
      ATTRS.forEach(function (attr) {
        var value = element.getAttribute(attr);
        if (value && value.indexOf("__prefix__") !== -1) {
          element.setAttribute(attr, value.split("__prefix__").join(String(index)));
        }
      });
    });
    var row = fragment.querySelector("[data-order-line]");
    body.appendChild(fragment);
    total.value = String(index + 1);
    if (row && window.seizouSelect) window.seizouSelect.init(row);
    var first = row && row.querySelector("select, input:not([type=hidden])");
    if (first) first.focus();
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-add-order-line]");
    if (!button) return;
    event.preventDefault();
    addLine(button);
  });

  // 数の欄の字 (全角の数字と桁の区切りの「,」も受ける。数の欄の normalize_number と同じそろえ方)。数でなければ null。
  function parseNumber(text) {
    var value = String(text).normalize("NFKC").replace(/,/g, "").trim();
    return /^\d+(\.\d+)?$/.test(value) ? Number(value) : null;
  }

  function updateAmount(row) {
    var cell = row.querySelector("[data-line-amount]");
    if (!cell) return;
    var qty = row.querySelector('input[name$="-qty"]');
    var price = row.querySelector('input[name$="-unit_price"]');
    var basis = row.querySelector('select[name$="-price_basis"]');
    var lump = !!basis && basis.value === basis.getAttribute("data-label-lump");
    var qtyValue = qty ? parseNumber(qty.value) : null;
    var priceValue = price ? parseNumber(price.value) : null;
    if (qtyValue === null || priceValue === null) {
      cell.textContent = "";
      return;
    }
    var amount = Math.round((lump ? priceValue : qtyValue * priceValue) * 100) / 100;
    // 金額の頭に半角の「¥」(U+00A5。保存のボタンの字の型と同じ字)。style: "currency" は全角の円の記号 (U+FFE5) を出すので使わない。
    cell.textContent = "¥" + amount.toLocaleString("ja-JP", { maximumFractionDigits: 2 });
  }

  function onLineEdit(event) {
    var target = event.target;
    var row = target && target.closest ? target.closest("[data-order-line]") : null;
    if (row) updateAmount(row);
  }

  document.addEventListener("input", onLineEdit);
  document.addEventListener("change", onLineEdit);
  // 編集の画面の既にある明細の金額を、読み込みの時に 1 回出す。「行を足す」で足した行は、打ったときに出る。
  document.querySelectorAll("[data-order-line]").forEach(updateAmount);
})();
