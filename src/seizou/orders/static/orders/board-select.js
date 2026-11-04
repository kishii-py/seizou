/*
 * 生産管理ボード (注文残の盤) の行の選択と「選んだ行の操作」の帯 (orders/orderline_list.html の #board-bulk)。
 * 行の押下で選ぶ (決め 11-04 22:4x。行の頭のチェックは見せない。送る値と数え方は N124 のまま)。
 * - 行 tr[data-row-menu] の、link・釦・入力の欄・⋮ のメニューの外を押すと、その行の選ぶ欄 [data-board-pick] の checked を
 *   切り替えて change を出す。選ぶ欄の無い行 (取消の受注と残の無い明細) は、押しても何もしない。
 * - Shift を押しながら押すと、前に押した行から押した行までを、押した行と同じ状態にする。
 * - ⋮ か右クリックのメニューが開いている間の押下は、メニューを閉じるだけにする (行を切り替えない)。
 *   core の js/seizou-row-menu.js が document の泡立ちの段でメニューを閉じるので、ここは capture の段で先に見る。
 * - 字をなぞって選んだ押下は、行を切り替えない (品番などを写すため)。
 * - 選んだ行には data-tone="primary" を付け、外した行からは消す (テンプレートの行の固定の class が、主色の薄い地にする)。
 * - 帯の [data-board-all] は今のページの行を全部選び、[data-board-none] は全部外す。
 * - [data-board-count] に選んだ数を書き、1 行も選んでいなければ帯を隠しの属性で隠す。
 * - htmx で #rows が差し替わると選んだ状態は消える (N124 のまま)。差し替えの後に数え直す。
 * - 右クリックの事件は聞かない (メニューの動きは core の js/seizou-row-menu.js のまま)。
 * - 触るのは隠しの属性・textContent・checked・data-tone だけ (class を触らない = R19)。
 */
(function () {
  "use strict";

  var ROW = "tr[data-row-menu]";
  var PICK = "[data-board-pick]";
  // 押しても行を切り替えない物 (link・釦・入力の欄・⋮ のメニュー)。
  var OWN = "a, button, input, select, textarea, label, summary, details";
  var MENU_OPEN = "details.row-menu[open]";
  var anchor = null; // 前に押した行の選ぶ欄 (Shift の範囲の起点)

  function elementOf(node) {
    return node && node.nodeType === 1 ? node : null;
  }

  function paint(box) {
    var row = box.closest(ROW);
    if (!row) return;
    if (box.checked) row.dataset.tone = "primary";
    else delete row.dataset.tone;
  }

  function count() {
    var boxes = document.querySelectorAll(PICK);
    var picked = 0;
    boxes.forEach(function (box) {
      paint(box);
      if (box.checked) picked += 1;
    });
    var band = document.querySelector("[data-board-bulk]");
    if (!band) return;
    var label = band.querySelector("[data-board-count]");
    if (label) label.textContent = String(picked);
    band.hidden = picked === 0;
  }

  function set(box, checked) {
    if (box.checked === checked) return;
    box.checked = checked;
    box.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function setAll(checked) {
    document.querySelectorAll(PICK).forEach(function (box) {
      set(box, checked);
    });
    anchor = null;
  }

  function pickRow(row, event) {
    var box = row.querySelector(PICK);
    if (!box) return;
    var checked = !box.checked;
    if (event.shiftKey && anchor && anchor !== box && document.contains(anchor)) {
      var boxes = Array.prototype.slice.call(document.querySelectorAll(PICK));
      var from = boxes.indexOf(anchor);
      var to = boxes.indexOf(box);
      if (from > to) {
        var swap = from;
        from = to;
        to = swap;
      }
      for (var i = from; i <= to; i++) set(boxes[i], checked);
    } else {
      set(box, checked);
    }
    anchor = box;
  }

  function onClick(event) {
    var target = elementOf(event.target);
    if (!target) return;
    if (target.closest("[data-board-all]")) {
      setAll(true);
      return;
    }
    if (target.closest("[data-board-none]")) {
      setAll(false);
      return;
    }
    var row = target.closest(ROW);
    if (!row || target.closest(OWN)) return;
    if (document.querySelector(MENU_OPEN)) return;
    var selection = window.getSelection();
    if (!event.shiftKey && selection && !selection.isCollapsed) return;
    pickRow(row, event);
  }

  // Shift を押しながらの押下で、行の字が範囲で選ばれないようにする。
  function onMouseDown(event) {
    if (!event.shiftKey) return;
    var target = elementOf(event.target);
    if (target && target.closest(ROW) && !target.closest(OWN)) event.preventDefault();
  }

  document.addEventListener("change", function (event) {
    var target = event.target;
    if (target instanceof HTMLInputElement && target.matches(PICK)) count();
  });
  document.addEventListener("click", onClick, true);
  document.addEventListener("mousedown", onMouseDown);
  document.addEventListener("htmx:afterSettle", function () {
    anchor = null;
    count();
  });
  document.addEventListener("DOMContentLoaded", count);
})();
