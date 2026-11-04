/*
 * 一覧の行の操作のメニュー (⋮ と右クリック)。読み込みは base.html の seizou-select.js の次の 1 行。
 * 素の JS で書く (外の library を使わない。htmx は読まない)。
 *
 * markup の約束 (orders/_row_menu.html が使う。受信した注文の画面も同じ形):
 *   <tr data-row-menu ...>                                  行。右クリックでこの行のメニューを開く
 *     ... <td class="whitespace-nowrap text-right">
 *       <details class="dropdown dropdown-end row-menu">
 *         <summary class="btn btn-ghost btn-xs" aria-label="行の操作" title="行の操作">⋮</summary>
 *         <ul class="menu dropdown-content z-50 w-56 rounded-lg border border-base-300 bg-base-100 p-2 shadow-sm">
 *           (z-50 は左のメニュー z-40・上の帯 z-30 より上)
 *           <li><a href="...">出荷する</a></li> ...
 *         </ul>
 *       </details>
 *     </td>
 *   </tr>
 *
 * 動き:
 * - tr[data-row-menu] の contextmenu: 既定のメニューを出さず (preventDefault)、その行の details.row-menu を開き (open)、
 *   ul を cursor の位置 (clientX・clientY) に出す。メニューの中の右クリックは既定のまま (新しいタブで開くなど)。
 * - summary の押下 (details の toggle で open になった時): ul を summary の下 (右端をそろえる) に出す。
 * - 出す時は ul の inline の style で position を fixed にし left・top を置く (right・bottom は auto)。
 *   表の箱 (overflow-auto) の端で切れないため。閉じたら inline の style を消す。
 *   daisyUI の dropdown は中に focus が無いと中身が透明なので、opacity と scale も inline の style で置く。
 * - 画面の右端・下端ではみ出す分は、左か上へずらす。
 * - 左は main の左の端 (左のメニューの右) より左に出さない。summary の下に右端をそろえて出すと左へはみ出すときは、
 *   summary の左端にそろえて右へ開く。
 * - 1 つ開く時、ほかの行の details.row-menu は閉じる (open を外す)。
 *   Escape・メニューの外の押下・項目 (a) の押下・画面と表の箱の scroll で閉じる。
 * - 行の onclick で詳細へ移る画面では、onclick の closest('a') を closest('a, details') にする
 *   (summary の押下で移らないため)。注残の盤の行は onclick を持たない。
 * - document に委譲して付ける (contextmenu・click・keydown・toggle (capture)・scroll (capture))。
 *   htmx で差し替えた行にも効く。要素ごとに載せない。
 * - class を足さない (Tailwind が CSS に入れないため)。open の属性と inline の style だけで動かす。
 * - 取った字を HTML として差し込まない。通信しない。
 * - 公開する口は window.seizouRowMenu = { closeAll: closeAll } だけ。
 */
