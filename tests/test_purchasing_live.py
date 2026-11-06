"""発注の一覧と詳細の 2 秒ごとの更新と、前の更新と状態が違う行の色づけ (N84)。

Django の test client で HTML を見る。2 秒ごとの更新の要求は HTTP_HX_REQUEST="true" と、前の更新で見せた行の状態の字を
見出し X-Seizou-Live (test client では HTTP_X_SEIZOU_LIVE) で送る形。
相手の会社に HTTP を送らない (send=False・つながりなし。requests.post は呼んだら落とす物に差し替える)。取引先と品目は作り物。
"""

import re
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.masters import services as masters_services
from seizou.purchasing import services

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", refuse)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _sent_order(partner, item, user):
    """数量 10・単価 100 の明細が 1 行の発注を作り、「送った」にする。"""
    po = services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=date(2026, 11, 2),
        send=False,
    )
    return services.mark_sent(po, user)


def _poll(client, live="", **params):
    """発注の一覧の 2 秒ごとの更新と同じ要求 (htmx の要求・見出しに前の更新の字)。返す物は HTML の字。"""
    response = client.get(
        reverse("purchasing:purchaseorder_list"), params, HTTP_HX_REQUEST="true", HTTP_X_SEIZOU_LIVE=live
    )
    assert response.status_code == 200
    return response.content.decode()


def _live(body):
    """画面の全部の状態の字を、空白で区切って並べた物 (取りに行く要素が見出しに入れる字と同じ形)。"""
    return " ".join(re.findall(r'data-live="([^"]+)"', body))


def _changed_rows(body):
    """seizou-changed を含む一覧の行 (<tr id="row-<pk>" ...>) の pk の集まり。"""
    pks = set()
    for part in body.split("<tr")[1:]:
        match = re.match(r'\s+id="row-(\d+)"', part)
        if match and "seizou-changed" in part.split("</tr>")[0]:
            pks.add(int(match.group(1)))
    return pks


# --- 1. 発注の一覧の 2 秒ごとの更新 ---


@pytest.mark.django_db
def test_list_page_has_poll_element(client_logged_in, user, subcontractor, item):
    po = _sent_order(subcontractor, item, user)

    body = client_logged_in.get(reverse("purchasing:purchaseorder_list")).content.decode()

    for word in (
        'id="purchaseorder-poll"',
        "every 2s",
        "location.search",
        "X-Seizou-Live",
        'hx-target="#rows"',
        'hx-swap="innerHTML"',
        "activeElement",
        "list-loading",
        "scrollTop",
    ):
        assert word in body, word
    assert body.count(f'data-live="po-{po.pk}:sent:0:0"') == 1
    # hx-vals の js: の値は `{` で始める (htmx は `{` で始まらない字を `{}` で包むので、式のままだと SyntaxError で取りに行かない)
    assert 'hx-vals="js:{' in body
    assert "seizou-changed" not in body
    # CSRF の見出しは土台の body の 1 か所だけ (R18。purchasing の部分は書かない)
    assert body.lower().count("x-csrftoken") == 1
    poll = body[body.index('id="purchaseorder-poll"') :]
    assert "x-csrftoken" not in poll[: poll.index("</div>")].lower()


@pytest.mark.django_db
def test_poll_returns_rows_only(client_logged_in, user, subcontractor, item):
    po = _sent_order(subcontractor, item, user)

    body = _poll(client_logged_in)

    assert f'<tr id="row-{po.pk}"' in body
    assert "<html" not in body
    assert 'id="purchaseorder-poll"' not in body  # 取りに行く要素は #rows の外 (入れ替わらない)
    assert 'id="list-meta"' in body and 'hx-swap-oob="true"' in body


@pytest.mark.django_db
def test_poll_keeps_filter_sort_and_page(client_logged_in, user, subcontractor, item):
    other = masters_services.create_partner(
        {"code": "S002", "name": "作り物の協力企業の二", "role": "subcontractor"}, user
    )
    first = _sent_order(subcontractor, item, user)
    second = _sent_order(other, item, user)

    body = _poll(client_logged_in, live=f"po-{first.pk}:sent:0:0 po-{second.pk}:sent:0:0", q="S002", sort="-po_no", per="20")

    assert f'<tr id="row-{second.pk}"' in body
    assert f'<tr id="row-{first.pk}"' not in body
    links = [href for href in re.findall(r'hx-get="([^"]+)"', body) if "sort=" in href]
    assert links
    for href in links:
        assert "q=S002" in href, href
        assert "per=20" in href, href
        assert "X-Seizou" not in href and "live" not in href.lower(), href


@pytest.mark.django_db
def test_changed_row_is_marked_once(client_logged_in, user, subcontractor, item):
    first = _sent_order(subcontractor, item, user)
    second = _sent_order(subcontractor, item, user)
    before = _live(_poll(client_logged_in))
    assert f"po-{first.pk}:sent:0:0" in before.split()

    services.mark_accepted(first, user)
    body = _poll(client_logged_in, live=before)

    assert _changed_rows(body) == {first.pk}
    assert f'data-live="po-{first.pk}:accepted:0:0"' in body
    assert f'data-live="po-{second.pk}:sent:0:0"' in body

    again = _poll(client_logged_in, live=_live(body))
    assert _changed_rows(again) == set()
    assert "seizou-changed" not in again


