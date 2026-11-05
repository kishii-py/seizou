/*
 * 選ぶ欄 (05 の 2 節)。data-select-url の属性を持つ <select> に Tom Select (同梱の 2.4.3) を載せる。
 * 素の JS で書く (外の library は同梱の Tom Select だけ)。読み込みは base.html の Tom Select の <script> の次の 1 行。
 *
 * 候補は core の候補の口 (BaseSelectView) から q と page を付けて取る。返る形は
 * {"items": [{"id", "text", "title", "sub", ...}], "more": 真偽, "recent": [...]}。1 ページは 30 件 (口の per_page)。
 *
 * 決まり:
 * - 一覧を下まで読むと次のページを足す (virtual_scroll)。
 * - 打ってから 250ms 待って取りに行く。
 * - 欄に着いたら空の検索で最初のページを取る。打った字を全部消したときも空の検索で取り直す。
 * - 字を変えるたびに前の候補の控えを捨てて取り直す。照合はサーバーに任せ、欄の中では絞らない。
 * - 選んだ値は Tom Select の既定の描き方 (text の 1 行) で描く。候補は sub (下の段の字) のある物を 2 段 (上 = title、下 = sub。
 *   属性 data-select-title・data-select-sub。見た目は scripts/css/input.css) で描き、無い物 (受注の明細・発注の明細) は今の 1 行のまま。
 *   字は escape してから組む。取った字を HTML として差し込まない。打った字に合う所は部品の highlight が印を付ける (N162)。
 * - 通信に失敗したら「該当なし」に見せず、赤の知らせを欄の下に 1 回だけ出す。
 * - 同じ欄に 2 回載せない (載っていれば何もしない)。htmx:afterSettle で、差し替えた中の欄に載せる。
 * - 日本語の変換を確定する Enter (isComposing か keyCode 229) では何もしない。
 * - 候補の一覧は body の直下に描き、欄の位置に合わせて置く (小窓の中の欄だけ包みの中)。表の箱の scroll でも置き直す。
 * - 取りに行っている間の Enter は止め、返ってきた一覧の先頭を選ぶ。
 * - 1 つ選びの欄には消すボタン、複数選びの欄には 1 つずつ消すボタン。
 * - 小窓のある候補 (data-modal-url を持つ 1 つ選びの欄) では、選んだ物の下に「詳細を見る」の link を出し、
 *   押すと master_link と同じ小窓 (#seizou-modal) で開く。選び直すと link の先も変わり、消すと link も消える。
 *   入力の小窓 (#seizou-modal-box) の中の欄の link は、小窓を差し替えず新しいタブで開く (入れかけの値を消さない)。
 *   小窓の口は htmx でない要求を詳細の画面へ移すので、新しいタブには詳細の画面が出る。
 * - 候補に unit と standard_price のある物 (部品の候補の口が返す) を選んだら、同じ表の行 (tr) の name が -unit と
 *   -unit_price で終わる欄に写す (05 の 2 節の「選んだら写す」。規則は masters.services.copy_values と同じ:
 *   欄が空か前に写したままの値なら写し、人が打った字は残す)。写したら欄の下に「単位と単価を部品から写しました。」を出す。
 *   写した欄には input の event を送る (明細の金額の目安が数え直す)。単価の欄の無い人 (金額の閲覧なし) には口が単価を返さない。
 * - 字を打つ前の 1 ページ目は、口の返す recent (最近の受注・発注・出荷で使った物。5 件まで) を「最近使った」の見出しの群
 *   (Tom Select の optgroup) に入れて先頭に出す。同じ id の候補は 1 つにまとまり、最近使ったの群に残る。recent が空なら見出しを出さない (N162)。
 */
