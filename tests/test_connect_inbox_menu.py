"""受信した注文の画面 (名前・行の操作・状態の列・並び・自動の更新・詳細の注文回答) のテスト。core の土台の上。

受信した注文の行は、送る側と同じ形の注文のデータを署名して services.receive に渡して作る (test_connect_inbox_views.py と同じ形)。
相手への送信は無い (取引先に送る向きのつながりを置かない)。念のため requests.post を偽の物に差し替え、呼ばれないことを見る。
鍵はテストのたびに作り、環境変数に置く。固定の鍵の文字列をファイルに書かない。品番と会社名は作り物。
"""

import json
import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services, signing
from seizou.connect.models import Inbox
from seizou.masters.services import create_item, create_partner
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret
from tests.test_r10_inbox_accept import _line, _payload

ENV_NAME = "SEIZOU_PARTNER_MENUCO_SECRET"
ROW_MENU_JS = Path(__file__).resolve().parent.parent / "src/seizou/core/static/js/seizou-row-menu.js"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(ENV_NAME, value)
    return value


@pytest.fixture
def no_http(monkeypatch):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    yield fake
    assert fake.calls == []


@pytest.fixture
def partner(user):
    return create_partner({"code": "MC01", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return create_item(
        {"part_no": "B-200", "name": "作り物のブラケット", "unit": "個", "standard_price": Decimal("240")},
        user,
    )


@pytest.fixture
def receive_order(partner, user, secret, no_http):
    services.register_connection(partner, "", ENV_NAME, "receive", user)

    def _receive(lines):
        msg_id = str(uuid.uuid4())
        body = json.dumps(_payload(msg_id, lines), ensure_ascii=False).encode("utf-8")
        result = services.receive(body, signing.headers_for(secret, msg_id, body))
        assert isinstance(result, Inbox)
        return result

    return _receive


def _poll(client, after):
    return client.get(reverse("connect:inbox_list"), {"after": after}, HTTP_HX_REQUEST="true")


def _row(body, pk):
    """一覧の応答から、1 行 (<tr id="row-<pk>"> から </tr> まで) の HTML を取り出す。"""
    match = re.search(rf'<tr id="row-{pk}".*?</tr>', body, re.S)
    assert match, f"row-{pk} が無い"
    return match.group(0)


def _row_ids(body):
    return [int(pk) for pk in re.findall(r'<tr id="row-(\d+)"', body)]


def test_list_name_and_menu_label(client_logged_in, receive_order):
    receive_order([_line(1, "A-100")])

    body = client_logged_in.get(reverse("connect:inbox_list")).content.decode()
    home = client_logged_in.get(reverse("core:home")).content.decode()
    connections = client_logged_in.get(reverse("connect:connection_list")).content.decode()

    # 画面の名前は「受信した注文」(題と見出し)。URL の名前と道は変えない。
    assert "<title>受信した注文" in body
    assert '<h1 class="truncate text-3xl font-bold tracking-tight">受信した注文</h1>' in body
    assert reverse("connect:inbox_list") == "/connect/inbox/"
    assert reverse("connect:inbox_list") in home
    # 取引先との接続は、名前を変えても左のメニューに出さない (設定の画面から開く)
    assert "<title>取引先との接続" in connections
    assert reverse("connect:connection_list") not in home


def test_rows_have_menu_items_by_status(client_logged_in, receive_order, item, user):
    waiting = receive_order([_line(1, "A-100")])
    accepted = receive_order([_line(1, "A-100")])
    rejected = receive_order([_line(1, "A-100")])
    order = services.accept(accepted, {"A-100": item}, user)
    services.reject(rejected, "納期に間に合わないため", user)

    body = client_logged_in.get(reverse("connect:inbox_list")).content.decode()

    row = _row(body, waiting.pk)
    for url in (
        reverse("connect:inbox_accept", args=[waiting.pk]),
        reverse("connect:inbox_reject", args=[waiting.pk]),
        reverse("connect:inbox_detail", args=[waiting.pk]),
        reverse("masters:partner_modal", args=[waiting.partner_id]),
    ):
        assert f'href="{url}"' in row, url
    for word in ("承認", "却下", "詳細を見る", "取引先の小窓"):
        assert word in row, word
    assert '<span class="badge badge-soft badge-info">届いた</span>' in row

    row = _row(body, accepted.pk)
    assert reverse("connect:inbox_accept", args=[accepted.pk]) not in row
    assert reverse("connect:inbox_reject", args=[accepted.pk]) not in row
    assert f'href="{reverse("orders:order_detail", args=[order.pk])}"' in row
    assert order.order_no in row and "受け入れた" in row and "受注の詳細" in row
    assert reverse("connect:inbox_detail", args=[accepted.pk]) in row

    row = _row(body, rejected.pk)
    assert '<span class="badge badge-outline text-base-content/50">断った</span>' in row
    assert reverse("connect:inbox_accept", args=[rejected.pk]) not in row
    assert reverse("connect:inbox_detail", args=[rejected.pk]) in row
    # 表の見出しに「操作」の列、種類の列は無い (受信した注文は注文だけ)
    assert '<th class="whitespace-nowrap font-medium">操作</th>' in body
    assert "種類" not in body.split("<tbody>")[0].split("<table")[-1]


def test_received_rows_come_first(client_logged_in, receive_order, item, user):
    old_waiting = receive_order([_line(1, "A-100")])
    accepted = receive_order([_line(1, "A-100")])
    services.accept(accepted, {"A-100": item}, user)
    new_waiting = receive_order([_line(1, "A-100")])

    body = client_logged_in.get(reverse("connect:inbox_list")).content.decode()

    # 未承認 (届いた) を上に、その中は新しい順。受け入れた行はその後
    assert _row_ids(body) == [new_waiting.pk, old_waiting.pk, accepted.pk]


def test_polled_rows_carry_menu(client_logged_in, receive_order):
    first = receive_order([_line(1, "A-100")])
    second = receive_order([_line(1, "A-200")])

    body = _poll(client_logged_in, first.pk).content.decode()

    assert _row_ids(body) == [second.pk]
    row = _row(body, second.pk)
    assert reverse("connect:inbox_accept", args=[second.pk]) in row
    assert "row-menu" in row and "data-row-menu" in row
    # 先頭に足した行は欄の地に色が付く
    assert "bg-primary/10" in row
    # 次の取りに行きと「全 N 件」の入れ直しは今までどおり
    assert 'hx-swap-oob="true"' in body and f"?after={second.pk}" in body
    assert 'id="list-count"' in body and ">2</span>" in body

    # 一覧が空で開いた画面の取りに行き (after=0) は表ごと返り、見出しに「操作」の列がある
    whole = _poll(client_logged_in, 0).content.decode()
    assert "<table" in whole and "操作</th>" in whole
    assert reverse("connect:inbox_accept", args=[first.pk]) in _row(whole, first.pk)


def test_detail_shows_reply_state(client_logged_in, receive_order, user):
    inbox = receive_order([_line(1, "A-100")])
    url = reverse("connect:inbox_detail", args=[inbox.pk])

    body = client_logged_in.get(url).content.decode()

    assert re.search(r"<title>[^<]* \| 受信した注文の詳細", body)
    reply = re.search(r"<dt[^>]*>注文回答</dt>\s*<dd[^>]*>(.*?)</dd>", body, re.S)
    assert reply and "—" in reply.group(1)

    # 断った後。注文回答を返す向き (送る) の接続が無いので、届いていない理由が出る
    services.reject(inbox, "作り物の断りの理由", user)
    body = client_logged_in.get(url).content.decode()

    reply = re.search(r"<dt[^>]*>注文回答</dt>\s*<dd[^>]*>(.*?)</dd>", body, re.S)
    assert reply and "届いていない" in reply.group(1) and "取引先との接続" in reply.group(1)
    # 注文回答の番号 (uuid) は画面に出さない
    inbox.refresh_from_db()
    assert inbox.reply_message_id is not None
    assert str(inbox.reply_message_id) not in body


def test_row_menu_markup_follows_core_js(client_logged_in, receive_order):
    inbox = receive_order([_line(1, "A-100")])
    js = ROW_MENU_JS.read_text(encoding="utf-8")
    # 行と ⋮ の欄の選び方は JS の頭の定数 (tr[data-row-menu] と details.row-menu)
    row_attr = re.search(r'var ROW = "tr\[([a-z-]+)\]"', js).group(1)
    menu_class = re.search(r'var MENU = "details\.([a-z-]+)"', js).group(1)
    assert (row_attr, menu_class) == ("data-row-menu", "row-menu")

    row = _row(client_logged_in.get(reverse("connect:inbox_list")).content.decode(), inbox.pk)

    assert re.match(rf'<tr id="row-{inbox.pk}"[^>]* {row_attr}[ >]', row)
    assert f'<details class="dropdown dropdown-end {menu_class}">' in row
    assert '<summary class="btn btn-ghost btn-xs" aria-label="行の操作" title="行の操作">⋮</summary>' in row
    assert '<ul class="menu dropdown-content z-50 w-56 rounded-lg border border-base-300 bg-base-100 p-2 shadow-sm">' in row
    # 行の押下で詳細へ移る onclick は、link と ⋮ の欄の中の押下では動かない (JS の頭の comment の約束)
    assert "closest('a, details')" in row
    assert "closest('a, details')" in js
    # 取引先の小窓は master_link と同じ小窓を開く
    assert 'hx-target="#seizou-modal-box"' in row and "showModal()" in row
