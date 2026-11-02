/*
 * 受注の入力の「行を足す」(orders/order_form.html)。素の JS。
 * <template id="order-line-template"> の空の行を写し、__prefix__ を次の番号に替えて表の末尾に足す。
 * formset の管理の欄 (lines-TOTAL_FORMS) を 1 つ増やし、足した行の選ぶ欄に seizou-select.js を載せる。
 * 行を消すのは「消す」の印 (formset の DELETE) で、保存のときに services が明細を外す。
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
})();
