/*
 * ファイルの欄のドラッグの枠 (決め 11-04 14:4x)。読み込みは base.html の seizou-guard.js の次の 1 行。
 * 素の JS で書く (外の library を使わない。htmx は読まない)。markup の約束は core/_file_field.html の注釈。
 *
 * - 枠 ([data-file-drop] の中に <input type=file> が 1 つ) の上のドラッグ (ファイルを持つときだけ): 既定の動き
 *   (ブラウザがファイルを開く) を止め、枠に data-tone="primary" を付ける。枠の外へ出たときと放したときに外す。
 * - 枠へ放す: 放したファイルを DataTransfer で input.files に入れ、change を出す。multiple の無い欄は 1 つ目だけ
 *   (選ぶ画面で 1 つしか選べないのと同じ)。形式・大きさ・枚数は見ない (サーバーの form と services が見て、
 *   超えたら今の赤の知らせ)。押せない欄 (disabled・押せない fieldset の中) には入れない。
 * - 枠の押下: input・link・label・button の外を押したら、input.click() で選ぶ画面を開く
 *   (編集の画面の「現在」の link と「クリア」はそのまま動く)。
 * - document に委譲して付ける。htmx で差し替えた欄 (マニュアルの画像) にも効く。
 * - class を足さない (Tailwind が CSS に入れないため)。data- の属性と hidden の付け外しだけで動かす。
 * - 取った字を HTML として差し込まない。通信しない。公開する口は無い。
 */
(function () {
  "use strict";

  var DROP = "[data-file-drop]";
  var INPUT = 'input[type="file"]';
  var PASS = "input, a, label, button, select, textarea"; // 枠の押下で選ぶ画面を開かない物
  var FIELD = "[data-file-field]";
  var KB = 1024;
  var MB = 1024 * 1024;
  var IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];
  var thumbUrls = new WeakMap(); // 一覧 → 今出しているサムネの URL の並び (作り直すときに捨てる)

  function elementOf(node) {
    return node && node.nodeType === 1 ? node : null;
  }

  function inputOf(box) {
    return box.querySelector(INPUT);
  }

  function hasFiles(event) {
    var types = event.dataTransfer ? event.dataTransfer.types : null;
    if (!types) return false;
    for (var i = 0; i < types.length; i++) {
      if (types[i] === "Files") return true;
    }
    return false;
  }

  // ファイルを受ける枠。枠の外か、押せない欄なら null。
  function openBoxOf(event) {
    var target = elementOf(event.target);
    var box = target ? target.closest(DROP) : null;
    if (!box) return null;
    var input = inputOf(box);
    if (!input || input.matches(":disabled")) return null;
    return box;
  }

  function sizeText(bytes) {
    if (bytes < KB) return bytes + " バイト";
    if (bytes < MB) return (bytes / KB).toFixed(1) + " KB";
    return (bytes / MB).toFixed(1) + " MB";
  }

  function dropThumbs(list) {
    var urls = thumbUrls.get(list) || [];
    for (var i = 0; i < urls.length; i++) URL.revokeObjectURL(urls[i]);
    thumbUrls.set(list, []);
  }

  // 絵のファイルは、行の頭の img にサムネを出す (URL.createObjectURL。通信しない)。
  function showThumb(list, row, file) {
    var thumb = row.querySelector("[data-file-drop-thumb]");
    if (!thumb || IMAGE_TYPES.indexOf(file.type) === -1) return;
    var url = URL.createObjectURL(file);
    thumbUrls.get(list).push(url);
    thumb.src = url;
    thumb.alt = file.name;
    thumb.hidden = false;
  }

  // 選んだファイルの一覧を作り直す (枠の下。行は template を写し、名前と大きさを textContent で入れる)。
  function render(input) {
    var field = input.closest(FIELD);
    if (!field) return;
    var list = field.querySelector("[data-file-drop-list]");
    var template = field.querySelector("template[data-file-drop-item]");
    if (!list || !template) return;
    dropThumbs(list);
    list.replaceChildren();
    var files = input.files || [];
    for (var i = 0; i < files.length; i++) {
      var row = template.content.firstElementChild.cloneNode(true);
      row.querySelector("[data-file-drop-name]").textContent = files[i].name;
      row.querySelector("[data-file-drop-size]").textContent = sizeText(files[i].size);
      showThumb(list, row, files[i]);
      list.appendChild(row);
    }
    list.hidden = files.length === 0;
  }

  function onDragOver(event) {
    if (!hasFiles(event)) return;
    var box = openBoxOf(event);
    if (!box) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    box.setAttribute("data-tone", "primary");
  }

  function onDragLeave(event) {
    var target = elementOf(event.target);
    var box = target ? target.closest(DROP) : null;
    if (!box) return;
    var next = elementOf(event.relatedTarget);
    if (next && box.contains(next)) return; // 枠の中の子へ移っただけ
    box.removeAttribute("data-tone");
  }

  function onDrop(event) {
    if (!hasFiles(event)) return;
    var box = openBoxOf(event);
    if (!box) return;
    event.preventDefault();
    box.removeAttribute("data-tone");
    var dropped = event.dataTransfer.files;
    if (!dropped || dropped.length === 0) return;
    var input = inputOf(box);
    var transfer = new DataTransfer();
    var count = input.multiple ? dropped.length : 1;
    for (var i = 0; i < count; i++) transfer.items.add(dropped[i]);
    input.files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function onClick(event) {
    var target = elementOf(event.target);
    if (!target || target.closest(PASS)) return;
    var box = openBoxOf(event);
    if (box) inputOf(box).click();
  }

  function onChange(event) {
    var input = elementOf(event.target);
    if (!input || !input.matches(INPUT) || !input.closest(DROP)) return;
    render(input);
  }

  document.addEventListener("dragover", onDragOver);
  document.addEventListener("dragleave", onDragLeave);
  document.addEventListener("drop", onDrop);
  document.addEventListener("click", onClick);
  document.addEventListener("change", onChange);
})();