(function () {
  "use strict";

  var SELECTOR = "select[data-select-url]";
  var DELAY_MS = 250;
  var ERROR_TEXT = "候補を取れませんでした。通信を確かめて、欄を開き直してください。";
  // 候補の値の鍵 → 同じ行の欄の name の末尾と、知らせの字の名前
  var COPY_FIELDS = [
    { key: "unit", suffix: "-unit", label: "単位" },
    { key: "standard_price", suffix: "-unit_price", label: "単価" },
  ];
  var COPIED_TAIL = "を部品から写しました。";
  // 字を打つ前の 1 ページ目に、口の返す recent を入れる群の名前と見出しの字 (N162)
  var RECENT = "recent";
  var RECENT_LABEL = "最近使った";

  function pageUrl(base, query, page) {
    var url = new URL(base, window.location.href);
    url.searchParams.set("q", query);
    url.searchParams.set("page", String(page));
    return url.toString();
  }

  function pageOf(url) {
    var page = parseInt(new URL(url).searchParams.get("page"), 10);
    return isNaN(page) ? 1 : page;
  }

  function showError(state, ts) {
    if (state.errorShown) return;
    state.errorShown = true;
    var notice = document.createElement("p");
    notice.className = "alert alert-error alert-soft mt-2";
    notice.setAttribute("role", "alert");
    notice.textContent = ERROR_TEXT;
    ts.wrapper.insertAdjacentElement("afterend", notice);
    state.notice = notice;
  }

  function clearError(state) {
    state.failed = false;
    if (state.notice) {
      state.notice.remove();
      state.notice = null;
    }
    state.errorShown = false;
  }

  // 選んだ候補の値を同じ表の行の欄に写し、写した欄の名前 (単位・単価) の並びを返す。
  // 写すのは欄が空か、前に写したままの値 (data-copied と同じ字) のときだけ。人が打った字は残す。
  function copyFromOption(select, option) {
    var copied = [];
    var row = select.closest("tr");
    if (!option || !row) return copied;
    COPY_FIELDS.forEach(function (spec) {
      var value = option[spec.key];
      if (value === undefined || value === null || String(value).trim() === "") return;
      var input = row.querySelector('input[name$="' + spec.suffix + '"]');
      if (!input) return;
      var now = input.value.trim();
      if (now !== "" && now !== input.getAttribute("data-copied")) return;
      value = String(value);
      if (now === value) return;
      input.value = value;
      input.setAttribute("data-copied", value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      copied.push(spec.label);
    });
    return copied;
  }

  function attach(select) {
    if (!(select instanceof HTMLSelectElement) || select.tomselect) return;
    if (typeof window.TomSelect !== "function") return;
    var base = select.getAttribute("data-select-url");
    var state = { failed: false, errorShown: false, notice: null };
    var plugins = { virtual_scroll: {} };
    if (select.multiple) {
      plugins.remove_button = { title: "外す" };
    } else {
      plugins.clear_button = { title: "消す" };
    }
    var inDialog = !!select.closest("dialog");

    var ts = new window.TomSelect(select, {
      valueField: "id",
      labelField: "text",
      searchField: ["text"],
      plugins: plugins,
      // 候補の一覧を body の直下に描き、欄の位置に合わせて置く (表の overflow の箱に切られない。R3 の止まる)。
      // 小窓 (dialog) の中の欄は包みの中のまま (dialog は最上層に出るので、body の直下の一覧は裏に隠れる)。
      dropdownParent: inDialog ? null : "body",
      // 読み込み中に包みへ付く class を部品の既定の "loading" から変える (daisyUI の .loading = 丸い読み込みの印の形が
      // 包みに当たり、欄が正方形に伸びて候補の一覧が窓の下の端に置かれる。初見テスト R4 の 1)。
      loadingClass: "ts-loading",
      maxOptions: null,
      preload: "focus",
      // 打ってから取りに行くまでの待ちは loadThrottle の 250ms だけにする
      // (部品の既定は、打った後に refreshThrottle の 300ms を待ってから loadThrottle を待つ)。
      refreshThrottle: 0,
      loadThrottle: DELAY_MS,
      placeholder: select.getAttribute("data-placeholder") || "字を打って探す",
      // 空の字でも取りに行く (部品の既定は空の検索を取りに行かない)。
      shouldLoad: function () {
        return true;
      },
      // 照合はサーバーが行う。欄の中では絞らず、サーバーの並びのまま出す。
      score: function () {
        return function () {
          return 1;
        };
      },
      firstUrl: function (query) {
        return pageUrl(base, query, 1);
      },
      load: function (query, callback) {
        var self = this;
        var url = self.getUrl(query);
        window
          .fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
          .then(function (response) {
            if (!response.ok) throw new Error(String(response.status));
            return response.json();
          })
          .then(function (data) {
            clearError(state);
            if (data.more) self.setNextUrl(query, pageUrl(base, query, pageOf(url) + 1));
            // 字を打つ前の 1 ページ目だけ、最近使った物 (5 件まで) を見出しの群に入れて先頭に足す (N162)。
            // 同じ id の候補は部品が 1 つにまとめる (先に足した最近使ったの群に残る)。無ければ見出しを出さない。
            var recent = query === "" && pageOf(url) === 1 && Array.isArray(data.recent) ? data.recent : [];
            var options = recent.map(function (option) {
              return Object.assign({}, option, { optgroup: RECENT });
            });
            // 選んだ候補は字を変えても部品が残す (virtual_scroll の clearFilter)。最近使ったから選んだ物が、字を打った後も
            // 見出しの下に残らないよう、最近使ったの無い 1 ページ目では群を外す (続きのページでは外さない)。
            if (pageOf(url) === 1 && !recent.length) self.clearOptionGroups();
            callback(options.concat(data.items || []), recent.length ? [{ value: RECENT, label: RECENT_LABEL }] : []);
          })
          .catch(function () {
            state.failed = true;
            showError(state, self);
            callback();
          });
      },
      render: {
        // 下の段の字 (sub) のある候補 (部品・取引先) は 2 段。上の段は title、下の段は sub (N162)。無い物は今の 1 行のまま。
        option: function (data, escape) {
          if (!data.sub) return "<div>" + escape(data.text) + "</div>";
          return (
            "<div><div data-select-title>" +
            escape(data.title || data.text) +
            "</div><div data-select-sub>" +
            escape(data.sub) +
            "</div></div>"
          );
        },
        no_results: function () {
          if (state.failed) return null;
          return '<div class="no-results">該当なし</div>';
        },
        loading_more: function () {
          return '<div class="loading-more-results">続きを取っています</div>';
        },
        no_more_results: function () {
          return null;
        },
      },
    });

    // 小窓のある候補では、選んだ物の下に「詳細を見る」の link (小窓) を出す。
    var modalTemplate = select.getAttribute("data-modal-url");
    if (modalTemplate && !select.multiple) {
      var link = null;
      var openModal = function (event) {
        event.preventDefault();
        if (!link) return;
        // 入力の小窓の中では、同じ箱に部品の小窓を入れると入力が消える (精査 A8 の 3 節の 1)。新しいタブで開く。
        if (link.closest("#seizou-modal-box")) {
          window.open(link.href, "_blank", "noopener");
          return;
        }
        if (!window.htmx) return;
        window.htmx
          .ajax("GET", link.getAttribute("data-url"), { target: "#seizou-modal-box", swap: "innerHTML" })
          .then(function () {
            var modal = document.getElementById("seizou-modal");
            if (modal && typeof modal.showModal === "function") modal.showModal();
          });
      };
      var syncLink = function () {
        var value = ts.getValue();
        if (!value) {
          if (link) {
            link.remove();
            link = null;
          }
          return;
        }
        if (!link) {
          link = document.createElement("a");
          link.className = "link mt-1 inline-block text-sm";
          link.href = "#";
          link.textContent = "詳細を見る";
          link.addEventListener("click", openModal);
          ts.wrapper.insertAdjacentElement("afterend", link);
        }
        link.setAttribute("data-url", modalTemplate.replace("/0/", "/" + encodeURIComponent(value) + "/"));
        link.href = link.getAttribute("data-url");
      };
      ts.on("change", syncLink);
      syncLink();
    }

    // 部品の候補を選んだら、同じ明細の行の単位と単価の欄に写し、欄の下に知らせを 1 つ出す (選び直すと出し直す)。
    if (!select.multiple) {
      var copyNote = null;
      ts.on("change", function () {
        if (copyNote) {
          copyNote.remove();
          copyNote = null;
        }
        var value = ts.getValue();
        var copied = value ? copyFromOption(select, ts.options[value]) : [];
        if (!copied.length) return;
        copyNote = document.createElement("p");
        copyNote.className = "mt-2 text-xs text-base-content/50";
        copyNote.setAttribute("role", "status");
        copyNote.textContent = copied.join("と") + COPIED_TAIL;
        ts.wrapper.parentNode.appendChild(copyNote);
      });
    }

    // 日本語の変換を確定する Enter は部品に渡さない (候補を選ばず、form も送らない)。
    // 候補を取りに行っている間 (ts.loading が 0 より大きい) の Enter も部品に渡さず、返ってきた一覧の先頭を選ぶ
    // (前の字の一覧の先頭を黙って選ばない。初見テスト R3 の「止まる」と、最初から値の入っている欄の Enter)。
    var enterWaiting = false;
    ts.wrapper.addEventListener(
      "keydown",
      function (event) {
        if (event.key !== "Enter") return;
        if (event.isComposing || event.keyCode === 229) {
          event.stopPropagation();
          return;
        }
        // 字を打っていない (欄に着いた時の空の検索・字を全部消した) 間の Enter は今のまま部品に渡す
        // (待たせると、返ってきた全件の先頭を黙って選ぶため)。
        if (ts.loading > 0 && ts.control_input.value.trim() !== "") {
          event.preventDefault();
          event.stopPropagation();
          enterWaiting = true;
        }
      },
      true
    );
    ts.on("load", function (options) {
      if (!enterWaiting || ts.loading > 0) return;
      enterWaiting = false;
      if (options && options.length) ts.addItem(String(options[0].id));
    });
    ts.on("blur", function () {
      enterWaiting = false;
    });
  }

  // 表の箱を横に送ったときも、body の直下に描いた一覧を欄に付いてこさせる
  // (部品は画面の scroll と幅の変化でしか置き直さない。capture の scroll は泡立たない scroll も受ける)。
  function reposition(event) {
    var target = event.target;
    if (target && target.closest && target.closest(".ts-dropdown")) return;
    document.querySelectorAll(SELECTOR).forEach(function (select) {
      var ts = select.tomselect;
      if (ts && ts.isOpen && ts.settings.dropdownParent === "body") ts.positionDropdown();
    });
  }

  function init(root) {
    var scope = root && root.querySelectorAll ? root : document;
    if (scope instanceof HTMLSelectElement && scope.matches(SELECTOR)) {
      attach(scope);
      return;
    }
    scope.querySelectorAll(SELECTOR).forEach(attach);
  }

  window.seizouSelect = { init: init };

  document.addEventListener("scroll", reposition, true);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      init(document);
    });
  } else {
    init(document);
  }

  document.addEventListener("htmx:afterSettle", function (event) {
    // 差し替えた中の欄に載せる。差し替えた要素が既に外れていれば、ページの全部を見る (載った欄は飛ばす)。
    var root = event.detail && event.detail.elt;
    init(root && root.isConnected ? root : document);
  });
})();
