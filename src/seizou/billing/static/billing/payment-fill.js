/*
 * 入金の記録のカード (billing/_payment_create_card.html) と、調整の記録のカード (billing/_adjustment_create_card.html。510 回目)。
 * 素の JS (外の library を使わない)。
 * 頭の 1 行の「残」の右の「残の全額を入れる」(調整は「残を消す金額を入れる」) を押したとき、同じカードの「金額」の欄にボタンの字を入れる。
 * 画面の全体 (billing/payment_form.html) と小窓 (billing/_form_modal.html) の両方が同じカードを include し、カードがこの JS を読む。
 *
 * 約束:
 * - document で click を受ける。押された物の近くに data-fill-amount の印のボタンが無ければ何もしない。
 * - ボタンの data-fill-amount の字 (サーバーが comma で作った「734,900」の形) をそのまま、
 *   同じカード (data-payment-card か data-adjustment-card の印の包み) の金額の欄 (name="amount") に入れる。カードか欄が無ければ何もしない。
 *   調整のボタンの字は残をマイナスにした数 (「-734,900」の形)。
 * - 入れた後に、欄から泡立つ input の event を送る。document で input を受ける保存のボタンの字の JS と、
 *   書きかけの印の JS がそれで動く。
 * - 小窓を開くたびに htmx がこの script をもう 1 度動かす。2 回目からは受け口を足さない (window.seizouPaymentFill)。
 * - 保存しない (form を送らない)。通信しない。HTML を差し込まない。class を触らない。欄へ focus を移さない。
 * - 打ってあった字は残の字に置き換える。押した人の操作なので、確かめは出さない。
 */
(function () {
  "use strict";

  if (window.seizouPaymentFill) return;
  window.seizouPaymentFill = true;

  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!(target instanceof Element)) return;
    var button = target.closest("[data-fill-amount]");
    var card = button ? button.closest("[data-payment-card], [data-adjustment-card]") : null;
    var input = card ? card.querySelector('input[name="amount"]') : null;
    if (!input) return;
    input.value = button.getAttribute("data-fill-amount");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
})();
