/*
 * 選ぶ欄 (05 の 2 節)。data-select-url の属性を持つ <select> に Tom Select (同梱の 2.4.3) を載せる。
 * 素の JS で書く (外の library は同梱の Tom Select だけ)。読み込みは base.html の Tom Select の <script> の次の 1 行。
 *
 * 候補は core の候補の口 (BaseSelectView) から q と page を付けて取る。返る形は
 * {"items": [{"id", "text"}], "more": 真偽}。1 ページは 30 件 (口の per_page)。
 *
 * 決まり:
 * - 一覧を下まで読むと次のページを足す (virtual_scroll)。
 * - 打ってから 250ms 待って取りに行く。
 * - 欄に着いたら空の検索で最初のページを取る。打った字を全部消したときも空の検索で取り直す。
 * - 字を変えるたびに前の候補の控えを捨てて取り直す。照合はサーバーに任せ、欄の中では絞らない。
 * - 候補と選んだ値は Tom Select の既定の描き方で描く (字を escape する)。取った字を HTML として差し込まない。
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
 */
(function () {
  "use strict";

  var SELECTOR = "select[data-select-url]";
  var DELAY_MS = 250;
  var ERROR_TEXT = "候補を取れませんでした。通信を確かめて、欄を開き直してください。";

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
            callback(data.items || []);
          })
          .catch(function () {
            state.failed = true;
            showError(state, self);
            callback();
          });
      },
      render: {
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
