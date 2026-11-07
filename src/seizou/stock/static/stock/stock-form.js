/*
 * 払い出しと棚卸しの調整の入力 (stock/stockmove_form.html と、小窓の stock/_form_modal.html)。素の JS (外の library を使わない)。
 * 部品の欄 (select[name="item"]) を選んだとき、数の欄 (棚卸しは counted_qty、払い出しは qty) の下に今の在庫数を 1 行出す。
 * 今の在庫数は stock の読むだけの口 (stock:item_balance) を選んだときに 1 回読む。口の URL の型は、この script の
 * data-balance-url (pk の所が 0) の「/0/」を選んだ部品の pk にした物。
 * 棚卸しの調整では、打った実地の数との差 (打った数 − 今の在庫数。services.adjust の差と同じ引き算) を同じ行に足す。
 *
 * 約束:
 * - 探すのは、この script の親の要素の中だけ (画面の全体は <main>、小窓は #seizou-modal-box)。小窓の後ろの画面の欄を拾わない。
 *   数の欄・その form の部品の欄・口の URL の型のどれかが無ければ何もしない。
 * - 聞き手は部品の欄と数の欄そのものに付ける (document に付けない)。小窓は止まって描き直すたびに中身と script が入れ替わり、
 *   この JS がもう 1 回動くので、document に付けると聞き手が増えていく。欄そのものなら古い欄と一緒に消える。
 * - 行 (#stock-now) は数の欄の箱の最後に 1 つだけ作る。部品を選んでいない間・取りに行っている間・読めなかった時は
 *   hidden の属性で隠す (class を付け替えない)。class の字は作る時に 1 回入れる固定の字だけ。
 * - 字は textContent で出す。取った字を HTML として差し込まない。数に 3 桁の区切りを付けない (出荷の「残り n」と同じ形)。
 * - 在庫を数えていない部品 (口が null を返す) は「まだ数えていません」で、差を出さない。払い出しは差を出さない。
 * - 選び直しが速いとき、返ってきた時に選んである値が取りに行った pk と違えば捨てる。
 * - 読めなかった時 (応答が ok でない・JSON でない・通信の失敗) は行を隠すだけ。赤の知らせを出さない。断らない。色を変えない。
 * - 通信は GET だけ (CSRF の見出しを付けない)。
 */
(function () {
  "use strict";

  // DOMContentLoaded の後に呼ぶ関数の中では null になるので、読み込まれた時に控える。
  var script = document.currentScript;
  var LABEL = "今の在庫数 ";
  var NOT_COUNTED = "まだ数えていません";
  var NUMBER = /^-?\d+(\.\d+)?$/;

  // 打った字の数。全角を半角に、「,」を外し、前後の空白を落とす。数の形でない字と空は null。
  function readNumber(input) {
    var text = String(input.value || "").normalize("NFKC").replace(/,/g, "").trim();
    return NUMBER.test(text) ? Number(text) : null;
  }

  // 差 = 打った数 − 今の在庫数。小数の誤差を出さないよう 100 倍の整数で引く。正は「+」を付け、0 は「0」。
  function diffText(counted, current) {
    var diff = (Math.round(counted * 100) - Math.round(current * 100)) / 100;
    return (diff > 0 ? "+" : "") + String(diff);
  }

  function start() {
    var root = script ? script.parentNode : null;
    var template = script ? script.getAttribute("data-balance-url") : "";
    if (!root || !template) return;
    var qty = root.querySelector('input[name="counted_qty"]');
    var counting = !!qty;
    if (!qty) qty = root.querySelector('input[name="qty"]');
    var select = qty && qty.form ? qty.form.querySelector('select[name="item"]') : null;
    if (!select || !qty.parentNode) return;

    var line = qty.parentNode.querySelector("#stock-now");
    if (!line) {
      line = document.createElement("p");
      line.id = "stock-now";
      line.className = "mt-2 text-xs text-base-content/60";
      line.setAttribute("role", "status");
      line.hidden = true;
      qty.parentNode.appendChild(line);
    }

    // undefined = 出さない、null = 在庫を数えていない、字 = 今の在庫数
    var balance;

    function render() {
      if (balance === undefined) {
        line.hidden = true;
        line.textContent = "";
        return;
      }
      var text = LABEL + (balance === null ? NOT_COUNTED : balance);
      if (counting && balance !== null) {
        var counted = readNumber(qty);
        if (counted !== null) text += "・差 " + diffText(counted, Number(balance));
      }
      line.textContent = text;
      line.hidden = false;
    }

    function load() {
      var value = select.value;
      balance = undefined;
      render();
      if (!value) return;
      var url = template.replace("/0/", "/" + encodeURIComponent(value) + "/");
      window
        .fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (response) {
          if (!response.ok) throw new Error(String(response.status));
          return response.json();
        })
        .then(function (data) {
          if (select.value !== value) return;
          var got = data ? data.balance : undefined;
          if (got !== null && typeof got !== "string") throw new Error("balance");
          balance = got;
          render();
        })
        .catch(function () {
          if (select.value !== value) return;
          balance = undefined;
          render();
        });
    }

    select.addEventListener("change", load);
    if (counting) {
      qty.addEventListener("input", render);
      qty.addEventListener("change", render);
    }
    // 止まった後の描き直し・?item=<pk> で開いた画面と小窓は、初めから部品が選んである。
    if (select.value) load();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
