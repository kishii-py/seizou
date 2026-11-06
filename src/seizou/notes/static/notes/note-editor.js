// 書式つきのメモ (notes/note_form.html の [data-note-editor]) の編集の箱を、同梱の Quill 2 で起こす。
// 起こすのは、画面を読んだ時 (記入の形の画面を直に開いた時) と、htmx が #notes を入れ替えた時 (詳細の画面の「書く」)。
// 起こした後は箱に focus を入れ、カーソルを本文の終わりに置く (閲覧の形の枠は起こさないので、詳細を開いただけでは focus を取らない)。
// 本文は打つたびに Quill の getSemanticHTML (ul と ol の素の HTML) を隠した欄 body_html に写す。掃除は保存の時に services がする。
// 写真は 1 枚ずつ写真の口 (data-upload-url) へ送り、返った URL (/media/notes/…) を本文に入れる (道具の列の「写真」・落とす・貼り付け)。
// CSRF は form の csrfmiddlewaretoken の欄の値を送る (見出しは土台の 1 か所 = R18)。class を足し引きしない (Quill の class は Quill の物)。
(function () {
  "use strict";

  var FORMATS = ["header", "bold", "italic", "underline", "strike", "color", "list", "blockquote", "link", "image"];
  var IMAGE_TYPES = "image/png,image/jpeg,image/webp,image/gif";
  var SEND_ERROR = "写真を送れませんでした。通信を確かめて、もう一度貼ってください。";
  var ready = typeof WeakSet === "function" ? new WeakSet() : null;

  function showError(editor, text) {
    var form = editor.closest("form");
    var box = form && form.querySelector("[data-note-error]");
    if (!box) return;
    box.textContent = text || "";
    box.hidden = !text;
  }

  function field(form, name) {
    return form.querySelector('[name="' + name + '"]');
  }

  function upload(editor, quill, file) {
    var form = editor.closest("form");
    var data = new FormData();
    data.append("file", file);
    data.append("content_type", field(form, "content_type").value);
    data.append("object_id", field(form, "object_id").value);
    data.append("csrfmiddlewaretoken", field(form, "csrfmiddlewaretoken").value);
    showError(editor, "");
    return fetch(editor.getAttribute("data-upload-url"), {
      method: "POST",
      body: data,
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (response) {
        return response.json().then(function (body) {
          return { ok: response.ok, body: body };
        });
      })
      .then(function (result) {
        if (!result.ok || !result.body || typeof result.body.url !== "string") {
          showError(editor, (result.body && result.body.error) || SEND_ERROR);
          return;
        }
        var range = quill.getSelection(true);
        var index = range ? range.index : quill.getLength();
        quill.insertEmbed(index, "image", result.body.url, "user");
        quill.setSelection(index + 1, 0, "silent");
      })
      .catch(function () {
        showError(editor, SEND_ERROR);
      });
  }

  function pickImage(editor, quill) {
    var input = document.createElement("input");
    input.type = "file";
    input.accept = IMAGE_TYPES;
    input.addEventListener("change", function () {
      if (input.files && input.files[0]) upload(editor, quill, input.files[0]);
    });
    input.click();
  }

  function setUp(editor) {
    if (!window.Quill || (ready && ready.has(editor))) return;
    if (ready) ready.add(editor);
    var form = editor.closest("form");
    var hidden = field(form, "body_html");
    var box = editor.querySelector("[data-note-box]");
    var toolbar = editor.querySelector("[data-note-toolbar]");
    var quill = null;
    quill = new window.Quill(box, {
      theme: "snow",
      formats: FORMATS,
      placeholder: "申し送り・段取りの注意・現場の写真・決めた事などを書きます。",
      modules: {
        toolbar: {
          container: toolbar,
          handlers: {
            image: function () {
              pickImage(editor, quill);
            },
            undo: function () {
              quill.history.undo();
            },
          },
        },
        uploader: {
          mimetypes: IMAGE_TYPES.split(","),
          handler: function (range, files) {
            files.forEach(function (file) {
              upload(editor, quill, file);
            });
          },
        },
      },
    });
    function copy() {
      // getSemanticHTML (Quill 2.0.3) は空白を全部 &nbsp; にするので、素の空白に戻す (閲覧の形の .ql-editor は空白を詰めない)。
      hidden.value = quill.getLength() > 1 ? quill.getSemanticHTML().replace(/&nbsp;/g, " ") : "";
    }
    quill.on("text-change", copy);
    copy();
    // 起こした箱に focus を入れ、カーソルを本文の終わりに置く (「書く」の後に箱を押さずに書き足せる。初見テスト R5 の U163)。
    // 先に "silent" の setSelection で終わりへ置き、後の focus() でその位置を画面に送る (逆の順だと focus() が頭へ送る)。
    quill.setSelection(quill.getLength(), 0, "silent");
    quill.focus();
  }

  function setUpAll(root) {
    var scope = root && root.querySelectorAll ? root : document;
    if (scope.matches && scope.matches("[data-note-editor]")) setUp(scope);
    scope.querySelectorAll("[data-note-editor]").forEach(setUp);
  }

  document.addEventListener("htmx:load", function (event) {
    setUpAll(event.target);
  });
  setUpAll(document);
})();