(function () {
  "use strict";

  var MENU = "details.row-menu";
  var ROW = "tr[data-row-menu]";
  var EDGE = 8; // 画面の端から空ける px
  var GAP = 4; // summary とメニューの間の px

  // 右クリックで開いた時の cursor の位置。details の toggle は後で (非同期に) 来るので、そこで 1 回だけ使う。
  var pending = null;

  function listOf(details) {
    return details.querySelector("ul");
  }

  function elementOf(node) {
    return node && node.nodeType === 1 ? node : null;
  }

  function clearPlacement(details) {
    var list = listOf(details);
    if (list) list.removeAttribute("style");
  }

  function closeMenu(details) {
    if (details.open) details.open = false;
    clearPlacement(details);
  }

  function closeAll(except) {
    var opened = document.querySelectorAll(MENU + "[open]");
    for (var i = 0; i < opened.length; i++) {
      if (opened[i] !== except) closeMenu(opened[i]);
    }
  }

  function place(details, point) {
    var list = listOf(details);
    var summary = details.querySelector("summary");
    if (!list) return;
    var style = list.style;
    style.position = "fixed";
    style.right = "auto";
    style.bottom = "auto";
    style.margin = "0";
    style.opacity = "1";
    style.scale = "1";
    style.left = "0px";
    style.top = "0px";

    var width = list.offsetWidth;
    var height = list.offsetHeight;
    var viewWidth = document.documentElement.clientWidth;
    var viewHeight = document.documentElement.clientHeight;
    // 左の端の下限は main の左の端 (左のメニューの右)。幅の狭い画面では左のメニューが閉じていて main の左は 0 (画面の端と同じ)。
    var minLeft = EDGE;
    var main = document.querySelector("main");
    if (main) minLeft = Math.max(EDGE, Math.round(main.getBoundingClientRect().left) + EDGE);
    var left;
    var top;
    if (point) {
      left = point.x;
      top = point.y;
      if (left + width > viewWidth - EDGE) left = point.x - width;
      if (top + height > viewHeight - EDGE) top = point.y - height;
    } else {
      var anchor = (summary || details).getBoundingClientRect();
      left = anchor.right - width;
      // 右端をそろえると左のメニューの下へ出るとき (表の先頭の列の ⋮) は、summary の左端にそろえて右へ開く。
      if (left < minLeft) left = anchor.left;
      top = anchor.bottom + GAP;
      if (top + height > viewHeight - EDGE) top = anchor.top - height - GAP;
    }
    if (left + width > viewWidth - EDGE) left = viewWidth - EDGE - width;
    if (left < minLeft) left = minLeft;
    if (top < EDGE) top = EDGE;
    style.left = Math.round(left) + "px";
    style.top = Math.round(top) + "px";
  }

  function onToggle(event) {
    var details = elementOf(event.target);
    if (!details || !details.matches(MENU)) return;
    if (details.open) {
      closeAll(details);
      var point = pending && pending.details === details ? pending.point : null;
      pending = null;
      place(details, point);
    } else {
      if (pending && pending.details === details) pending = null;
      clearPlacement(details);
    }
  }

  function onContextMenu(event) {
    var target = elementOf(event.target);
    if (!target) return;
    if (target.closest(MENU + " ul")) return;
    var row = target.closest(ROW);
    if (!row) return;
    var details = row.querySelector(MENU);
    if (!details) return;
    event.preventDefault();
    var point = { x: event.clientX, y: event.clientY };
    closeAll(details);
    if (!details.open) {
      pending = { details: details, point: point };
      details.open = true;
    }
    place(details, point);
  }

  function onClick(event) {
    var target = elementOf(event.target);
    if (!target) return;
    var details = target.closest(MENU);
    if (!details) {
      closeAll(null);
      return;
    }
    var summary = target.closest("summary");
    if (summary && summary.parentElement === details) {
      closeAll(details);
      if (!details.open) {
        // 既定の開き方 (toggle は後で来る) を待たずに開いて置く。置く前の位置が一瞬見えないため。
        event.preventDefault();
        pending = null;
        details.open = true;
        place(details, null);
      }
      return;
    }
    if (target.closest("ul") && target.closest("a")) closeMenu(details);
  }

  function onKeyDown(event) {
    if (event.key !== "Escape") return;
    var opened = document.querySelector(MENU + "[open]");
    if (!opened) return;
    closeAll(null);
    var summary = opened.querySelector("summary");
    if (summary) summary.focus({ preventScroll: true });
  }

  function onScroll(event) {
    var target = elementOf(event.target);
    if (target && target.closest(MENU)) return;
    if (document.querySelector(MENU + "[open]")) closeAll(null);
  }

  document.addEventListener("contextmenu", onContextMenu);
  document.addEventListener("click", onClick);
  document.addEventListener("keydown", onKeyDown);
  document.addEventListener("toggle", onToggle, true);
  document.addEventListener("scroll", onScroll, true);

  window.seizouRowMenu = { closeAll: closeAll };
})();
