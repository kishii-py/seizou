// 図面ビューアの拡大・縮小・原寸・回転・移動 (masters/drawing_viewer.html)。外の library を使わない。
// [data-drawing-viewer] の箱ごとに動き、箱の中の img[data-drawing-image] (1 枚か、見比べるときの 2 枚) の全部に、
// 同じ拡大率・回転・移動を style.transform で当てる。状態は data- の属性と style.transform だけで持つ
// (CSS はテンプレートだけから作るので、JS で class を足し引きしない)。ダウンロードは口の link (JS で作らない)。
(function () {
  "use strict";

  var STEP = 1.25;
  var MIN_SCALE = 0.25;
  var MAX_SCALE = 8;

  function clamp(value) {
    return Math.min(MAX_SCALE, Math.max(MIN_SCALE, value));
  }

  function setUp(box) {
    var images = Array.prototype.slice.call(box.querySelectorAll("img[data-drawing-image]"));
    if (!images.length) return;
    var state = { scale: 1, rotate: 0, x: 0, y: 0 };
    var drag = null;

    function apply() {
      var transform =
        "translate(" + state.x + "px, " + state.y + "px) scale(" + state.scale + ") rotate(" + state.rotate + "deg)";
      images.forEach(function (image) {
        image.style.transform = transform;
      });
      box.setAttribute("data-scale", String(Math.round(state.scale * 100) / 100));
      box.setAttribute("data-rotate", String(state.rotate));
    }

    function zoom(factor) {
      state.scale = clamp(state.scale * factor);
      apply();
    }

    // 原寸 = 表示用の絵の 1 画素を画面の 1 画素にする。初めの大きさは枠に合わせた大きさ (object-contain)。
    function actual() {
      var image = images[0];
      if (!image.naturalWidth || !image.clientWidth) return;
      var fit = Math.min(image.clientWidth / image.naturalWidth, image.clientHeight / image.naturalHeight);
      state.scale = clamp(1 / fit);
      state.x = 0;
      state.y = 0;
      apply();
    }

    function rotate() {
      state.rotate = (state.rotate + 90) % 360;
      apply();
    }

    var actions = { "zoom-in": function () { zoom(STEP); }, "zoom-out": function () { zoom(1 / STEP); }, actual: actual, rotate: rotate };
    document.querySelectorAll("[data-action]").forEach(function (button) {
      var run = actions[button.getAttribute("data-action")];
      if (run) button.addEventListener("click", run);
    });

    box.addEventListener("pointerdown", function (event) {
      if (event.button !== 0) return;
      drag = { id: event.pointerId, startX: event.clientX, startY: event.clientY, x: state.x, y: state.y };
      box.setPointerCapture(event.pointerId);
      box.setAttribute("data-dragging", "true");
    });
    box.addEventListener("pointermove", function (event) {
      if (!drag || drag.id !== event.pointerId) return;
      state.x = drag.x + (event.clientX - drag.startX);
      state.y = drag.y + (event.clientY - drag.startY);
      apply();
    });
    function stop(event) {
      if (!drag || drag.id !== event.pointerId) return;
      drag = null;
      if (box.hasPointerCapture(event.pointerId)) box.releasePointerCapture(event.pointerId);
      box.setAttribute("data-dragging", "false");
    }
    box.addEventListener("pointerup", stop);
    box.addEventListener("pointercancel", stop);

    // ホイールで拡大・縮小。枠の上だけ画面の送りを止める (passive: false が要る)。
    box.addEventListener(
      "wheel",
      function (event) {
        event.preventDefault();
        zoom(event.deltaY < 0 ? STEP : 1 / STEP);
      },
      { passive: false }
    );

    apply();
  }

  function start() {
    document.querySelectorAll("[data-drawing-viewer]").forEach(setUp);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
