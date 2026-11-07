/*
 * 取引先の新規登録と編集の入力 (masters/partner_form.html と、小窓の masters/_form_modal.html)。素の JS (外の library を使わない)。
 * 支払の月 (select[name="payment_month"]) と支払日 (input[name="payment_day"]) の今の値を、詳細と同じ字
 * (「支払日 翌月 25 日」「支払日 翌月末」「支払日 当日」) で支払日の欄の下に 1 行読み返す。
 * 字の作り方は masters/models.py の Partner.get_payment_day_display と同じ分かれ (上から順に見る)。分かれを変えたら両方を直す。
 *   1. 支払の月が当日 (option の value が same_day) なら、選んだ選択肢の字だけ。支払日の値は見ない (空でも読めない字でも出す)。
 *   2. 月の字は、支払の月を選んでいれば選んだ選択肢の字、「選んでください」なら空。
 *   3. 支払日が空なら月の字だけ (月の字も空なら隠す)。31 なら「<月の字>末」(月の字が空なら「月末」)。ほかは「<月の字> <n> 日」。
 * 出所: 初見テスト 5 回目の V42 (2 欄に分けて入れた値の意味を推して入れた)・V192 (詳細の字と照らして正しく入れられた。今は保存の後にしか照らせない)。
 *
 * 約束:
 * - 探すのは、この script の親の要素の中だけ (画面の全体は <main>、小窓は #seizou-modal-box)。小窓の後ろの画面の欄を拾わない。
 *   支払の月の欄と支払日の欄のどちらかが無ければ何もしない。
 * - 聞き手は支払の月の欄と支払日の欄そのものに付ける (document に付けない)。小窓は止まって描き直すたびに中身と script が入れ替わり、
 *   この JS がもう 1 回動くので、document に付けると聞き手が増えていく。欄そのものなら古い欄と一緒に消える。
 * - 行 (#payment-readback) は支払日の欄の箱の最後 (案内の字の次) に 1 つだけ作る。字が無い間と支払日が読めない間は
 *   hidden の属性で隠す (class を付け替えない)。class の字は作る時に 1 回入れる固定の字だけ。
 * - 支払日の読み方は保存の時 (core/widgets.py の normalize_number) と同じ。全角を半角に、「,」を外し、前後の空白を落とす。
 *   数字だけでない字か 1〜31 の外は読めないとして行を隠すだけ。赤の字にしない。断らない。保存の検査は今のまま。
 * - 字は textContent で出す。HTML として差し込まない。通信しない。欄の値を書き換えない。保存を止めない。知らせを出さない。
 */
(function () {
  "use strict";

  // DOMContentLoaded の後に呼ぶ関数の中では null になるので、読み込まれた時に控える。
  var script = document.currentScript;
  var LABEL = "支払日 ";
  var SAME_DAY = "same_day";
  var MONTH_END_DAY = 31;
  var DIGITS = /^\d+$/;

  // 選んだ選択肢の字 (前後の空白を落とす)。選んでいなければ空。
  function optionText(select) {
    var option = select.selectedOptions && select.selectedOptions[0];
    return option ? String(option.textContent || "").trim() : "";
  }

  // 支払日の欄の字。空は ""、1〜31 の数は Number、ほかは null (読めない)。
  function readDay(input) {
    var text = String(input.value || "").normalize("NFKC").replace(/,/g, "").trim();
    if (!text) return "";
    if (!DIGITS.test(text)) return null;
    var day = Number(text);
    return day >= 1 && day <= MONTH_END_DAY ? day : null;
  }

  // 行の字の値の部分 (見出しの「支払日 」を除く)。出さないときは空。
  function words(select, input) {
    if (select.value === SAME_DAY) return optionText(select);
    var month = select.value ? optionText(select) : "";
    var day = readDay(input);
    if (day === null) return "";
    if (day === "") return month;
    if (day === MONTH_END_DAY) return month ? month + "末" : "月末";
    return (month ? month + " " : "") + day + " 日";
  }

  function start() {
    var root = script ? script.parentNode : null;
    if (!root) return;
    var select = root.querySelector('select[name="payment_month"]');
    var input = root.querySelector('input[name="payment_day"]');
    if (!select || !input || !input.parentNode) return;

    var line = input.parentNode.querySelector("#payment-readback");
    if (!line) {
      line = document.createElement("p");
      line.id = "payment-readback";
      line.className = "mt-2 text-xs text-base-content/50";
      line.setAttribute("role", "status");
      line.hidden = true;
      input.parentNode.appendChild(line);
    }

    function render() {
      var text = words(select, input);
      if (!text) {
        line.hidden = true;
        line.textContent = "";
        return;
      }
      line.textContent = LABEL + text;
      line.hidden = false;
    }

    select.addEventListener("change", render);
    input.addEventListener("input", render);
    input.addEventListener("change", render);
    // 編集の画面と止まった後の描き直しは、初めから値が入っている。
    render();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
