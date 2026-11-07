/*
 * 上の帯の「現品票を読む」の小窓のカメラ (workorders/_workorder_reader_modal.html の [data-qr-reader] の節)。素の JS。
 * QR の映像を読む部品は同梱の jsQR (core/static/vendor/jsQR.js。窓の jsQR 関数)。小窓の中身でこの script の前に読む。
 * htmx が小窓の中身を差し替えるたびに、中の script をもう一度動かす (読み取れない QR の帯付きで描き直したときも、カメラがもう一度始まる)。
 *
 * 約束:
 * - 動いたら、#seizou-modal-box の中の [data-qr-reader] のうち、まだ始めていない物 (data-qr-started の無い物) を 1 つ取り、印を付ける。何回動いても 2 回始めない。
 * - 映像は端末の中だけで読み、サーバーへ送るのは読めた QR の字だけにする (映像も字も残さない)。
 * - https の住所でない (isSecureContext が偽) → insecure の帯。getUserMedia か jsQR が無い → nocamera。
 *   カメラを断られた (NotAllowedError・SecurityError) → denied。ほかの失敗 (NotFoundError など) → nocamera。どれも映像の枠は出さない。
 * - 帯の字はサーバーが前もって描いた [data-qr-message] の包みの hidden を外すだけ (字を JS に書かない)。
 * - 映ったら 200ms ほどごとに 1 枚を canvas (幅 640px まで) に描いて読む。読めたら 1 回だけ、カメラを止め、
 *   data-reader-url に ?code=<読めた字> を付けて htmx で小窓の中身を替える (道の字を JS に書かない。見分けるのはサーバー)。
 * - 止める: 読めた・小窓が閉じた (close を 1 回だけ受ける)・節が DOM から外れた (毎回の読みの前に isConnected を見る)・ページを離れる (pagehide)。
 *   止めるときは映像の track を全部 stop し、srcObject を空にし、時計を止める。
 * - class を触らない (class の一覧と class の字を JS で替えない)。字を HTML として差し込まない。hidden の出し入れだけにする。
 * - カメラを使えないときは「指示書」の欄に focus を移す (字を打って探す欄が載っていれば。まだなら何もしない)。
 */
(function () {
  "use strict";

  var INTERVAL_MS = 200;
  var MAX_WIDTH = 640;

  var box = document.getElementById("seizou-modal-box");
  if (!box) return;
  var section = box.querySelector("[data-qr-reader]:not([data-qr-started])");
  if (!section) return;
  section.setAttribute("data-qr-started", "");

  var modal = document.getElementById("seizou-modal");
  var video = section.querySelector("video");
  var hint = section.querySelector("[data-qr-hint]");
  var stream = null;
  var timer = null;
  var finished = false;
  var canvas = null;
  var context = null;

  function stopStream(media) {
    media.getTracks().forEach(function (track) {
      track.stop();
    });
  }

  // カメラと時計を止め、受けている合図を外す。何回呼んでもよい。
  function finish() {
    finished = true;
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    if (stream) {
      stopStream(stream);
      stream = null;
    }
    if (video) video.srcObject = null;
    if (modal) modal.removeEventListener("close", finish);
    window.removeEventListener("pagehide", finish);
  }

  function focusField() {
    var select = box.querySelector('select[name="workorder"]');
    if (select && select.tomselect) select.tomselect.focus();
  }

  // カメラを使えない: 映像の枠と案内を隠し、前もって描いた帯を 1 つ出す。
  function fail(name) {
    finish();
    if (video) video.hidden = true;
    if (hint) hint.hidden = true;
    var message = section.querySelector('[data-qr-message="' + name + '"]');
    if (message) message.hidden = false;
    focusField();
  }

  function send(text) {
    finish();
    var url = section.getAttribute("data-reader-url");
    if (!url || !window.htmx) return;
    var query = new URLSearchParams({ code: text }).toString();
    // 差し替えの形は htmx の既定 (箱の中身を入れ替える。同梱の htmx の defaultSwapStyle)。この script には差し込みの形の名前も書かない。
    window.htmx.ajax("GET", url + (url.indexOf("?") === -1 ? "?" : "&") + query, { target: "#seizou-modal-box" });
  }

  function readFrame() {
    var width = video.videoWidth;
    var height = video.videoHeight;
    if (video.readyState < 2 || !width || !height) return null;
    var scale = Math.min(1, MAX_WIDTH / width);
    width = Math.round(width * scale);
    height = Math.round(height * scale);
    if (!canvas) {
      canvas = document.createElement("canvas");
      context = canvas.getContext("2d", { willReadFrequently: true });
    }
    if (!context) return null;
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    context.drawImage(video, 0, 0, width, height);
    var image = context.getImageData(0, 0, width, height);
    // 現品票は白地に黒の QR なので、白黒を反した読みはしない。
    return window.jsQR(image.data, width, height, { inversionAttempts: "dontInvert" });
  }

  function tick() {
    timer = null;
    if (finished) return;
    if (!section.isConnected) {
      finish();
      return;
    }
    var found = readFrame();
    if (found && found.data) {
      send(found.data);
      return;
    }
    timer = setTimeout(tick, INTERVAL_MS);
  }

  if (!window.isSecureContext) {
    fail("insecure");
    return;
  }
  if (
    !video ||
    !navigator.mediaDevices ||
    typeof navigator.mediaDevices.getUserMedia !== "function" ||
    typeof window.jsQR !== "function"
  ) {
    fail("nocamera");
    return;
  }

  if (modal) modal.addEventListener("close", finish, { once: true });
  window.addEventListener("pagehide", finish);

  navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false }).then(
    function (media) {
      // 許しを待つ間に小窓が閉じた・中身が替わったときは、受けたカメラをすぐ止める。
      if (finished || !section.isConnected) {
        stopStream(media);
        finish();
        return;
      }
      stream = media;
      video.srcObject = media;
      video.hidden = false;
      if (hint) hint.hidden = false;
      var played = video.play();
      if (played && typeof played.catch === "function") played.catch(function () {});
      timer = setTimeout(tick, INTERVAL_MS);
    },
    function (error) {
      if (finished) return;
      var name = error && error.name;
      fail(name === "NotAllowedError" || name === "SecurityError" ? "denied" : "nocamera");
    }
  );
})();
