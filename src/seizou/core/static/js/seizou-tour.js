/*
 * ガイドツアー (「案内」の小窓の「順に見る」)。読み込みは core/_tour.html の 1 行 (段のある画面だけ)。素の JS で書く (外の library を使わない)。
 * markup の約束 (core/_tour.html): #seizou-tour = 段の JSON ([{"selector", "title", "text"}, ...])。#seizou-tour-root (hidden) の中に
 *   [data-tour-ring] (枠) と [data-tour-bubble] (吹き出し。[data-tour-count]・[data-tour-title]・[data-tour-text] の字と、
 *   [data-tour-close]・[data-tour-prev]・[data-tour-next] のボタン)。始めるボタンは [data-tour-start] (core/_screen_guide.html)。
 * 動き:
 * - 始める: selector の要素がある段だけを残し (0 段なら何もしない)、開いている小窓 (#seizou-modal) を閉じ、
 *   root を body の末尾へ 1 回だけ移して (上の帯の sticky と左のメニューの重なりの外へ)、hidden を外す。
 * - 段を出す: 要素を画面の中央へ scroll し、枠を要素の外側 4px に置く (線と外の暗さは box-shadow。theme の変数だけ)。
 *   吹き出しは要素の下 (入らなければ上) に、画面の端から 8px 空けて収める。字は textContent で入れる。
 *   最初の段は「前へ」を押せず、最後の段は「次へ」が「終わる」になる (押すと閉じる)。
 * - 次へ・前へ・閉じる のボタンと Esc (→ と ← も。字を打つ欄の中では見ない)。大きさが変わるか scroll したら置き直す。
 *   閉じたら hidden を戻し、inline の style を消し、上の帯の「案内」([data-tour="guide"]) に focus を戻す。
 * - document に委譲する (htmx が小窓に差し込んだボタンにも効く)。class を足さない (Tailwind が CSS に入れないため)。取った字を
 *   HTML として差し込まない。通信しない。1 回見たかを覚えない。公開する口は seizouTour の start と close だけ。
 */
(function () {
  "use strict";

  var EDGE = 8, PAD = 4, GAP = 12; // 画面の端から空ける px・要素と枠の間の px・枠と吹き出しの間の px
  var SHADOW = "0 0 0 4px var(--color-primary), 0 0 0 9999px color-mix(in oklab, var(--color-base-content) 50%, transparent)";

  var steps = [];
  var index = 0;
  var root = null;
  var nextLabel = null; // 「次へ」のボタンの元の中身 (字と絵)。最後の段から戻るときに戻す

  function part(name) {
    return root.querySelector("[data-tour-" + name + "]");
  }

  function target(step) {
    return document.querySelector(step.selector); // selector は core.tours の固定の字 (試験が形を見る)
  }

  function readSteps() {
    var source = document.getElementById("seizou-tour");
    var list;
    try {
      list = JSON.parse(source ? source.textContent : "[]");
    } catch (error) {
      return [];
    }
    if (!Array.isArray(list)) return [];
    return list.filter(function (step) {
      return step && typeof step.selector === "string" && target(step) !== null;
    });
  }

  function isOpen() {
    return root !== null && !root.hidden;
  }

  function copies(nodes) {
    return nodes.map(function (node) {
      return node.cloneNode(true);
    });
  }

  function place() {
    var element = target(steps[index]);
    if (!element) return;
    var rect = element.getBoundingClientRect();
    var ring = part("ring").style;
    ring.top = rect.top - PAD + "px";
    ring.left = rect.left - PAD + "px";
    ring.width = rect.width + PAD * 2 + "px";
    ring.height = rect.height + PAD * 2 + "px";
    ring.boxShadow = SHADOW;
    var bubble = part("bubble");
    var width = bubble.offsetWidth;
    var height = bubble.offsetHeight;
    var top = rect.bottom + PAD + GAP;
    if (top + height > window.innerHeight - EDGE) top = rect.top - PAD - GAP - height;
    top = Math.max(EDGE, Math.min(top, window.innerHeight - height - EDGE));
    var left = Math.max(EDGE, Math.min(rect.left, window.innerWidth - width - EDGE));
    bubble.style.top = top + "px";
    bubble.style.left = left + "px";
  }

  function show(next) {
    index = next;
    var step = steps[index];
    var element = target(step);
    if (element) element.scrollIntoView({ block: "center" });
    part("count").textContent = index + 1 + " / " + steps.length;
    part("title").textContent = step.title || "";
    part("text").textContent = step.text || "";
    part("prev").disabled = index === 0;
    var button = part("next");
    if (index === steps.length - 1) button.textContent = "終わる";
    else button.replaceChildren.apply(button, copies(nextLabel));
    place();
    button.focus({ preventScroll: true });
  }

  function start() {
    root = root || document.getElementById("seizou-tour-root");
    if (!root) return;
    steps = readSteps();
    if (!steps.length) return;
    var modal = document.getElementById("seizou-modal");
    if (modal && modal.open) modal.close();
    if (root.parentNode !== document.body) document.body.appendChild(root);
    if (!nextLabel) nextLabel = copies(Array.prototype.slice.call(part("next").childNodes));
    root.hidden = false;
    show(0);
  }

  function close() {
    if (!isOpen()) return;
    root.hidden = true;
    part("ring").removeAttribute("style");
    part("bubble").removeAttribute("style");
    var guide = document.querySelector('[data-tour="guide"]');
    if (guide) guide.focus();
  }

  function move(step) {
    var next = index + step;
    if (next >= steps.length) close();
    else if (next >= 0) show(next);
  }

  function replace() {
    if (isOpen()) place();
  }

  document.addEventListener("click", function (event) {
    var origin = event.target instanceof Element ? event.target : null;
    if (origin && origin.closest("[data-tour-start]")) return start();
    if (!origin || !isOpen() || !root.contains(origin)) return;
    if (origin.closest("[data-tour-close]")) close();
    else if (origin.closest("[data-tour-prev]")) move(-1);
    else if (origin.closest("[data-tour-next]")) move(1);
  });

  document.addEventListener("keydown", function (event) {
    if (!isOpen()) return;
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName || "");
    if (event.key === "Escape") close();
    else if (event.key === "ArrowRight" && !typing) move(1);
    else if (event.key === "ArrowLeft" && !typing) move(-1);
  });

  window.addEventListener("resize", replace);
  document.addEventListener("scroll", replace, true);

  window.seizouTour = { start: start, close: close };
})();
