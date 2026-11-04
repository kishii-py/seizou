// AI チャットの窓 (assistant/_panel.html) を、題の帯で掴んで動かし、右下の角で大きさを変え、「広げる」で画面の 80% にする。
// 位置と大きさは localStorage の鍵 seizou-assistant-window に覚え、次に開いた時に同じ所と大きさで出す
// (画面が狭くなって収まらない時は画面の中に寄せる)。覚えるのはブラウザだけ (サーバーに送らない)。
// 幅 640 未満 (スマホ) は窓を画面いっぱいの板にし、動かす・大きさを変える・広げるを切る (覚えた値を読まず書かない)。
// 開け閉め・Esc・質問と答え (htmx) は _panel.html の script のまま。右下の「AI に質問」の釦は動かさない (E-38)。
// 位置と大きさは窓の箱 [data-assistant-window] の inline の style だけで持つ。class を足し引きしない
// (CSS はテンプレートだけから作るので)。外の library を使わない。取った字を HTML として差し込まない。
(function () {
  "use strict";

  var KEY = "seizou-assistant-window";
  var EDGE = 8; // 画面の端から空ける px
  var MIN_WIDTH = 320;
  var MIN_HEIGHT = 360;
  var MAX = 0.95; // 大きさの上限は画面の 95%
  var BIG = 0.8; // 「広げる」は画面の幅と高さの 80%

  var panel = document.getElementById("assistant-panel");
  var box = panel && panel.querySelector("[data-assistant-window]");
  var grip = panel && panel.querySelector("[data-assistant-grip]");
  var expand = document.getElementById("assistant-expand");
  if (!panel || !box || !grip || !expand) return;

  var wide = window.matchMedia("(min-width: 640px)");
  var mode = null; // "wide" (動かせる窓) か "sheet" (スマホの板)。null は開いた時に決める
  var back = null; // 広げる前の位置と大きさ。広げていない時は null
  var drag = null;

  function isNumber(value) {
    return typeof value === "number" && isFinite(value);
  }

  function isPlace(value) {
    return !!value && isNumber(value.left) && isNumber(value.top) && isNumber(value.width) && isNumber(value.height);
  }

  function read() {
    try {
      var saved = JSON.parse(window.localStorage.getItem(KEY));
      return isPlace(saved) ? saved : null;
    } catch (error) {
      return null;
    }
  }

  function viewSize() {
    return { width: document.documentElement.clientWidth, height: document.documentElement.clientHeight };
  }

  function current() {
    var rect = box.getBoundingClientRect();
    return { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
  }

  // 大きさを最小と最大の間に、位置を画面の端から EDGE の内側に収める。
  function fit(place) {
    var view = viewSize();
    var width = Math.min(Math.max(place.width, MIN_WIDTH), view.width * MAX);
    var height = Math.min(Math.max(place.height, MIN_HEIGHT), view.height * MAX);
    var left = Math.min(Math.max(place.left, EDGE), view.width - width - EDGE);
    var top = Math.min(Math.max(place.top, EDGE), view.height - height - EDGE);
    return { left: Math.round(left), top: Math.round(top), width: Math.round(width), height: Math.round(height) };
  }

  function pinned() {
    return mode === "wide" && box.style.position === "fixed";
  }

  function put(place) {
    var style = box.style;
    style.position = "fixed";
    style.right = "auto";
    style.bottom = "auto";
    style.left = place.left + "px";
    style.top = place.top + "px";
    style.width = place.width + "px";
    style.height = place.height + "px";
    style.minWidth = MIN_WIDTH + "px";
    style.minHeight = MIN_HEIGHT + "px";
    style.maxWidth = "95vw";
    style.maxHeight = "95vh";
  }

  // 初めて動かす・大きさを変える時に、右下に付いた今の窓を同じ所に置き直す (左上の点と大きさで持つ)。
  // 最小より小さい窓は、右と下の端をそのままに左と上へ広げる (右下の「AI に質問」の上に重ねない)。
  function pin() {
    if (pinned()) return;
    var place = current();
    if (place.width < MIN_WIDTH) {
      place.left -= MIN_WIDTH - place.width;
      place.width = MIN_WIDTH;
    }
    if (place.height < MIN_HEIGHT) {
      place.top -= MIN_HEIGHT - place.height;
      place.height = MIN_HEIGHT;
    }
    put(fit(place));
  }

  function save() {
    if (!pinned() || panel.hidden) return;
    var place = current();
    var data = { left: place.left, top: place.top, width: place.width, height: place.height, back: back };
    try {
      window.localStorage.setItem(KEY, JSON.stringify(data));
    } catch (error) {
      // 覚えられないブラウザ (容量・設定) では覚えずに動く
    }
  }

  function showExpand() {
    var label = back ? "戻す" : "広げる";
    expand.setAttribute("aria-label", label);
    expand.setAttribute("title", label);
  }

  function clear() {
    box.removeAttribute("style");
    grip.removeAttribute("style");
  }

  function toBottom() {
    var list = document.getElementById("assistant-messages");
    if (list) list.scrollTop = list.scrollHeight;
  }

  function setUpSheet() {
    mode = "sheet";
    back = null;
    clear();
    var style = box.style;
    style.position = "fixed";
    style.left = "0";
    style.top = "0";
    style.width = "100%";
    style.height = "100%";
    style.maxWidth = "none";
    style.maxHeight = "none";
    style.borderRadius = "0";
    expand.hidden = true;
  }

  function setUpWide() {
    mode = "wide";
    clear();
    var style = box.style;
    style.resize = "both";
    style.overflow = "auto";
    grip.style.cursor = "move";
    grip.style.touchAction = "none";
    grip.style.userSelect = "none";
    expand.hidden = false;
    var saved = read();
    back = saved && isPlace(saved.back) ? saved.back : null;
    if (saved) put(fit(saved));
    showExpand();
  }

  // 開いた時・幅が 640 をまたいだ時・画面の大きさが変わった時に置き直す。閉じている間は何もしない。
  function layout() {
    if (panel.hidden) return;
    if (!wide.matches) {
      if (mode !== "sheet") setUpSheet();
      return;
    }
    if (mode !== "wide") setUpWide();
    else if (pinned()) put(fit(current()));
  }

  grip.addEventListener("pointerdown", function (event) {
    if (mode !== "wide" || event.button !== 0) return;
    if (event.target.closest("button")) return; // 閉じる・広げるの釦は掴まない
    pin();
    var place = current();
    drag = { id: event.pointerId, dx: event.clientX - place.left, dy: event.clientY - place.top };
    grip.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  grip.addEventListener("pointermove", function (event) {
    if (!drag || drag.id !== event.pointerId) return;
    var place = current();
    place.left = event.clientX - drag.dx;
    place.top = event.clientY - drag.dy;
    put(fit(place));
  });
  function stop(event) {
    if (!drag || drag.id !== event.pointerId) return;
    drag = null;
    if (grip.hasPointerCapture(event.pointerId)) grip.releasePointerCapture(event.pointerId);
    save();
  }
  grip.addEventListener("pointerup", stop);
  grip.addEventListener("pointercancel", stop);

  // 右下の角 (resize の取っ手) は窓の箱そのものの押下。大きさを変える前に、左上の点で置き直す。
  box.addEventListener("pointerdown", function (event) {
    if (mode === "wide" && event.target === box) pin();
  });
  // CSS の resize は event を出さないので、箱の大きさの変化を見て覚える (置き直した時も同じ値を書くだけ)。
  if (window.ResizeObserver) {
    new window.ResizeObserver(function () {
      if (!drag) save();
    }).observe(box);
  }

  expand.addEventListener("click", function () {
    if (mode !== "wide") return;
    pin();
    if (back) {
      put(fit(back));
      back = null;
    } else {
      back = current();
      var view = viewSize();
      put(
        fit({
          left: (view.width * (1 - BIG)) / 2,
          top: (view.height * (1 - BIG)) / 2,
          width: view.width * BIG,
          height: view.height * BIG,
        })
      );
    }
    showExpand();
    save();
  });

  // 開いた時 (hidden が外れた時) に置き、吹き出しの並びを下まで送り直す (大きさが変わると下の端がずれるため)。
  new MutationObserver(function () {
    if (panel.hidden) return;
    layout();
    toBottom();
  }).observe(panel, { attributes: true, attributeFilter: ["hidden"] });
  wide.addEventListener("change", function () {
    mode = null;
    layout();
  });
  window.addEventListener("resize", function () {
    if (pinned() && !panel.hidden) put(fit(current()));
  });
  layout();
})();
