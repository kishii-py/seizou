/*
 * 一覧の表の箱の「右に続きがある」印 (初見テスト R5 の候補 C34 = C34-1 = a。候補 C23 の下の端は決め C23-2 = a)。
 * 読み込みは base.html の script の並びの最後の 1 行。素の JS で書く (外の library を使わない。htmx は読まない)。
 *
 * 形 (本人の決め 11-07 18:4x「他の部分はフラットなのに影があると統一感がない」→ 19:1x「進めて」): 右に続きがあるときは、箱の右の端の
 * 縦の中央に、白い丸の中の「→」のボタン (枠線だけ。影なし) を出し、押すと 1 画面分 (幅の 8 割) 右へ送る。右の端まで送ると消える。
 * 下に続きがあるかは、箱のいつも見えるスクロールのバー (input.css の ::-webkit-scrollbar) で分かるので、印を重ねない
 * (前の形 = 右の端と下の端の灰のぼかし。合計の行にまで影が掛かった)。
 *
 * markup の約束 (core/_rows.html と、その写しの orders/_rows.html・orders/_board_rows.html・connect/_rows.html と、
 * 横断の検索の core/search.html・進捗の盤の progress/progress_board.html):
 *   <div>                                              包み。この JS が「→」のボタン (button[data-scroll-more]) を箱の後ろに足す。
 *                                                      見せ方は scripts/css/input.css (包みに data-more-right がある間だけ出る)
 *     <div class="max-h-[70vh] overflow-auto ..." data-scroll-hint>   表の箱 (送る物)
 *       <table ...>
 *
 * 決まり:
 * - 印 [data-scroll-hint] の箱ごとに、右に続きがあるとき (scrollLeft + clientWidth < scrollWidth - 1) だけ、
 *   包み (箱の親) に data-more-right を付け、無ければ外す。右の端まで送ると外れ、左へ戻すと付き直す。溢れの無い幅では付かない。
 * - 同じ箱で、下に続きがあるとき (scrollTop + clientHeight < scrollHeight - 1) だけ、包みに data-more-below を付け、
 *   無ければ外す (決め C23-2 = a。今は CSS が使わない印。盤の見た目を足すときの口として残す)。
 * - 右と下は別々に比べ、変わった方の属性だけを書く (右が前と同じでも、下は取り直す)。
 * - 「→」のボタンは包みに 1 つだけ作る (createElement。中の絵は seizou_icons の chevron-right と同じ線を createElementNS で描く)。
 *   押すと箱を scrollBy で右へ送る (smooth)。ボタンは箱の外 (包みの子) なので、箱の送りで動かない。
 * - 取り直す時: 読み込み (DOMContentLoaded)・箱の中の送り (scroll は泡立たないので、document の capture の段で受ける。
 *   縦に送っても同じ出来事が来る)・窓の大きさの変化 (window の resize)・htmx が中身を差し替えた後 (htmx:afterSettle。
 *   並び替え・絞り・ページ送り・小窓で保存した後の取り直し・受信した注文の一覧で行が足された後・進捗の盤の 10 秒ごとの更新)。
 * - 触るのは包みの data の属性と、自分で作ったボタンだけ。class を触らない (R19)。取った字を HTML として差し込まない。通信しない。
 */
(function () {
  "use strict";

  var BOX = "[data-scroll-hint]";
  var MORE_RIGHT = "data-more-right";
  var MORE_BELOW = "data-more-below";
  var BUTTON = "button[data-scroll-more]";
  var SVG_NS = "http://www.w3.org/2000/svg";
  // 1 回の押下で送る幅 (箱の見える幅に対する割合)。少し重ねて、どこまで送ったかを目で追えるようにする
  var PAGE_RATIO = 0.8;

  // 包みに「→」のボタンが無ければ作って箱の後ろに置く (1 つだけ)。中の絵は seizou_icons の chevron-right と同じ線
  function ensureButton(wrap, box) {
    var found = null;
    for (var i = 0; i < wrap.children.length; i++) {
      if (wrap.children[i].matches(BUTTON)) found = wrap.children[i];
    }
    if (found) return found;
    var button = document.createElement("button");
    button.type = "button";
    button.setAttribute("data-scroll-more", "");
    button.setAttribute("aria-label", "右へ送る");
    button.title = "右に続きがあります。押すと右へ送ります。";
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    var path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", "m9 6 6 6-6 6");
    svg.appendChild(path);
    button.appendChild(svg);
    box.insertAdjacentElement("afterend", button);
    return button;
  }

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
    var moreRight = box.scrollLeft + box.clientWidth < box.scrollWidth - 1;
    if (moreRight) ensureButton(wrap, box);
    setMore(wrap, MORE_RIGHT, moreRight);
    setMore(wrap, MORE_BELOW, box.scrollTop + box.clientHeight < box.scrollHeight - 1);
  }

  // 「→」のボタンの押下: その包みの箱を 1 画面分右へ送る (document に委譲。htmx で差し替えた包みにも効く)
  function onClick(event) {
    var target = event.target instanceof Element ? event.target.closest(BUTTON) : null;
    if (!target) return;
    var box = target.parentElement ? target.parentElement.querySelector(BOX) : null;
    if (!box) return;
    event.preventDefault();
    box.scrollBy({ left: Math.max(40, Math.round(box.clientWidth * PAGE_RATIO)), behavior: "smooth" });
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
  document.addEventListener("click", onClick);
  window.addEventListener("resize", markAll);
  document.addEventListener("htmx:afterSettle", markAll);
  document.addEventListener("DOMContentLoaded", markAll);
})();
