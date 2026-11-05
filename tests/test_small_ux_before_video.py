"""撮影の前の小さい直し 5 つ (決め 11-05 15:0x。N160) の試験。

1 = 「AI に質問」の丸いボタンと本文の下の余白、2 = 受信した注文の空の枠、3 = 生産管理ボードの行のメニューの見出しと線、
4 = 受信した注文の未処理の行の太字と点、5 = 読み取りの「読み取る」の回る印。作り物のデータだけを使う。
"""

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.connect.models import Inbox
from seizou.masters import services as masters_services
from tests.test_intake_extract import FAKE_KEY
from tests.test_orders_board_menu_more import _tr

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "seizou"
TOKYO = ZoneInfo("Asia/Tokyo")
HX = {"HTTP_HX_REQUEST": "true"}
EMPTY_LINE = "取引先から注文が届くと、ここに並びます。"
BUSY = '<template data-intake-busy><span class="loading loading-spinner loading-sm" aria-hidden="true"></span>読み取り中…</template>'
TOGGLE = (
    '<span class="tooltip" data-tip="AI に質問"><button id="assistant-toggle" type="button" class="btn btn-neutral btn-circle shadow-lg'
    ' focus-visible:outline-2 focus-visible:outline-offset-2" aria-expanded="false" aria-controls="assistant-panel" aria-label="AI に質問">'
)
DOT = '<span class="status status-info" aria-hidden="true"></span>'


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)


@pytest.fixture
def without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# 2. 受信した注文の空の枠


def test_inbox_empty_says_orders_arrive_and_links_connections(logged_in):
    # 画面の全体・行の部分・2 秒ごとの取りに行き (after=0) の 3 つとも同じ枠 (取りに行きで前の枠に戻らない)。
    url = reverse("connect:inbox_list")
    whole = logged_in.get(url).content.decode()
    partial = logged_in.get(url, **HX).content.decode()
    poll = logged_in.get(url, {"after": "0"}, **HX).content.decode()
    link = f'href="{reverse("connect:connection_list")}"'

    for body in (whole, partial, poll):
        assert body.count(EMPTY_LINE) == 1
        assert "まだ 1 件もありません。" in body
        assert link in body
        assert "絞りの条件に" not in body
    # 行の部分と取りに行きは、次の取りに行く要素を入れ直す (取りに行きが続く)。
    for body in (partial, poll):
        assert 'id="inbox-poll"' in body and 'hx-swap-oob="true"' in body


def test_inbox_filtered_empty_keeps_core_sentence(logged_in):
    # 絞りで 0 件のときは core の「絞りの条件に合う物はありません。」のまま (取りに行きも同じ)。
    url = reverse("connect:inbox_list")
    rejected = {"f_status": Inbox.STATUS_REJECTED}
    whole = logged_in.get(url, rejected).content.decode()
    poll = logged_in.get(url, {"after": "0", **rejected}, **HX).content.decode()

    for body in (whole, poll):
        assert "絞りの条件に合う物はありません。" in body
        assert EMPTY_LINE not in body


# 5. 読み取りの「読み取る」の回る印


def test_upload_page_swaps_button_to_spinner(logged_in, with_key):
    body = logged_in.get(reverse("intake:extraction_create")).content.decode()

    assert body.count(BUSY) == 1
    # 包みのすぐ内側が入力の form (上の帯にもログアウトの <form method="post" があるので、位置の前後で比べない)。
    assert re.search(r'<div data-intake-upload>\s*<form method="post" class="rounded-xl', body)
    assert "読み取る</button>" in body  # ボタンの字は今のまま
    script = body[body.index(BUSY) : body.index("</script>", body.index(BUSY))]
    for needed in (
        'addEventListener("DOMContentLoaded"',
        'addEventListener("submit"',
        "event.submitter",
        "defaultPrevented",
        "setTimeout",
        "replaceChildren",
        "[data-intake-upload]",
    ):
        assert needed in script, needed
    for banned in ("innerHTML", "classList", "className", "処理中…", "fetch("):
        assert banned not in script, banned
    # guard (defer) の読み込みはこの script より前 (受けの順は DOMContentLoaded で guard の後になる)。
    assert body.index("js/seizou-guard.js") < body.index(BUSY)
    # 頼る core の字 (押せなくする・2 回目の止めの印・ブラウザの戻るで元の字に戻す) を見張る。
    guard = (SRC / "core/static/js/seizou-guard.js").read_text(encoding="utf-8")
    for needed in (
        'form.dataset.seizouSent = "1";',
        "button.disabled = true;",
        "button.dataset.seizouLabel = button.innerHTML;",
        "button.innerHTML = button.dataset.seizouLabel;",
    ):
        assert needed in guard, needed


