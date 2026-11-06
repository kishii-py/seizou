/*
 * 一覧の表の箱の端のぼかしの印 (初見テスト R5 の候補 C34。C34-1 = a)。読み込みは base.html の script の並びの最後の 1 行。
 * 素の JS で書く (外の library を使わない。htmx は読まない)。
 *
 * markup の約束 (core/_rows.html と、その写しの orders/_rows.html・orders/_board_rows.html・connect/_rows.html):
 *   <div>                                              包み。ぼかしは scripts/css/input.css がこの包みの ::after で箱の上に重ねる
 *     <div class="max-h-[70vh] overflow-auto ..." data-scroll-hint>   表の箱 (送る物)
 *       <table ...>
 *
 * 決まり:
 * - 印 [data-scroll-hint] の箱ごとに、右に続きがあるとき (scrollLeft + clientWidth < scrollWidth - 1) だけ、
 *   包み (箱の親) に data-more-right を付け、無ければ外す。右の端まで送ると外れ、左へ戻すと付き直す。溢れの無い幅では付かない。
 * - 取り直す時: 読み込み (DOMContentLoaded)・箱の中の送り (scroll は泡立たないので、document の capture の段で受ける)・
 *   窓の大きさの変化 (window の resize)・htmx が中身を差し替えた後 (htmx:afterSettle。並び替え・絞り・ページ送り・
 *   小窓で保存した後の取り直し・受信した注文の一覧で行が足された後)。
 * - 箱の下の端のぼかし (C23-2 = a) は、同じ関数 (mark) で scrollTop・scrollHeight・clientHeight を見て、
 *   別の data の属性を包みに付ける形で足す (この版は右の端だけ)。
 * - 触るのは包みの data の属性だけ。class を触らない (R19)。取った字を HTML として差し込まない。通信しない。
 */
(function () {
  "use strict";

  var BOX = "[data-scroll-hint]";
  var MORE_RIGHT = "data-more-right";

  function mark(box) {
    var wrap = box.parentElement;
    if (!wrap) return;
    var more = box.scrollLeft + box.clientWidth < box.scrollWidth - 1;
    // 同じ状態のときは書かない (送るたびに属性を書き直さない)
    if (more === wrap.hasAttribute(MORE_RIGHT)) return;
    if (more) {
      wrap.setAttribute(MORE_RIGHT, "");
    } else {
      wrap.removeAttribute(MORE_RIGHT);
    }
  }

  function markAll() {
    var boxes = document.querySelectorAll(BOX);
    for (var i = 0; i < boxes.length; i++) mark(boxes[i]);
  }

  document.addEventListener(
    "scroll",
    function (event) {
      var target = event.target;
      if (target instanceof Element && target.matches(BOX)) mark(target);
    },
    true
  );
  window.addEventListener("resize", markAll);
  document.addEventListener("htmx:afterSettle", markAll);
  document.addEventListener("DOMContentLoaded", markAll);
})();
