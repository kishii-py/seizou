"""connect の画面 (受信箱・受信箱の詳細・受け入れる・断る・つながり) のテスト。core の土台の上。

受信箱の行は、送る側と同じ形の注文のデータを署名して services.receive に渡して作る。
鍵はテストのたびに作り、環境変数に置く。固定の鍵の文字列をファイルに書かない。品番と会社名は作り物。
"""

import json
import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services, signing
from seizou.connect.forms import AcceptForm
from seizou.connect.models import Inbox
from seizou.masters.services import create_item, create_partner, map_partner_item
from seizou.orders.models import Order
from tests.test_connect_signing import _new_secret
from tests.test_r10_inbox_accept import _line, _payload

ENV_NAME = "SEIZOU_PARTNER_VIEWCO_SECRET"
UNSET_ENV_NAME = "SEIZOU_PARTNER_VIEWCO_UNSET_SECRET"


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
def partner(user):
    return create_partner({"code": "VC01", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def connection(partner, user, secret):
    return services.register_connection(partner, "", ENV_NAME, "receive", user)


@pytest.fixture
def item(user):
    return create_item(
        {"part_no": "B-200", "name": "作り物のブラケット", "unit": "個", "standard_price": Decimal("240")},
        user,
    )


@pytest.fixture
def receive_order(connection, secret):
    def _receive(lines):
        msg_id = str(uuid.uuid4())
        body = json.dumps(_payload(msg_id, lines), ensure_ascii=False).encode("utf-8")
        result = services.receive(body, signing.headers_for(secret, msg_id, body))
        assert isinstance(result, Inbox)
        return result

    return _receive


def _poll(client, after):
    return client.get(reverse("connect:inbox_list"), {"after": after}, HTTP_HX_REQUEST="true")


@pytest.mark.django_db
def test_inbox_list_requires_login(client):
    response = client.get(reverse("connect:inbox_list"))
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


def test_inbox_list_shows_summary_and_partner(client_logged_in, receive_order):
    inbox = receive_order([_line(1, "A-100"), _line(2, "A-200"), _line(3, "A-300")])

    response = client_logged_in.get(reverse("connect:inbox_list"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "作り物の得意先" in body
    assert "A-100 ×10 ほか 2 行" in body
    assert str(inbox.message_id) not in body
    # 2 秒ごとに、今の一番新しい行より新しい行を取りに行く要素がある
    assert f"?after={inbox.pk}" in body and "every 2s" in body


def test_poll_returns_only_newer_rows(client_logged_in, receive_order):
    first = receive_order([_line(1, "A-100")])
    second = receive_order([_line(1, "A-200")])

    body = _poll(client_logged_in, first.pk).content.decode()

    assert f'id="row-{second.pk}"' in body
    assert f'id="row-{first.pk}"' not in body
    assert "<html" not in body
    # 次の取りに行きは、返した行の番号から
    assert 'hx-swap-oob="true"' in body and f"?after={second.pk}" in body
    # 上の「全 N 件」も入れ替える (行だけ増えて件数が古いままにならない。初見テスト 2 回目の所見)
    assert 'id="list-count"' in body and ">2</span>" in body
    # 下の「N 件中 1–M 件」も入れ替える (M = 表に出ている行の数。動画の確かめの所見 11-05)
    assert 'id="list-range"' in body and "2</span> 件中 <span class=\"tabular-nums\">1–2</span> 件" in body
    # 足した行の最初の欄は詳細への link (キーボードでも移れる)
    assert f'href="/connect/inbox/{second.pk}/"' in body


def test_poll_without_new_rows_is_empty(client_logged_in, receive_order):
    only = receive_order([_line(1, "A-100")])

    body = _poll(client_logged_in, only.pk).content.decode()

    assert "<tr" not in body
    assert f"?after={only.pk}" in body


def test_poll_on_empty_list_returns_whole_table(client_logged_in, receive_order):
    # 一覧が空で開いた画面は after=0 で取りに行く。行が届いたら表ごと返し、次からは新しい行だけ。
    inbox = receive_order([_line(1, "A-100")])

    body = _poll(client_logged_in, 0).content.decode()

    assert "<table" in body and f'id="row-{inbox.pk}"' in body
    assert f"?after={inbox.pk}" in body


def test_inbox_detail_shows_lines_without_uuid(client_logged_in, receive_order, item, user):
    inbox = receive_order([_line(1, "A-100")])
    accept_url = reverse("connect:inbox_accept", args=[inbox.pk])

    body = client_logged_in.get(reverse("connect:inbox_detail", args=[inbox.pk])).content.decode()

    assert "A-100" in body and "作り物の部品 A-100" in body
    assert str(inbox.message_id) not in body
    assert accept_url in body
    services.accept(inbox, {"A-100": item}, user)
    body = client_logged_in.get(reverse("connect:inbox_detail", args=[inbox.pk])).content.decode()
    assert accept_url not in body
    assert Order.objects.get().order_no in body


def test_accept_screen_shows_partner_part_no_and_initial_item(
    client_logged_in, receive_order, partner, item, user
):
    create_item({"part_no": "B-300", "name": "作り物のシャフト", "unit": "個"}, user)
    map_partner_item(partner, item, "A-100", user)
    inbox = receive_order([_line(1, "A-100"), _line(2, "A-200")])

    response = client_logged_in.get(reverse("connect:inbox_accept", args=[inbox.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "作り物の得意先 の品番 A-100 (作り物の部品 A-100) に当たる自社の部品" in body
    assert "作り物の得意先 の品番 A-200 (作り物の部品 A-200) に当たる自社の部品" in body
    # 表に対応がある A-100 だけ、自社の品目が初めから選ばれている
    assert body.count(f'<option value="{item.pk}" selected>') == 1


def test_accept_post_creates_order_and_redirects(client_logged_in, receive_order, item):
    inbox = receive_order([_line(1, "A-100")])
    url = reverse("connect:inbox_accept", args=[inbox.pk])

    response = client_logged_in.post(url, {"item_1": item.pk})

    assert response.status_code == 302
    assert Order.objects.count() == 1
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_ACCEPTED

    again = client_logged_in.post(url, {"item_1": item.pk})

    assert again.status_code == 200
    body = again.content.decode()
    assert 'data-notice="stop"' in body and "R11" in body
    assert Order.objects.count() == 1


def test_reject_post(client_logged_in, receive_order):
    inbox = receive_order([_line(1, "A-100")])
    url = reverse("connect:inbox_reject", args=[inbox.pk])

    empty = client_logged_in.post(url, {"reason": " "})

    assert empty.status_code == 200
    assert "text-error" in empty.content.decode()
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_RECEIVED

    response = client_logged_in.post(url, {"reason": "納期に間に合わないため"})

    assert response.status_code == 302
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_REJECTED
    assert inbox.reason == "納期に間に合わないため"
    assert Order.objects.count() == 0


def test_connection_list_shows_secret_state_without_value(
    client_logged_in, connection, secret, user, monkeypatch
):
    monkeypatch.delenv(UNSET_ENV_NAME, raising=False)
    other = create_partner({"code": "VC02", "name": "作り物の協力企業", "role": "subcontractor"}, user)
    services.register_connection(other, "https://example.com/connect/receive/order/", UNSET_ENV_NAME, "send", user)

    body = client_logged_in.get(reverse("connect:connection_list")).content.decode()

    assert "済み" in body and "まだ" in body
    assert ENV_NAME in body and UNSET_ENV_NAME in body
    assert secret not in body
    partial = client_logged_in.get(reverse("connect:connection_list"), HTTP_HX_REQUEST="true").content.decode()
    assert "<html" not in partial and "inbox-poll" not in partial
    assert secret not in partial


def test_menu_has_inbox_entries(client_logged_in):
    body = client_logged_in.get(reverse("core:home")).content.decode()

    assert reverse("connect:inbox_list") in body and "受信した注文" in body
    # つながりは左のメニューに出さず、設定の画面から開く
    assert reverse("connect:connection_list") not in body
    assert reverse("connect:connection_list") in client_logged_in.get(reverse("core:settings")).content.decode()


def test_reject_screen_says_reject(client_logged_in, receive_order):
    inbox = receive_order([_line(1, "A-100")])
    url = reverse("connect:inbox_reject", args=[inbox.pk])

    response = client_logged_in.get(url)

    assert response.status_code == 200
    body = response.content.decode()
    assert "「必須」の欄を埋めて「断る」を押します。" in body
    assert "断る</button>" in body
    assert "「保存」を押します" not in body

    empty = client_logged_in.post(url, {"reason": " "})

    assert empty.status_code == 200
    body = empty.content.decode()
    assert "断る</button>" in body
    assert "「断る」を押します" in body
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_RECEIVED


# ---------------------------------------------------------------- 相手の発注番号と発注日 (初見テスト R5 の候補 C08)


@pytest.mark.parametrize(
    ("payload", "order_no", "order_date"),
    [
        ({"order_no": "PO-000002", "order_date": "2026-11-03"}, "PO-000002", date(2026, 11, 3)),
        ({"order_no": "", "order_date": ""}, "", None),
        ({"order_no": None, "order_date": None}, "", None),
        ({}, "", None),
        ({"order_no": 12, "order_date": "2026/11/03"}, "", None),
        ({"order_no": ["PO"], "order_date": 20261103}, "", None),
        ({"order_no": "PO-1", "order_date": "2026-13-40"}, "PO-1", None),
        (["PO-000002", "2026-11-03"], "", None),
    ],
)
def test_inbox_buyer_order_no_and_date(payload, order_no, order_date):
    """payload の形が違っても、表示のための読み方は例外を出さず、空の字か None を返す。"""
    inbox = Inbox(payload=payload)

    assert inbox.buyer_order_no() == order_no
    assert inbox.buyer_order_date() == order_date


def _head_value(body, label):
    """相手の発注番号と発注日の dd の中の字 (タグを除く)。"""
    match = re.search(rf"<dt[^>]*>{label}</dt>\s*<dd[^>]*>(.*?)</dd>", body, re.S)
    assert match is not None, label
    return re.sub(r"<[^>]+>", "", match.group(1)).strip()


def _record(partner, user, **changes):
    payload = _payload(str(uuid.uuid4()), [_line(1, "A-100")])
    payload.update(changes)
    return services.record_inbox(partner, payload, user)


def test_detail_shows_buyer_order_no_and_date(client_logged_in, partner, user):
    inbox = _record(partner, user, order_no="PO-000002", order_date="2026-11-03")

    response = client_logged_in.get(reverse("connect:inbox_detail", args=[inbox.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert _head_value(body, "相手の発注番号") == "PO-000002"
    assert _head_value(body, "相手の発注日") == "2026-11-03"
    # 「基本」の並び: 取引先・受信日時・相手の発注番号・相手の発注日・入口・状態
    labels = re.findall(r'<dt class="text-base-content/70">([^<]+)</dt>', body)
    assert labels[:6] == ["取引先", "受信日時", "相手の発注番号", "相手の発注日", "入口", "状態"]


def test_intake_row_without_order_date_shows_dash(client_logged_in, partner, user):
    payload = _payload(str(uuid.uuid4()), [_line(1, "A-100")])
    payload["order_date"] = None
    inbox = services.record_inbox(partner, payload, user, source=Inbox.SOURCE_INTAKE, sender_name="作り物の差出人")

    body = client_logged_in.get(reverse("connect:inbox_detail", args=[inbox.pk])).content.decode()

    assert _head_value(body, "相手の発注番号") == "PO-0001"
    assert _head_value(body, "相手の発注日") == "—"


ODD_PAYLOADS = (
    {"order_no": 12, "order_date": "2026/11/03"},
    {"order_no": ["PO"], "order_date": 20261103},
    {"order_no": None, "order_date": "not-a-date"},
)


@pytest.mark.parametrize("changes", ODD_PAYLOADS)
def test_odd_payload_detail_still_opens_with_dash(client_logged_in, partner, user, changes):
    inbox = _record(partner, user, **changes)

    response = client_logged_in.get(reverse("connect:inbox_detail", args=[inbox.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert _head_value(body, "相手の発注番号") == "—"
    assert _head_value(body, "相手の発注日") == "—"


def test_accept_screen_shows_buyer_order_no_and_date_above_lines(client_logged_in, partner, user):
    inbox = _record(partner, user, order_no="PO-000002", order_date="2026-11-03")

    response = client_logged_in.get(reverse("connect:inbox_accept", args=[inbox.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert _head_value(body, "相手の発注番号") == "PO-000002"
    assert _head_value(body, "相手の発注日") == "2026-11-03"
    # 届いた明細の枠の中、明細の見出しの上に出る
    assert body.index("相手の発注日</dt>") < body.index("届いた明細 (")


@pytest.mark.parametrize("changes", ODD_PAYLOADS)
def test_odd_payload_accept_still_opens_with_dash(client_logged_in, partner, user, changes):
    inbox = _record(partner, user, **changes)

    response = client_logged_in.get(reverse("connect:inbox_accept", args=[inbox.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert _head_value(body, "相手の発注番号") == "—"
    assert _head_value(body, "相手の発注日") == "—"


def test_accept_field_help_says_where_initial_item_comes_from(client_logged_in, receive_order, partner, item, user):
    """欄の説明は、表に対応があれば自社品番と「最初から入れた」こと、無ければ「受け入れると表に足す」こと (C08 の 5)。"""
    map_partner_item(partner, item, "A-100", user)
    inbox = receive_order([_line(1, "A-100"), _line(2, "A-200")])
    inbox.payload["lines"][1]["part_name"] = ""
    inbox.save(update_fields=["payload"])

    form = AcceptForm(instance=inbox)

    known, unknown = form.fields["item_1"], form.fields["item_2"]
    assert known.label == "作り物の得意先 の品番 A-100 (作り物の部品 A-100) に当たる自社の部品"
    assert known.help_text == (
        "取引先ごとの品番の表で、作り物の得意先 の A-100 は B-200 に当たるので、最初から入れています。"
        "選び直すと、この受注だけ選んだ部品になります (表は変わりません)。"
    )
    # 品名が無い品番は括弧を付けない
    assert unknown.label == "作り物の得意先 の品番 A-200 に当たる自社の部品"
    assert unknown.help_text == "取引先ごとの品番の表に、作り物の得意先 の A-200 の対応がまだありません。選んだ部品は、受け入れると表に足します。"

    # 画面にも欄ごとの説明が出る
    body = client_logged_in.get(reverse("connect:inbox_accept", args=[inbox.pk])).content.decode()
    assert "作り物の得意先 の A-100 は B-200 に当たるので、最初から入れています。" in body
    assert "受け入れると表に足します。" in body


def test_accept_form_without_partner_keeps_old_words(client_logged_in, user):
    """取引先が空の読み取りの行 (差出人の確認待ち) は、URL を直に開いても落ちず、見出しと説明は前からの字。"""
    payload = _payload(str(uuid.uuid4()), [_line(1, "A-100")])
    inbox = services.record_inbox(None, payload, user, source=Inbox.SOURCE_INTAKE, sender_name="作り物の差出人")

    form = AcceptForm(instance=inbox)

    assert form.fields["item_1"].label == "相手の品番 A-100 (作り物の部品 A-100)"
    assert form.fields["item_1"].help_text == "自社の部品を選びます。取引先ごとの品番の表に無い対応は、受け入れると表に足します。"
    response = client_logged_in.get(reverse("connect:inbox_accept", args=[inbox.pk]))
    assert response.status_code == 200
    assert "相手の品番 A-100 (作り物の部品 A-100)" in response.content.decode()
