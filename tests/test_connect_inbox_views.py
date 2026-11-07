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
from django.db import connection as db_connection
from django.template.loader import render_to_string
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.connect import services, signing
from seizou.connect.forms import AcceptForm, RejectForm
from seizou.connect.models import Inbox
from seizou.core import edi_codes
from seizou.masters.services import create_item, create_partner, map_partner_item
from seizou.orders.models import Order
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret
from tests.test_core_save_bar import ROW as SAVE_ROW
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
        "部品の取引先の表で、作り物の得意先 の A-100 は B-200 に当たるので、最初から入れています。"
        "選び直すと、この受注だけ選んだ部品になります (表は変わりません)。"
    )
    # 品名が無い品番は括弧を付けない
    assert unknown.label == "作り物の得意先 の品番 A-200 に当たる自社の部品"
    assert unknown.help_text == "部品の取引先の表に、作り物の得意先 の A-200 の対応がまだありません。選んだ部品は、受け入れると表に足します。"

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
    assert form.fields["item_1"].help_text == "自社の部品を選びます。部品の取引先の表に無い対応は、受け入れると表に足します。"
    response = client_logged_in.get(reverse("connect:inbox_accept", args=[inbox.pk]))
    assert response.status_code == 200
    assert "相手の品番 A-100 (作り物の部品 A-100)" in response.content.decode()


# ---------------------------------------------------------------- 受け入れの画面の品番ごとの枠 (初見テスト R5 の候補 C91)

DASH = '<span class="text-base-content/60">—</span>'


def _frame(body, name):
    """受け入れの画面の data-accept-part="<name>" の枠の字 (その印から、次の枠の印か </form> まで)。"""
    start = body.index(f'data-accept-part="{name}"')
    ends = [at for at in (body.find("data-accept-part=", start + 1), body.find("</form>", start)) if at != -1]
    return body[start : min(ends)]


def _receive_change(secret, base, lines):
    """第 1 版 (base) の第 2 版の変更を、相手の受け口の役で署名して受ける。返す物は受信した注文の行。"""
    msg_id = str(uuid.uuid4())
    data = _payload(msg_id, lines)
    data.update(order_id=str(base.message_id), order_revision_no=2, purpose_code=edi_codes.PURPOSE_CHANGE)
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    result = services.receive(body, signing.headers_for(secret, msg_id, body))
    assert isinstance(result, Inbox), result
    return result


