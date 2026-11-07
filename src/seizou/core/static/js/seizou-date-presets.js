/*
 * 一覧の期間の絞りの近道「今日・今週・今月・先月」(core/_date_presets.html。受け渡しの seizou-部品の6案-20261107/指示書.md の 1)。
 * 読み込みは base.html の script の並びの最後の 1 行。素の JS (外の library を使わない。htmx は読まない)。
 *
 * 動き:
 * - 近道のボタン (button[data-preset-start][data-preset-end]) の押下を document に委譲して受ける (htmx で差し替えた箱にも効く)。
 * - ボタンの箱の外側の期間の枠 ([data-date-range]。core/_filters.html) の中の 2 つの日付の欄 (始め・終わりの順) に、
 *   data 属性の値 (サーバーが会社の時刻で決めた ISO の日付) をそのまま入れ、終わりの欄で change を出す (bubbles)。
 *   form の hx-trigger (change delay:800ms from:[data-list-filter][type=date]) がそれを受けて #rows を取り直す (今の絞りと同じ道)。
 * - 選んだ印 (押した印の属性と class) は触らない。行の部分の応答でサーバーが箱ごと描き直す (core/_filter_clear.html の oob)。
 * - 日付を計算しない (ブラウザの時刻を見ない)。class を触らない。HTML を差し込まない。通信しない。
 */
(function () {
  "use strict";

  var BUTTON = "button[data-preset-start][data-preset-end]";
  var RANGE = "[data-date-range]";

  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0) return;
    var button = event.target.closest ? event.target.closest(BUTTON) : null;
    if (!button) return;
    var range = button.closest(RANGE);
    if (!range) return;
    var inputs = range.querySelectorAll('input[type="date"]');
    if (inputs.length < 2) return;
    event.preventDefault();
    inputs[0].value = button.getAttribute("data-preset-start");
    inputs[1].value = button.getAttribute("data-preset-end");
    inputs[1].dispatchEvent(new Event("change", { bubbles: true }));
  });
})();
