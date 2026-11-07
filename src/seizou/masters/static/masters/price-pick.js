/*
 * 単価表から選ぶ小窓 (C100。決め PRICE-0・PRICE-5)。読み込みは masters/_price_pick_dialog.html の 1 行。素の JS で書く (外の library は同梱の htmx だけ)。
 *
 * 決まり:
 * - 押せる形: 明細の行の「単価表から選ぶ」(button[data-price-pick]) は、その行の部品が選ばれるまで押せない形 (disabled)。
 *   部品の欄 (select[name$="-item"]) の change で、同じ行のボタンを部品の値が空なら押せない形、ほかは押せる形にする。初めの形はサーバーが描く。
 * - 開く: ボタンを押すと、ボタンの data-price-pick (部品の pk が 0 の道) の「/0/prices/pick/」を部品の pk に替え、
 *   kind (data-price-kind)・partner (data-price-partner-field の名前の欄)・on (data-price-date-field の名前の欄)・qty (同じ行の数量) を付けて、
 *   htmx で #price-pick-box に読み、#price-pick を開く (入力の小窓 #seizou-modal と別の枠。入れかけの発注を消さない)。開いた行を覚える。
 * - 選ぶ: 小窓の「選ぶ」(button[data-price-choice]) を押すと、覚えた行の単価・単価の単位・見積の番号 (発注は工程も) の欄に写し、
 *   input と change の event を送り (金額の目安・合計の枠・保存のボタンの字・書きかけの印が拾う)、小窓を閉じて単価の欄へカーソルを移す。
 *   写した単価と工程からは部品から写した印 (data-copied) を外す (部品を選び直しても標準単価や工程の最初の値で上書きしない)。
 *   行に無い欄は飛ばす。select に入れるのは、その値の option があるときだけ。
 * - 字は value だけを入れる。知らせの字は作らない。class を組み立てない (R19)。取った字を HTML として差し込まない。
 * - 2 回読まれても 1 回だけ動く (発注の入力は小窓で開くたびに htmx が中身の script を動かすため)。
 */
(function () {
  "use strict";

  if (window.seizouPricePick) return;
  window.seizouPricePick = true;

  var OPENER = "button[data-price-pick]";
  var CHOICE = "button[data-price-choice]";
  var ITEM = 'select[name$="-item"]';
  var openedRow = null;

  function dialog() {
    return document.getElementById("price-pick");
  }

  function fieldValue(form, name) {
    if (!form || !name) return "";
    var field = form.querySelector('[name="' + name + '"]');
    return field ? field.value : "";
  }

  // 部品の欄が変わったら、同じ行の「単価表から選ぶ」を押せる形か押せない形にする (Tom Select は選ぶと元の select に change を出す)。
  document.addEventListener("change", function (event) {
    var select = event.target;
    if (!select.matches || !select.matches(ITEM)) return;
    var row = select.closest("tr");
    var button = row ? row.querySelector(OPENER) : null;
    if (button) button.disabled = select.value === "";
  });

  function open(button) {
    var row = button.closest("tr");
    var item = row ? row.querySelector(ITEM) : null;
    if (!item || item.value === "" || !window.htmx) return;
    var form = button.closest("form");
    var qty = row.querySelector('input[name$="-qty"]');
    var params = new URLSearchParams();
    params.set("kind", button.getAttribute("data-price-kind") || "");
    params.set("partner", fieldValue(form, button.getAttribute("data-price-partner-field")));
    params.set("on", fieldValue(form, button.getAttribute("data-price-date-field")));
    params.set("qty", qty ? qty.value : "");
    var url = button.getAttribute("data-price-pick").replace("/0/prices/pick/", "/" + encodeURIComponent(item.value) + "/prices/pick/");
    openedRow = row;
    window.htmx.ajax("GET", url + "?" + params.toString(), { target: "#price-pick-box" }).then(function () {
      var modal = dialog();
      if (modal && typeof modal.showModal === "function" && !modal.open) modal.showModal();
    });
  }

  function hasOption(select, value) {
    return Array.prototype.some.call(select.options, function (each) {
      return each.value === value;
    });
  }

  function send(field) {
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function choose(choice) {
    var row = openedRow;
    if (!row || !row.isConnected) return;
    var price = row.querySelector('input[name$="-unit_price"]');
    if (price) {
      var value = choice.getAttribute("data-unit-price") || "";
      if (price.hasAttribute("data-number") && window.seizouNumber) value = window.seizouNumber.format(value);
      price.value = value;
      price.removeAttribute("data-copied");
      send(price);
    }
    var basis = row.querySelector('select[name$="-price_basis"]');
    var basisValue = choice.getAttribute("data-price-basis") || "";
    if (basis && hasOption(basis, basisValue)) {
      basis.value = basisValue;
      send(basis);
    }
    var quote = row.querySelector('input[name$="-quote_no"]');
    if (quote) {
      quote.value = choice.getAttribute("data-quote-no") || "";
      send(quote);
    }
    if (choice.hasAttribute("data-process")) {
      var process = row.querySelector('select[name$="-process"]');
      var processValue = choice.getAttribute("data-process") || "";
      if (process && hasOption(process, processValue)) {
        process.value = processValue;
        process.removeAttribute("data-copied");
        send(process);
      }
    }
    var modal = dialog();
    if (modal && modal.open) modal.close();
    if (price) price.focus();
  }

  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0) return;
    var target = event.target.closest ? event.target : null;
    if (!target) return;
    var opener = target.closest(OPENER);
    if (opener) {
      event.preventDefault();
      if (!opener.disabled) open(opener);
      return;
    }
    var choice = target.closest(CHOICE);
    if (choice && choice.closest("#price-pick-box")) {
      event.preventDefault();
      choose(choice);
    }
  });
})();
