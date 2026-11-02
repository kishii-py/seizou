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
 * - 1 つ選びの欄には消すボタン、複数選びの欄には 1 つずつ消すボタン。
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

    var ts = new window.TomSelect(select, {
      valueField: "id",
      labelField: "text",
      searchField: ["text"],
      plugins: plugins,
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

    // 日本語の変換を確定する Enter は部品に渡さない (候補を選ばず、form も送らない)。
    ts.wrapper.addEventListener(
      "keydown",
      function (event) {
        if (event.key === "Enter" && (event.isComposing || event.keyCode === 229)) {
          event.stopPropagation();
        }
      },
      true
    );
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