def _accept(po, user):
    services.mark_accepted(po, user)


def _reject(po, user):
    services.mark_rejected(po, user, reason="作り物の断り")


def _notice(po, user):
    connect_services.record_shipping_notice(
        po.lines.get(), Decimal("4"), date(2026, 11, 3), user, message_id=uuid.uuid4(), shipment_no="SH-T1"
    )


def _receive(po, user):
    services.receive(po.lines.get(), Decimal("4"), date(2026, 11, 3), user)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("change", "token"),
    [
        (_accept, "accepted:0:0"),
        (_reject, "rejected:0:0"),
        (_notice, "sent:1:0"),
        (_receive, "sent:0:4"),
    ],
    ids=["相手が受け入れた", "断られた", "出荷の知らせ", "検収"],
)
def test_four_kinds_mark_the_row(client_logged_in, user, subcontractor, item, change, token):
    target = _sent_order(subcontractor, item, user)
    other = _sent_order(subcontractor, item, user)
    before = _live(_poll(client_logged_in))

    change(target, user)
    body = _poll(client_logged_in, live=before)

    assert _changed_rows(body) == {target.pk}
    assert f'data-live="po-{target.pk}:{token}"' in body
    assert f'data-live="po-{other.pk}:sent:0:0"' in body


@pytest.mark.django_db
def test_without_header_nothing_is_marked(client_logged_in, user, subcontractor, item):
    po = _sent_order(subcontractor, item, user)
    services.mark_accepted(po, user)

    assert "seizou-changed" not in _poll(client_logged_in)
    body = _poll(client_logged_in, live="po-999999:sent:0:0 abc")
    assert f'data-live="po-{po.pk}:accepted:0:0"' in body
    assert "seizou-changed" not in body


def _orders_with_notice_and_receipt(partner, item, user, count):
    for index in range(count):
        po = _sent_order(partner, item, user)
        line = po.lines.get()
        connect_services.record_shipping_notice(
            line, Decimal("2"), date(2026, 11, 3), user, message_id=uuid.uuid4(), shipment_no=f"SH-Q{index}"
        )
        services.receive(line, Decimal("2"), date(2026, 11, 3), user)


def _count_poll_queries(client):
    live = _live(_poll(client))
    with CaptureQueriesContext(connection) as queries:
        body = _poll(client, live=live)
    return len(queries.captured_queries), body


@pytest.mark.django_db
def test_poll_query_count_does_not_grow_with_rows(client_logged_in, user, subcontractor, item):
    _orders_with_notice_and_receipt(subcontractor, item, user, 1)
    _poll(client_logged_in)  # 最初の 1 回の控え (session など) を外す
    small, body = _count_poll_queries(client_logged_in)
    assert len(re.findall(r'data-live="po-\d+:sent:1:2"', body)) == 1

    _orders_with_notice_and_receipt(subcontractor, item, user, 4)
    large, body = _count_poll_queries(client_logged_in)
    assert len(re.findall(r'data-live="po-\d+:sent:1:2"', body)) == 5

    assert small == large, (small, large)


@pytest.mark.django_db
def test_receipt_list_is_unchanged(client_logged_in, user, subcontractor, item):
    po = _sent_order(subcontractor, item, user)
    services.receive(po.lines.get(), Decimal("3"), date(2026, 11, 3), user)
    url = reverse("purchasing:receipt_list")

    page = client_logged_in.get(url).content.decode()
    rows = client_logged_in.get(url, HTTP_HX_REQUEST="true").content.decode()

    for body in (page, rows):
        assert po.po_no in body
        assert "data-live" not in body
        assert "seizou-changed" not in body
        assert "purchaseorder-poll" not in body


# --- 2. 発注の詳細の 2 秒ごとの更新 ---


def _detail(client, po, live=None):
    """発注の詳細の HTML。live を渡すと 2 秒ごとの更新と同じ要求 (htmx の要求・見出しに前の更新の字) にする。"""
    url = reverse("purchasing:purchaseorder_detail", args=[po.pk])
    if live is None:
        response = client.get(url)
    else:
        response = client.get(url, HTTP_HX_REQUEST="true", HTTP_X_SEIZOU_LIVE=live)
    assert response.status_code == 200
    return response.content.decode()


def _heading(body):
    """見出しの帯 (<div id="po-heading" ...>) の開きの tag と、黄の知らせの入れ物までの HTML。"""
    start = body.index('<div id="po-heading"')
    return body[start : body.index('id="po-alert"', start)]


