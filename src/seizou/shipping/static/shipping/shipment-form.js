/*
 * 出荷の新規登録の入力 (shipping/shipment_form.html と、受注の詳細から開く小窓 shipping/_form_modal.html)。素の JS (外の library を使わない)。
 * 1. 受注の明細の選ぶ欄 (select[name="order_line"]) を選び直したとき、数量の欄の下の字 (#id_qty_helptext) を
 *    選んだ明細の受注の残りにする。残りの数は候補の口 (orders:orderline_select) の字の末尾の「 残り n」を読む。
 * 2. 数量の欄 (#id_qty) の input と change で、打った数を残りと比べる。超えていれば、押す前に欄の下の字を
 *    「残り 6 を 1 超えています。」にし、会社の設定 (数量の欄の data-over-shipment) が止める (stop) なら「このままでは保存できません。」、
 *    知らせる (warn) なら「保存の時に確かめの知らせが出ます。」を続け、欄の下の字に data-tone="warning" を付ける
 *    (黄の字は core/_form_card.html の決まった class の data-[tone=warning]:text-warning が出す。初見テスト R5 の V61)。
 *    残り以内に戻せば「残り n」に戻し、data-tone を外す。止めない (保存のボタンは押せるまま。止めるか知らせるかは保存の時に検査の層が決める)。
 *
 * 約束:
 * - 選ぶ欄か数量の欄の下の字が無ければ何もしない。
 * - 選んだ値が空なら「受注の明細を選ぶと、残りの数が出ます。」(shipping/forms.py の NO_LINE_HELP と同じ字) にし、比べない。
 * - 残りの数は、選んだ物の字の「 残り n」から読む。字に無いとき (サーバーが描いた初めの字は集計が無い) は、描いたときの明細
 *   (選ぶ欄の data-label-line) と同じ値なら数量の欄の data-remaining を使う。ほかの明細なら比べない (欄の下の字は前のまま)。
 * - 会社の設定が通す (off) か知らない値なら、比べず、欄の下の字は「残り n」のまま (会社が知らせない形を選んでいるため)。
 * - 数は全角を半角に・「,」を外して読む (js/seizou-submit-label.js の readNumber と同じ)。数でない字なら比べない。差は小数 2 桁で丸める。
 * - 開いたときにも 1 回比べる (止まった後に描き直した画面で、打ってある数が残りを超えていれば同じ字を出す)。
 * - 聞き手は欄そのものに付ける (document に委ねない)。小窓は中身を差し替えるたびにこの script を読み直すので、古い欄と一緒に聞き手も消える。
 * - 字は textContent で出す。取った字を HTML として差し込まない。触る属性は data-tone だけで、class を足さない。通信しない。
 */
(function () {
  "use strict";

  var NO_LINE_TEXT = "受注の明細を選ぶと、残りの数が出ます。";
  var REMAINING = / 残り (\S+)$/;
  var STOP_TEXT = "このままでは保存できません。";
  var WARN_TEXT = "保存の時に確かめの知らせが出ます。";

  function chosenText(select, value) {
    var ts = select.tomselect;
    var option = ts && ts.options ? ts.options[value] : null;
    if (option && option.text) return String(option.text);
    var selected = select.selectedOptions && select.selectedOptions[0];
    return selected ? selected.textContent : "";
  }

  function readNumber(text) {
    var value = String(text || "").normalize("NFKC").replace(/,/g, "").trim();
    if (!/^-?\d+(\.\d+)?$/.test(value)) return null;
    return Number(value);
  }

  function start() {
    var select = document.querySelector('select[name="order_line"]');
    var help = document.getElementById("id_qty_helptext");
    if (!select || !help) return;
    var qty = document.getElementById("id_qty");
    var mode = qty ? qty.getAttribute("data-over-shipment") : null;
    var modeText = mode === "stop" ? STOP_TEXT : mode === "warn" ? WARN_TEXT : "";
    // 描いたときの明細の残り (字のまま)。明細が決まっていなければ null
    var drawnRemaining = qty ? qty.getAttribute("data-remaining") : null;
    // 今の明細の残り (字のまま。分からなければ null) と、超えていないときの欄の下の字
    var remainingText = drawnRemaining;
    var baseText = help.textContent;

    function compare() {
      var remaining = remainingText === null ? null : readNumber(remainingText);
      var typed = qty ? readNumber(qty.value) : null;
      if (!modeText || remaining === null || typed === null || typed <= remaining) {
        help.textContent = baseText;
        delete help.dataset.tone;
        return;
      }
      var over = Math.round((typed - remaining) * 100) / 100;
      help.textContent = "残り " + remainingText + " を " + over + " 超えています。" + modeText;
      help.dataset.tone = "warning";
    }

    select.addEventListener("change", function () {
      var value = select.value;
      if (!value) {
        remainingText = null;
        baseText = NO_LINE_TEXT;
      } else {
        var match = REMAINING.exec(chosenText(select, value).trim());
        if (match) {
          remainingText = match[1];
          baseText = "残り " + match[1];
        } else if (drawnRemaining !== null && value === select.getAttribute("data-label-line")) {
          remainingText = drawnRemaining;
          baseText = "残り " + drawnRemaining;
        } else {
          remainingText = null;
        }
      }
      compare();
    });
    if (qty) {
      qty.addEventListener("input", compare);
      qty.addEventListener("change", compare);
      compare();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
