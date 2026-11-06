/*
 * 受入の新規登録の入力 (purchasing/receipt_form.html の画面の全体と、purchasing/_form_modal.html の小窓)。素の JS (外の library を使わない)。
 * 発注の明細の選ぶ欄 (select[name="line"]) を選び直したとき、数量の欄の下の字 (#id_qty_helptext) を
 * 選んだ明細の受入の残にする。受入の残の数は候補の口 (purchasing:purchaseorderline_select) の字の末尾の「 残り n」を読む。
 * 出荷の入力の shipping/shipment-form.js の写し (初見テスト R5 の候補 C64)。数量と受入の残を比べることはしない。
 *
 * 約束:
 * - 選ぶ欄か数量の欄の下の字が無ければ何もしない。
 * - 選んだ値が空なら「発注の明細を選ぶと、受入の残が出ます。」(purchasing/forms.py の NO_LINE_HELP と同じ字)。
 * - 選んだ物の字に「 残り n」が無いとき (サーバーが描いた初めの字は集計が無い) は何もしない (help_text が既に正しい)。
 * - 字は textContent で出す。取った字を HTML として差し込まない。class を足さない。通信しない。
 * - 聞き手は選ぶ欄そのものに付ける (小窓を開き直すたびに聞き手が増えないように)。
 */
(function () {
  "use strict";

  var NO_LINE_TEXT = "発注の明細を選ぶと、受入の残が出ます。";
  var REMAINING = / 残り (\S+)$/;

  function chosenText(select, value) {
    var ts = select.tomselect;
    var option = ts && ts.options ? ts.options[value] : null;
    if (option && option.text) return String(option.text);
    var selected = select.selectedOptions && select.selectedOptions[0];
    return selected ? selected.textContent : "";
  }

  function start() {
    var select = document.querySelector('select[name="line"]');
    var help = document.getElementById("id_qty_helptext");
    if (!select || !help) return;
    select.addEventListener("change", function () {
      var value = select.value;
      if (!value) {
        help.textContent = NO_LINE_TEXT;
        return;
      }
      var match = REMAINING.exec(chosenText(select, value).trim());
      if (match) help.textContent = "受入の残 " + match[1];
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
