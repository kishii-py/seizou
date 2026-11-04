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

  document.addEventListener("dragover", onDragOver);
  document.addEventListener("dragleave", onDragLeave);
  document.addEventListener("drop", onDrop);
  document.addEventListener("click", onClick);
})();
