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
 * - 出す時は ul を body の直下へ移し (印 data-row-menu-list)、inline の style で position を fixed にし left・top を置く
 *   (right・bottom は auto)。表の箱 (overflow-auto) の端で切れないためと、止めた 1 列目の欄 (sticky + z-index) と行の地が
 *   メニューより手前に描かれないため (表の中に居ると先祖の stacking context に閉じ込められ、z-50 でも勝てない。本人の報告 11-07 18:0x。
 *   seizou-select.js の候補の一覧を body の直下に描く dropdownParent と同じ考え)。閉じたら details の中へ戻し、inline の style と印を消す。
 *   body の直下では daisyUI の dropdown の規則 (透明・absolute) が当たらないので、見た目は menu の class だけで決まる。
 * - htmx が画面の一部を差し替えた後 (htmx:afterSwap) に、body へ移した ul の details がもう document に無ければ閉じる
 *   (details が消えた後に ul が body に取り残されないため)。details が残っていれば閉じず、置き直す (行が動いた分だけ追う)。
 *   差し替えの前 (beforeSwap) に全部閉じる形はやめた: 受信した注文の 2 秒ごとの取り直しは表の先頭に行を足すだけ (afterbegin) で
 *   今の行を消さないのに、開いた ⋮ を 2 秒以内に閉じていた (指示書 11-07 の見つけ GS-01。402 回目の後戻り)。
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
  // body の直下へ移した ul (details → ul)。閉じる時に details の中へ戻す。
  var moved = new WeakMap();
  // 移した ul の持ち主 (ul → details)。htmx の差し替えの後に、details が document に残っているかを見る。
  var owner = new WeakMap();
  // 開いた時に置いた位置 (details → 右クリックの cursor の位置。summary の下なら null)。差し替えの後に同じ形で置き直す。
  var anchors = new WeakMap();
  var LIST = "[data-row-menu-list]";

  function listOf(details) {
    return moved.get(details) || details.querySelector("ul");
  }

  function moveOut(details, list) {
    if (list.parentNode === document.body) return;
    moved.set(details, list);
    owner.set(list, details);
    list.setAttribute("data-row-menu-list", "");
    document.body.appendChild(list);
  }

  function moveBack(details, list) {
    if (list.parentNode !== document.body) return;
    list.removeAttribute("data-row-menu-list");
    details.appendChild(list);
    moved.delete(details);
    owner.delete(list);
  }

  function elementOf(node) {
    return node && node.nodeType === 1 ? node : null;
  }

  function clearPlacement(details) {
    var list = listOf(details);
    if (!list) return;
    moveBack(details, list);
    list.removeAttribute("style");
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
    moveOut(details, list);
    anchors.set(details, point || null);
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
    if (target.closest(LIST)) return;
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
    if (target.closest(LIST)) {
      // body へ移したメニューの中。項目 (a) の押下で閉じ、見出しの字の押下では閉じない。
      if (target.closest("a")) closeAll(null);
      return;
    }
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
    if (target && (target.closest(MENU) || target.closest(LIST))) return;
    if (document.querySelector(MENU + "[open]")) closeAll(null);
  }

  // htmx の差し替えの後: body に移した ul のうち、details が document から外れた物は閉じる (ul を details の中へ戻し、body に残さない)。
  // details が残っていて開いたままなら、開いた時と同じ形 (summary の下か cursor の位置) で置き直す (先頭に行が足されて行が動いた時に追う)。
  function onAfterSwap() {
    var lists = document.querySelectorAll(LIST);
    for (var i = 0; i < lists.length; i++) {
      var details = owner.get(lists[i]);
      if (!details) {
        lists[i].remove();
      } else if (!details.isConnected) {
        closeMenu(details);
      } else if (details.open) {
        place(details, anchors.get(details) || null);
      } else {
        clearPlacement(details);
      }
    }
  }

  document.addEventListener("contextmenu", onContextMenu);
  document.addEventListener("click", onClick);
  document.addEventListener("keydown", onKeyDown);
  document.addEventListener("toggle", onToggle, true);
  document.addEventListener("scroll", onScroll, true);
  document.addEventListener("htmx:afterSwap", onAfterSwap);

  window.seizouRowMenu = { closeAll: closeAll };
})();
