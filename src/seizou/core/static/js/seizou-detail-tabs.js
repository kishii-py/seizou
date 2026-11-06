/*
 * 詳細の区分のタブ (core/detail.html の input[name="detail-tabs"]) を、htmx の取り直しの後も選んでいたタブのまま保つ
 * (初見テスト R5 の r5-a-05 の 5 節。候補 C26 の 3)。読み込みは base.html の seizou-field-error.js の次の 1 行 (script の最後)。
 * 素の JS で書く (外の library を使わない)。
 *
 * 訳: 受注の詳細は小窓で保存した後の seizou:refresh で #order-live をタブの箱ごと取り直すので、「出荷」を選んでいても
 * サーバーが選ぶタブ (件数のある最初のタブ。core.views の BaseDetailView) へ戻っていた。保存の後も利用者が選んだタブのまま、
 * 新しい件数と行を見せる。指示書・請求書・出荷・納品書の詳細の #…-live の取り直しでも同じに効く。
 *
 * 約束:
 * - htmx:beforeSwap (document): 入れ替える先 (event.detail.target) の中に input[name="detail-tabs"] があるときだけ、
 *   その中で選ばれている radio の要素と aria-label を覚える。中に無いとき (発注の #po-live・添付ファイルの枠の #attachments・
 *   一覧の行など) は何もしない (覚えた物も消さない)。
 * - htmx:afterSettle (document): 覚えた radio があり、その要素がもう document から外れている (isConnected が偽 = 覚えた入れ替えが
 *   済んだ) ときだけ、ページの全部の input[name="detail-tabs"] から同じ aria-label の radio を探して checked にし、覚えた物を消す。
 *   覚えた radio がまだ document にあるときは何もしない (覚えた物も残す)。見つからなければ選び直さない (サーバーが選んだタブのまま)。
 * - 外れたかで見る訳: 小窓で保存した後の seizou:refresh では、#order-live と添付ファイルの枠 #attachments
 *   (attachments/_panel.html の hx-trigger="seizou:refresh from:body") の 2 つの取り直しが別に出て、どちらが先に落ち着くかは
 *   決まらない。ほかの入れ替えの落ち着きで、覚えた物を使ったり消したりしないため。
 * - outerHTML で入れ替えた元の要素は外れているので、新しい radio はページの全部から探す (seizou-select.js の htmx:afterSettle と同じ形)。
 * - aria-label は CSS の選び方の字に埋め込まず、querySelectorAll の結果を回して比べる (字の escape を要らなくするため)。
 * - 触るのは radio の checked だけ。class を触らない (R19)。通信しない。
 */
(function () {
  "use strict";

  var TABS = 'input[name="detail-tabs"]';
  var remembered = null;

  document.addEventListener("htmx:beforeSwap", function (event) {
    var target = event.detail && event.detail.target;
    if (!(target instanceof Element) || !target.querySelector(TABS)) return;
    var radios = target.querySelectorAll(TABS);
    for (var i = 0; i < radios.length; i++) {
      if (radios[i].checked) {
        remembered = { radio: radios[i], label: radios[i].getAttribute("aria-label") };
        return;
      }
    }
  });

  document.addEventListener("htmx:afterSettle", function () {
    if (!remembered || remembered.radio.isConnected) return;
    var label = remembered.label;
    remembered = null;
    var radios = document.querySelectorAll(TABS);
    for (var i = 0; i < radios.length; i++) {
      if (radios[i].getAttribute("aria-label") === label) {
        radios[i].checked = true;
        return;
      }
    }
  });
})();