def test_accept_screen_puts_line_values_beside_each_part_field(client_logged_in, receive_order):
    """受け入れの画面で、欄 1 つの枠に、その品番の届いた明細の数量・単位・納期・単価が並ぶ (phone で欄を選ぶ時に数量と納期が見える)。"""
    inbox = receive_order(
        [_line(1, "A-100", qty="200.00", due_date="2026-11-06"), _line(2, "A-200", qty="100.00", due_date="2026-11-13")]
    )

    response = client_logged_in.get(reverse("connect:inbox_accept", args=[inbox.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    first, second = _frame(body, "item_1"), _frame(body, "item_2")
    assert "行 1<" in first and "A-100" in first and ">200 個<" in first and "2026-11-06" in first and ">¥250<" in first
    assert 'name="item_1"' in first
    assert "2026-11-13" not in first and 'name="item_2"' not in first
    assert "行 2<" in second and ">100 個<" in second and "2026-11-13" in second and 'name="item_2"' in second
    assert "2026-11-06" not in second and 'name="item_1"' not in second
    assert body.index('data-accept-part="item_1"') < body.index('data-accept-part="item_2"')
    # 上の表 (届いた明細) も今のまま、枠より上に出る
    assert "届いた明細 (" in body and body.index("届いた明細 (") < body.index('data-accept-part="item_1"')


def test_accept_screen_groups_lines_with_same_part_no(client_logged_in, receive_order, item, user):
    """同じ品番の明細が 2 行あれば、欄は 1 つのまま、その欄の枠の左に 2 行とも並ぶ (受け入れの動きは変わらない)。"""
    other = create_item({"part_no": "B-300", "name": "作り物のシャフト", "unit": "個"}, user)
    inbox = receive_order([_line(1, "A-100"), _line(2, "A-200"), _line(3, "A-100", qty="5.00", due_date="2026-12-01")])
    url = reverse("connect:inbox_accept", args=[inbox.pk])

    body = client_logged_in.get(url).content.decode()

    assert body.count("data-accept-part=") == 2
    first = _frame(body, "item_1")
    assert "行 1<" in first and "行 3<" in first and "2026-12-01" in first and ">5 個<" in first
    assert "行 2<" not in first
    assert len(AcceptForm(instance=inbox).part_fields) == 2
    response = client_logged_in.post(url, {"item_1": item.pk, "item_2": other.pk})
    assert response.status_code == 302
    assert Order.objects.get().lines.count() == 3


def test_accept_card_hides_unit_price_without_money(receive_order):
    """金額の閲覧の無い人 (seizou.hide_money) には、枠に単価の行を dt も dd も出さない (上の表の単価の列と同じ)。"""
    inbox = receive_order([_line(1, "A-100")])

    def render(hide):
        return render_to_string("connect/_inbox_accept_card.html", {"form": AcceptForm(instance=inbox), "seizou": {"hide_money": hide}})

    hidden, shown = render(True), render(False)
    assert "単価</dt>" not in hidden and "¥250" not in hidden
    assert "単価</dt>" in shown and "¥250" in shown


def test_accept_card_shows_dash_for_missing_values(partner, user):
    """単価と納期の無い明細は、枠の dd に薄い「—」を出す (「None」の字を出さない)。"""
    payload = _payload(str(uuid.uuid4()), [_line(1, "A-100", unit_price=None, due_date=None)])
    inbox = services.record_inbox(partner, payload, user)

    body = render_to_string("connect/_inbox_accept_card.html", {"form": AcceptForm(instance=inbox), "seizou": {"hide_money": False}})

    frame = _frame(body, "item_1")
    assert f'納期</dt><dd class="font-mono tabular-nums">{DASH}</dd>' in frame
    assert f'単価</dt><dd class="tabular-nums">{DASH}</dd>' in frame
    assert ">10 個<" in frame
    assert "None" not in body


def test_accept_field_error_stays_in_its_frame(client_logged_in, receive_order):
    """欄を空のまま押すと、その欄の枠の中に欄の下の赤い字と赤枠の印が出る。右上の赤い知らせも今と同じく出る。"""
    inbox = receive_order([_line(1, "A-100"), _line(2, "A-200")])

    response = client_logged_in.post(reverse("connect:inbox_accept", args=[inbox.pk]), {})

    assert response.status_code == 200
    body = response.content.decode()
    first = _frame(body, "item_1")
    assert 'id="id_item_1_error"' in first and 'aria-invalid="true"' in first
    assert 'id="id_item_2_error"' not in first
    assert 'data-notice="stop"' in body
    assert Order.objects.count() == 0


def test_accept_screen_has_one_save_row(client_logged_in, receive_order):
    """受け入れの画面の下の行 (キャンセルと受け入れる) は、写したカードで 1 つだけ出る (欠けたり 2 つになったりしない)。"""
    inbox = receive_order([_line(1, "A-100")])

    body = client_logged_in.get(reverse("connect:inbox_accept", args=[inbox.pk])).content.decode()

    assert body.count(SAVE_ROW) == 1
    assert "受け入れる</button>" in body and "キャンセル</a>" in body


def test_change_frame_lists_only_new_lines_of_the_part(client_logged_in, receive_order, secret, item, user):
    """変更の版で、受注に既にある行と新しく足した行が同じ相手の品番のとき、その品番の枠には新しい行だけが並ぶ (欄を作った行と同じ行。C96)。"""
    other = create_item({"part_no": "B-300", "name": "作り物のシャフト", "unit": "個"}, user)
    base = receive_order([_line(1, "A-100"), _line(2, "A-200")])
    services.accept(base, {"A-100": item, "A-200": other}, user)
    revision = _receive_change(
        secret,
        Inbox.objects.get(pk=base.pk),
        [
            {**_line(1, "A-100"), "line_status_code": edi_codes.LINE_NEW},
            {**_line(2, "A-200"), "line_status_code": edi_codes.LINE_NEW},
            {**_line(3, "A-100", qty="5.00", due_date="2026-12-01"), "line_status_code": edi_codes.LINE_ADDED},
        ],
    )

    # 欄と枠の行は同じ行から作り、受注の行番号を引くのは 1 回だけ
    with CaptureQueriesContext(db_connection) as queries:
        form = AcceptForm(instance=revision)
    assert form.part_fields == [("item_1", "A-100")]
    assert [line["line_no"] for line in form.part_rows[0]["lines"]] == [3]
    assert sum('"line_no" FROM "orders_orderline"' in query["sql"] for query in queries.captured_queries) == 1

    body = client_logged_in.get(reverse("connect:inbox_accept", args=[revision.pk])).content.decode()
    assert body.count("data-accept-part=") == 1
    frame = _frame(body, "item_1")
    assert "行 3<" in frame and ">5 個<" in frame and "2026-12-01" in frame
    assert "行 1<" not in frame and ">10 個<" not in frame


# ---------------------------------------------------------------- 断るの画面の届いた明細と理由の欄の説明 (初見テスト R5 の候補 C72)


def test_reject_screen_shows_received_lines(client_logged_in, receive_order):
    """断るの画面に、受け入れの画面と同じ「届いた明細」の枠 (取引先と明細) が出る (C72 の 1・2)。

    この取引先のつながりは向き「受ける」だけで相手へは送らないので、理由の欄の下に送る説明は出ない。
    """
    inbox = receive_order([_line(1, "A-100")])

    response = client_logged_in.get(reverse("connect:inbox_reject", args=[inbox.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "届いた明細 (" in body
    assert "作り物の得意先" in body
    assert "A-100" in body and "作り物の部品 A-100" in body
    assert "注文回答として" not in body


def test_reject_reason_help_says_reply_goes_to_partner(client_logged_in, receive_order, partner, user, monkeypatch):
    """向き「送る」のつながりがある取引先の行は、理由の欄の下に、書いた理由が注文回答として相手へ届くことを書く (C72 の 3)。

    理由が空で止まった画面にも同じ字が残る。GET と止まる POST だけなので HTTP は出ない (偽の requests.post が控える)。
    """
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    inbox = receive_order([_line(1, "A-100")])
    services.register_connection(partner, "https://buyer.example.test/connect/receive/order/", ENV_NAME, "send", user)
    url = reverse("connect:inbox_reject", args=[inbox.pk])
    help_text = "書いた理由は、注文回答として 作り物の得意先 へ送ります。"

    assert help_text in client_logged_in.get(url).content.decode()

    empty = client_logged_in.post(url, {"reason": " "})

    assert empty.status_code == 200
    assert help_text in empty.content.decode()
    inbox.refresh_from_db()
    assert inbox.status == Inbox.STATUS_RECEIVED
    assert fake.calls == []


def test_reject_form_help_stays_on_the_form(receive_order, partner, user):
    """理由の欄の説明は、受信した注文を渡した form の欄にだけ入り、class の欄 (base_fields) と instance 無しの form は空のまま (C72 の 3)。"""
    inbox = receive_order([_line(1, "A-100")])
    services.register_connection(partner, "https://buyer.example.test/connect/receive/order/", ENV_NAME, "send", user)

    form = RejectForm(instance=inbox)

    assert form.fields["reason"].help_text == "書いた理由は、注文回答として 作り物の得意先 へ送ります。"
    assert RejectForm.base_fields["reason"].help_text == ""
    assert RejectForm().fields["reason"].help_text == ""
