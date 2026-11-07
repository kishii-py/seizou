/*
 * 一覧の表の箱の端のぼかしの印 (初見テスト R5 の候補 C34 の右の端 = C34-1 = a と、候補 C23 の下の端 = 決め C23-2 = a)。
 * 読み込みは base.html の script の並びの最後の 1 行。素の JS で書く (外の library を使わない。htmx は読まない)。
 *
 * markup の約束 (core/_rows.html と、その写しの orders/_rows.html・orders/_board_rows.html・connect/_rows.html と、
 * 横断の検索の core/search.html・進捗の盤の progress/progress_board.html):
 *   <div>                                              包み。ぼかしは scripts/css/input.css がこの包みの上に重ねる
 *                                                      (右の端は ::after、下の端は ::before)
 *     <div class="max-h-[70vh] overflow-auto ..." data-scroll-hint>   表の箱 (送る物)
 *       <table ...>
 *
 * 決まり:
 * - 印 [data-scroll-hint] の箱ごとに、右に続きがあるとき (scrollLeft + clientWidth < scrollWidth - 1) だけ、
 *   包み (箱の親) に data-more-right を付け、無ければ外す。右の端まで送ると外れ、左へ戻すと付き直す。溢れの無い幅では付かない。
 * - 同じ箱で、下に続きがあるとき (scrollTop + clientHeight < scrollHeight - 1) だけ、包みに data-more-below を付け、
 *   無ければ外す (決め C23-2 = a)。下の端まで送ると外れ、上へ戻すと付き直す。溢れの無い高さでは付かない。
 * - 右と下は別々に比べ、変わった方の属性だけを書く (右が前と同じでも、下は取り直す)。
 * - 取り直す時: 読み込み (DOMContentLoaded)・箱の中の送り (scroll は泡立たないので、document の capture の段で受ける。
 *   縦に送っても同じ出来事が来る)・窓の大きさの変化 (window の resize)・htmx が中身を差し替えた後 (htmx:afterSettle。
 *   並び替え・絞り・ページ送り・小窓で保存した後の取り直し・受信した注文の一覧で行が足された後・進捗の盤の 10 秒ごとの更新)。
 * - 触るのは包みの data の属性だけ。class を触らない (R19)。取った字を HTML として差し込まない。通信しない。
 */
(function () {
  "use strict";

  var BOX = "[data-scroll-hint]";
  var MORE_RIGHT = "data-more-right";
  var MORE_BELOW = "data-more-below";

  // 包みの属性 name を、続きがあるか (more) に合わせる。同じ状態のときは書かない (送るたびに属性を書き直さない)
  function setMore(wrap, name, more) {
    if (more === wrap.hasAttribute(name)) return;
    if (more) {
      wrap.setAttribute(name, "");
    } else {
      wrap.removeAttribute(name);
    }
  }

  function mark(box) {
    var wrap = box.parentElement;
    if (!wrap) return;
    setMore(wrap, MORE_RIGHT, box.scrollLeft + box.clientWidth < box.scrollWidth - 1);
    setMore(wrap, MORE_BELOW, box.scrollTop + box.clientHeight < box.scrollHeight - 1);
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