@pytest.mark.django_db
def test_detail_has_live_parts(client_logged_in, user, subcontractor, item):
    po = _sent_order(subcontractor, item, user)

    body = _detail(client_logged_in, po)

    for word in (
        'id="po-heading"',
        'id="po-alert"',
        'id="po-live"',
        'id="purchaseorder-detail-poll"',
        'hx-select="#po-live"',
        'hx-select-oob="#po-heading,#po-alert,#detail-aside"',
        "X-Seizou-Live",
    ):
        assert word in body, word
    heading = _heading(body)
    assert f'data-live="po-{po.pk}:sent:0:0"' in heading
    assert "seizou-changed" not in body
    # 見出しの h1 と状態の印は同じ帯の中 (E2E の h1 ~ .badge が通る並び)
    assert heading.index("<h1") < heading.index("badge") and "送った" in heading
    # 明細と検収の節は #po-live の中、取りに行く要素は #po-live の外
    live = body[body.index('<div id="po-live">') : body.index('id="purchaseorder-detail-poll"')]
    assert 'id="po-lines-title"' in live
    assert "x-csrftoken" not in body[body.index('id="purchaseorder-detail-poll"') :].split("</div>")[0].lower()
    # 明細の箱を横に送っている間は取り直さない (入れ替えると箱が左の端へ戻る。発注の一覧の scrollTop と同じ形。C22)
    poll = re.search(r'<div id="purchaseorder-detail-poll"[^>]*>', body)
    assert poll
    assert "scrollLeft" in poll.group(0)
    assert "#po-live section .overflow-x-auto" in poll.group(0)

    # 「作成」の発注: ボタンと link の行は今のまま (再送 1・受入を登録 1)
    draft = services.create_purchase_order(
        subcontractor,
        [{"item": item, "qty": Decimal("5"), "unit_price": Decimal("100")}],
        user,
        order_date=date(2026, 11, 2),
        send=False,
    )
    body = _detail(client_logged_in, draft)
    assert body.count("再送") == 1
    assert body.count("受入を登録") == 1
    assert f'data-live="po-{draft.pk}:draft:0:0"' in body


@pytest.mark.django_db
def test_detail_marks_changed_heading(client_logged_in, user, subcontractor, item):
    po = _sent_order(subcontractor, item, user)
    before = _live(_detail(client_logged_in, po))
    assert before == f"po-{po.pk}:sent:0:0"

    services.mark_accepted(po, user)
    body = _detail(client_logged_in, po, live=before)
    assert "seizou-changed" in _heading(body).split(">")[0]
    assert _live(body) == f"po-{po.pk}:accepted:0:0"

    services.receive(po.lines.get(), Decimal("4"), date(2026, 11, 3), user)
    body = _detail(client_logged_in, po, live=_live(body))
    assert "seizou-changed" in _heading(body).split(">")[0]
    assert _live(body) == f"po-{po.pk}:accepted:0:4"

    again = _detail(client_logged_in, po, live=_live(body))
    assert "seizou-changed" not in again


# --- 8. 受けすぎの知らせ (受入の数の合計が明細の数量を超えた明細。本体の追記) ---


@pytest.mark.django_db
def test_over_receipt_notice_is_shown(client_logged_in, user, subcontractor, item):
    po = _sent_order(subcontractor, item, user)
    line = po.lines.get()
    services.receive(line, Decimal("6"), date(2026, 11, 3), user)
    services.receive(line, Decimal("6.00"), date(2026, 11, 4), user)  # 受けすぎでも受入は止まらない
    message = "受入の数 12 が発注の数 10 を超えています。"

    detail = _detail(client_logged_in, po)
    assert f'data-over-receipt="{line.pk}"' in detail
    assert f"明細 1: {message}" in detail
    # 知らせの行は #po-live の中 (2 秒ごとの更新で入れ替わる)
    assert detail.index('<div id="po-live">') < detail.index(message) < detail.index('id="purchaseorder-detail-poll"')

    receipts = client_logged_in.get(reverse("purchasing:receipt_list")).content.decode()
    assert f"{line}: {message}" in receipts
    assert receipts.count(message) == 1  # 受入が 2 行でも、明細ごとに 1 つ


@pytest.mark.django_db
def test_over_receipt_notice_not_shown_for_equal_or_less(client_logged_in, user, subcontractor, item):
    equal = _sent_order(subcontractor, item, user)
    less = _sent_order(subcontractor, item, user)
    services.receive(equal.lines.get(), Decimal("10"), date(2026, 11, 3), user)
    services.receive(less.lines.get(), Decimal("4"), date(2026, 11, 3), user)

    for po in (equal, less):
        body = _detail(client_logged_in, po)
        assert "を超えています" not in body
        assert "data-over-receipt" not in body
    receipts = client_logged_in.get(reverse("purchasing:receipt_list")).content.decode()
    assert equal.po_no in receipts and less.po_no in receipts
    assert "を超えています" not in receipts


# --- 3. 色づけの CSS ---


def test_css_has_changed_class():
    source = (ROOT / "scripts" / "css" / "input.css").read_text(encoding="utf-8")
    built = (ROOT / "src" / "seizou" / "core" / "static" / "css" / "app.css").read_text(encoding="utf-8")

    assert "@keyframes seizou-changed" in source
    assert ".seizou-changed {" in source
    assert "seizou-changed" in built
