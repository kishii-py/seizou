/*
 * 実績の新規登録の入力 (progress/progress_form.html と、小窓の progress/_form_modal.html)。素の JS (外の library を使わない)。
 * 「指示書の工程」の欄 (select[name="workorder_step"]) を選び直したとき、その工程に完了の印の付いた実績があれば、
 * 欄の下に「この工程には完了の印が付いています。実績は足せます。」を出し、無ければ隠す (PROG-1c = a。断らない・色を変えない)。
 * 印の有る無しは progress の読むだけの口 (progress:step_completed) を選び直したときに 1 回読む。口の URL の型は、この script の
 * data-completed-url (pk の所が 0) の「/0/」を選んだ工程の pk にした物。
 * 開いた時の字 (?workorder_step=<pk> で開いた画面と小窓・止まった後の描き直し) は、サーバーが form の __init__ で描く
 * (progress/forms.py の ProgressRecordForm)。この JS は開いた時には読まず、選び直したときだけ読む。
 *
 * 約束 (stock/stock-form.js と同じ形):
 * - 探すのは、この script の親の要素の中だけ (画面の全体は <main>、小窓は #seizou-modal-box)。小窓の後ろの画面の欄を拾わない。
 *   工程の欄か口の URL の型が無ければ何もしない。
 * - 聞き手は工程の欄そのものに付ける (document に付けない)。小窓は止まって描き直すたびに中身と script が入れ替わり、
 *   この JS がもう 1 回動くので、document に付けると聞き手が増えていく。欄そのものなら古い欄と一緒に消える。
 * - 行 (#id_workorder_step_helptext。土台の入力のカード core/_form_card.html が欄の下の字に付ける id と同じ) は、
 *   サーバーが描いていればそれを使い、無ければ欄の箱の最後に 1 つだけ作る。工程を選んでいない間・取りに行っている間・
 *   印が無い時・読めなかった時は hidden の属性で隠す (class を付け替えない)。class の字は作る時に 1 回入れる固定の字だけ。
 * - 字は textContent で出す。字は progress/forms.py の STEP_COMPLETED_HELP と同じ字 (tests/test_progress_defect.py が見る)。
 * - 選び直しが速いとき、返ってきた時に選んである値が取りに行った pk と違えば捨てる。
 * - 読めなかった時 (応答が ok でない・JSON でない・通信の失敗) は行を隠すだけ。赤の知らせを出さない。断らない。色を変えない。
 * - 通信は GET だけ (CSRF の見出しを付けない)。
 */
(function () {
  "use strict";

  // DOMContentLoaded の後に呼ぶ関数の中では null になるので、読み込まれた時に控える。
  var script = document.currentScript;
  var TEXT = "この工程には完了の印が付いています。実績は足せます。";

  function start() {
    var root = script ? script.parentNode : null;
    var template = script ? script.getAttribute("data-completed-url") : "";
    if (!root || !template) return;
    var select = root.querySelector('select[name="workorder_step"]');
    if (!select || !select.parentNode) return;

    var line = select.parentNode.querySelector("#id_workorder_step_helptext");

    function show(completed) {
      if (!completed) {
        if (line) {
          line.hidden = true;
          line.textContent = "";
        }
        return;
      }
      if (!line) {
        line = document.createElement("p");
        line.id = "id_workorder_step_helptext";
        line.className = "mt-2 text-xs text-base-content/60";
        select.parentNode.appendChild(line);
      }
      line.textContent = TEXT;
      line.hidden = false;
    }

    function load() {
      var value = select.value;
      show(false);
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
          show(!!(data && data.completed === true));
        })
        .catch(function () {
          if (select.value !== value) return;
          show(false);
        });
    }

    select.addEventListener("change", load);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
