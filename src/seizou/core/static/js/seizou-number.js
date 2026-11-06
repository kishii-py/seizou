/*
 * 数の欄 (core の DecimalInput。印 data-number) の字を、欄を離れた時に 3 桁ごとの区切りの字にそろえる
 * (初見テスト 5 回目 V73・U124。候補 C43。決め 11-06 18:1x「欄を離れた時に付けるで」)。素の JS (外の library を使わない)。
 * 読み込みは base.html の script の並びの最後の 1 行。
 *
 * 約束:
 * - document の focusout に委譲する (blur は泡立たないため。小窓の中身が htmx で差し替わっても動く)。
 * - 扱うのは input[data-number] だけ。一覧の絞りの数の範囲の欄と、整数の欄 (IntegerInput。締日など) は扱わない。
 * - 読み方はサーバーの normalize_number と seizou-submit-label.js の readNumber と同じ。全角を半角に (NFKC)、「,」を外し、
 *   前後の空白を落とし、^-?\d+(\.\d+)?$ に合うときだけ数として読む。読めない字 (「12e」「abc」) は触らない (誤りは form が日本語で出す)。
 * - 字の整え方は詳細の字 (seizou_tags.comma の Decimal の枝) と同じ。整数の部分に 3 桁ごとの区切りを付け、小数の部分の末尾の 0 を落とす
 *   (「2500」→「2,500」・「12.50」→「12.5」)。整数の部分の頭の 0 も落とす (「0012」→「12」。サーバーが数で読んで詳細に出す字と同じ)。
 *   丸めない (「12.3456」は「12.3456」のまま)。Number に直さず字のまま組む (toLocaleString は既定で小数 3 桁に丸めるため)。
 * - 字が変わる時だけ input.value を書く。input と change の event は送らない。数の値は変わらないので、保存のボタンの字と明細の金額は
 *   書き直さなくてよい。触っていない欄を Tab で通っただけで、seizou-form-guard.js が書きかけの印を付けないため。
 * - 外へ出す口は window.seizouNumber.format。seizou-select.js が部品の単価を数の欄に写す時に、離れた時と同じ字にそろえる。
 */
(function () {
  "use strict";

  var SELECTOR = "input[data-number]";
  var NUMBER = /^(-?)(\d+)(?:\.(\d+))?$/;

  // 字を詳細と同じ形にそろえた字を返す。数として読めない字は受けた字のまま返す。
  function format(text) {
    var raw = text === undefined || text === null ? "" : String(text);
    var match = NUMBER.exec(raw.normalize("NFKC").replace(/,/g, "").trim());
    if (!match) return raw;
    var whole = match[2].replace(/^0+(?=\d)/, "");
    var fraction = (match[3] || "").replace(/0+$/, "");
    var sign = whole === "0" && fraction === "" ? "" : match[1];
    return sign + whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (fraction === "" ? "" : "." + fraction);
  }

  document.addEventListener("focusout", function (event) {
    var input = event.target;
    if (!(input instanceof HTMLInputElement) || !input.matches(SELECTOR)) return;
    var next = format(input.value);
    if (next !== input.value) input.value = next;
  });

  window.seizouNumber = { format: format };
})();