def test_upload_page_without_key_has_no_spinner(logged_in, without_key):
    body = logged_in.get(reverse("intake:extraction_create")).content.decode()

    assert "data-intake-busy" not in body
    assert "<fieldset disabled" in body


# 1. 「AI に質問」の丸いボタンと本文の下の余白


def test_assistant_toggle_is_small_circle_with_tooltip(logged_in):
    html = logged_in.get(reverse("core:home")).content.decode()

    assert html.count(TOGGLE) == 1
    inner = re.search(r'<button id="assistant-toggle"[^>]*>(.*?)</button>', html, re.S).group(1)
    assert "<svg" in inner
    assert re.sub(r"<svg.*?</svg>", "", inner, flags=re.S).strip() == ""  # 見える字は無い (吹き出しと aria-label だけ)
    assert ">AI に質問</button>" not in html
    # 根の div (右下の位置) は今のまま。
    assert html.count('<div id="assistant-root" class="fixed bottom-6 right-6 z-40 flex flex-col items-end gap-2 print:hidden">') == 1


def test_main_bottom_room_is_one_button_taller(logged_in):
    html = logged_in.get(reverse("core:home")).content.decode()

    assert '<main class="flex-1 p-4 pb-34 lg:p-6 lg:pb-34">' in html
    # Tailwind はテンプレートの注釈の字も拾うので、使わなくなった字を残さない。
    for path in SRC.rglob("*.html"):
        assert "pb-24" not in path.read_text(encoding="utf-8"), path


# 4. 受信した注文の未処理の行の太字と点


def test_inbox_unprocessed_rows_bold_with_blue_dot(logged_in, user, customer):
    done = connect_services.record_inbox(customer, {"order_no": "X-1", "lines": []}, user, received_at=datetime(2026, 11, 5, 9, 0, tzinfo=TOKYO))
    waiting = connect_services.record_inbox(customer, {"order_no": "X-2", "lines": []}, user, received_at=datetime(2026, 11, 5, 10, 0, tzinfo=TOKYO))
    connect_services.reject(done, "作り物の断りの理由", user)  # 取引先との接続を置かないので、相手への送信は無い
    url = reverse("connect:inbox_list")
    body = logged_in.get(url).content.decode()

    waiting_tr = _tr(body, waiting.pk)
    done_tr = _tr(body, done.pk)
    assert waiting_tr.startswith(f'<tr id="row-{waiting.pk}" class="row-hover h-10 cursor-pointer font-bold" data-row-menu')
    assert done_tr.startswith(f'<tr id="row-{done.pk}" class="row-hover h-10 cursor-pointer" data-row-menu')
    # 最初の欄 (受信日時。東京の時刻で出るので字の形で見る) の字の前に点。断った行には無い。
    waiting_first = re.search(r"<td[^>]*>(.*?)</td>", waiting_tr, re.S).group(1)
    done_first = re.search(r"<td[^>]*>(.*?)</td>", done_tr, re.S).group(1)
    assert re.search(re.escape(DOT) + r" \d{4}-\d{2}-\d{2} \d{2}:\d{2}</a>", waiting_first)
    assert DOT not in done_first

    # 2 秒ごとに先頭に足す行 (同じ 1 行のテンプレート) も同じ。
    newer = connect_services.record_inbox(customer, {"order_no": "X-3", "lines": []}, user, received_at=datetime(2026, 11, 5, 11, 0, tzinfo=TOKYO))
    poll = logged_in.get(url, {"after": str(waiting.pk)}, **HX).content.decode()
    newer_tr = _tr(poll, newer.pk)
    assert 'class="row-hover h-10 cursor-pointer font-bold"' in newer_tr
    assert DOT in re.search(r"<td[^>]*>(.*?)</td>", newer_tr, re.S).group(1)
