/*
 * 注文残の盤の「選んだ行の操作」の帯 (orders/orderline_list.html の #board-bulk)。
 * 行の頭のチェック [data-board-pick] の数を [data-board-count] に書き、1 行も選んでいなければ帯を隠しの属性で隠す。
 * [data-board-all] は今のページの行のチェックを全部同じにする。
 * htmx で #rows が差し替わると選んだ状態は消える (それでよい = 決め 11-04 17:4x の範囲)。差し替えの後に数え直す。
 * 触るのは隠しの属性・textContent・checked だけ (class を触らない = R19)。
 */
(function () {
  "use strict";

  function count() {
    var band = document.querySelector("[data-board-bulk]");
    if (!band) return;
    var picked = document.querySelectorAll("[data-board-pick]:checked").length;
    var label = band.querySelector("[data-board-count]");
    if (label) label.textContent = String(picked);
    band.hidden = picked === 0;
    var all = document.querySelector("[data-board-all]");
    var boxes = document.querySelectorAll("[data-board-pick]");
    if (all) all.checked = boxes.length > 0 && picked === boxes.length;
  }

  document.addEventListener("change", function (event) {
    var target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.matches("[data-board-all]")) {
      document.querySelectorAll("[data-board-pick]").forEach(function (box) {
        box.checked = target.checked;
      });
      count();
    } else if (target.matches("[data-board-pick]")) {
      count();
    }
  });
  document.addEventListener("htmx:afterSettle", count);
  document.addEventListener("DOMContentLoaded", count);
})();
