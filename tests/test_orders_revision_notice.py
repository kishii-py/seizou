"""取引先から取消か変更が届いたまま受け入れも断りもしていない受注に、黄色の 1 行を出す (依頼書 N417。精査 Q3 の T7・M6。本体の決め 11-11 14:2x)。

受注の詳細と、出荷の新規登録 (画面の全体と小窓) の上に、知らせの段 5 (状態) の黄色の 1 行が出ることと、出荷は止めないことを見る。
1 つの DB に注文を送る会社 (A = 協力企業 S001 へ発注する) と受ける会社 (B = 得意先 C001 から受注する) の両方の役を置く。
受信した注文と後の版の作り方は、既にある試験の道具と fixture を import して使う (tests/test_connect_revision_accept.py と
tests/test_core_access.py。借りるファイルは変えない)。相手への HTTP は借りた fixture が requests.post を偽の物に差し替える。
画面は Django の test client で開いて HTML を見る (本物のブラウザは使わない)。取引先・品番・人は作り物だけ。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.connect import services as connect_services
from seizou.core import edi_codes
from seizou.orders import services as orders_services
from seizou.shipping.models import Shipment
from tests import test_connect_revision_accept as revision_accept
from tests.test_connect_revision_accept import _accept_first, _cancel, _revise, _send, _send_first
from tests.test_core_access import account

# 版の受け入れの試験の fixture を借りる (pytest は module の名前で fixture を見つける。tests/test_core_department_buttons.py と同じ形)。
user = revision_accept.user
client_logged_in = revision_accept.client_logged_in
order_secret = revision_accept.order_secret
reply_secret = revision_accept.reply_secret
subcontractor = revision_accept.subcontractor
customer = revision_accept.customer
items = revision_accept.items
connections = revision_accept.connections
fake = revision_accept.fake

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
CANCEL_TEXT = "取引先から第 2 版の取消が届いています。"
CHANGE_TEXT = "取引先から第 2 版の変更が届いています。"
LINK_TEXT = "受信した注文の詳細を開く"
#: 黄色の 1 行 (orders/_revision_notice.html) の div。段 5 の形 (alert-warning) を class の字のまま見る
NOTICE_RE = re.compile(r'<div class="alert alert-warning alert-soft mb-4" role="alert" data-revision-notice>.*?</div>', re.S)


def _waiting(subcontractor, items, user, *, purpose=edi_codes.PURPOSE_CANCEL):
    """A の第 1 版を B が受け入れて受注にし、A が第 2 版 (取消か変更) を送って B の受信した注文に未処理で届いた所。返す物は (受注, 第 2 版の行)。"""
    po = _send_first(subcontractor, items, user)
    base, order = _accept_first(items, user)
    _send(_cancel(po) if purpose == edi_codes.PURPOSE_CANCEL else _revise(po, items), user)
    revision = base.revisions.get()
    assert (revision.revision_no, revision.purpose_code, revision.status) == (2, purpose, revision.STATUS_RECEIVED)
    return order, revision


def _notice(body):
    """HTML の中の黄色の 1 行 (無ければ None)。"""
    found = NOTICE_RE.findall(body)
    assert len(found) <= 1, found
    return found[0] if found else None


def _pages(client, order):
    """受注の詳細と、受注の明細 1 の出荷の新規登録 (画面の全体と小窓) の HTML。"""
    line = order.lines.order_by("line_no").first()
    ship_url = reverse("shipping:shipment_create") + f"?order_line={line.pk}"
    return {
        "detail": client.get(reverse("orders:order_detail", args=[order.pk])).content.decode(),
        "ship": client.get(ship_url).content.decode(),
        "ship_modal": client.get(ship_url, **MODAL).content.decode(),
    }


# ---------------------------------------------------------------- (a) 受注の詳細


@pytest.mark.django_db
def test_order_detail_shows_cancel_revision(fake, user, subcontractor, items, client_logged_in):
    order, revision = _waiting(subcontractor, items, user)

    body = client_logged_in.get(reverse("orders:order_detail", args=[order.pk])).content.decode()

    notice = _notice(body)
    assert notice is not None
    assert CANCEL_TEXT in notice
    assert "受け入れるまで、この受注は前の版の中身のままです。受信した注文の詳細で、受け入れるか断ってください。" in notice
    assert f'href="{reverse("connect:inbox_detail", args=[revision.pk])}"' in notice and LINK_TEXT in notice
    assert "¥" not in notice
    # 小窓で保存した後の取り直し (seizou:refresh) で描き直す範囲 (#order-live) の中の頭 (段の帯の前)
    live = body.index('<div id="order-live">')
    assert live < body.index(notice) < body.index('id="order-stages"')


@pytest.mark.django_db
def test_order_detail_shows_change_revision(fake, user, subcontractor, items, client_logged_in):
    order, revision = _waiting(subcontractor, items, user, purpose=edi_codes.PURPOSE_CHANGE)

    notice = _notice(client_logged_in.get(reverse("orders:order_detail", args=[order.pk])).content.decode())

    assert notice is not None
    assert CHANGE_TEXT in notice and "取消" not in notice
    assert reverse("connect:inbox_detail", args=[revision.pk]) in notice


# ---------------------------------------------------------------- (b) 出荷の新規登録 (画面の全体と小窓)


@pytest.mark.django_db
def test_shipment_create_shows_the_same_line(fake, user, subcontractor, items, client_logged_in):
    order, revision = _waiting(subcontractor, items, user)
    pages = _pages(client_logged_in, order)

    for name in ("ship", "ship_modal"):
        notice = _notice(pages[name])
        assert notice is not None, name
        assert CANCEL_TEXT in notice, name
        assert reverse("connect:inbox_detail", args=[revision.pk]) in notice, name
    assert "<html" not in pages["ship_modal"]
    # 欄の上 (受注の明細の欄より前)
    for name in ("ship", "ship_modal"):
        assert pages[name].index("data-revision-notice") < pages[name].index('name="order_line"'), name

    # 保存して止まって戻った時も、送った明細で出る
    line = order.lines.order_by("line_no").first()
    stopped = client_logged_in.post(reverse("shipping:shipment_create"), {"order_line": line.pk, "qty": "", "shipped_at": ""})
    assert stopped.status_code == 200
    assert CANCEL_TEXT in (_notice(stopped.content.decode()) or "")


@pytest.mark.django_db
def test_shipment_create_without_line_has_no_line(fake, user, subcontractor, items, client_logged_in):
    _waiting(subcontractor, items, user)

    for query in ("", "?order_line=", "?order_line=abc", "?order_line=999999"):
        body = client_logged_in.get(reverse("shipping:shipment_create") + query).content.decode()
        assert _notice(body) is None, query


# ---------------------------------------------------------------- (c) 受け入れるか断ると消える


@pytest.mark.django_db
def test_line_goes_away_after_accepting_cancel(fake, user, subcontractor, items, client_logged_in):
    order, revision = _waiting(subcontractor, items, user)
    assert all(_notice(body) for body in _pages(client_logged_in, order).values())

    connect_services.accept(revision, {}, user)

    for name, body in _pages(client_logged_in, order).items():
        assert _notice(body) is None, name
        assert CANCEL_TEXT not in body, name


@pytest.mark.django_db
def test_line_goes_away_after_rejecting_change(fake, user, subcontractor, items, client_logged_in):
    order, revision = _waiting(subcontractor, items, user, purpose=edi_codes.PURPOSE_CHANGE)
    assert all(_notice(body) for body in _pages(client_logged_in, order).values())

    connect_services.reject(revision, "作り物の断り", user)

    for name, body in _pages(client_logged_in, order).items():
        assert _notice(body) is None, name
        assert CHANGE_TEXT not in body, name


# ---------------------------------------------------------------- (d) 出ない受注と、connect を開けない人


@pytest.mark.django_db
def test_no_line_without_later_revision(fake, user, subcontractor, items, customer, client_logged_in):
    _send_first(subcontractor, items, user)
    _base, from_inbox = _accept_first(items, user)
    _buyer, own = items
    by_hand = orders_services.create_order(
        customer, [{"item": own[0], "qty": Decimal("5"), "unit_price": Decimal("100")}], user, order_date=date(2026, 11, 2)
    )

    for order in (from_inbox, by_hand):
        for name, body in _pages(client_logged_in, order).items():
            assert _notice(body) is None, (order.order_no, name)
            assert "が届いています。" not in body, (order.order_no, name)


@pytest.mark.django_db
def test_people_who_cannot_open_connect_get_text_without_link(settings, client, fake, user, subcontractor, items):
    order, revision = _waiting(subcontractor, items, user)
    settings.SEIZOU = {"access_control": {"department": "on", "write": "off", "money": "off"}}
    sales = accounts_services.create_department("受注と出荷", ["orders", "shipping", "masters"], ["orders", "shipping"], None)
    client.force_login(account("shukka", departments=[sales]))

    pages = _pages(client, order)

    inbox_url = reverse("connect:inbox_detail", args=[revision.pk])
    for name, body in pages.items():
        notice = _notice(body)
        assert notice is not None and CANCEL_TEXT in notice, name
        assert LINK_TEXT not in notice and inbox_url not in notice, name


# ---------------------------------------------------------------- (e) 出荷は止めない


@pytest.mark.django_db
def test_shipment_still_saves_while_line_is_shown(fake, user, subcontractor, items, client_logged_in):
    order, _revision = _waiting(subcontractor, items, user)
    line = order.lines.order_by("line_no").first()
    assert _notice(_pages(client_logged_in, order)["ship"]) is not None

    response = client_logged_in.post(
        reverse("shipping:shipment_create"),
        {"order_line": line.pk, "qty": "5", "shipped_at": "2026-11-06", "note": ""},
    )

    assert response.status_code == 302
    assert Shipment.objects.filter(order_line=line).count() == 1


# ---------------------------------------------------------------- tag の受ける形


@pytest.mark.django_db
def test_tag_reads_order_line_and_number(fake, user, subcontractor, items, django_assert_num_queries):
    from seizou.orders.templatetags.orders_detail import order_revision_notice

    order, revision = _waiting(subcontractor, items, user)
    line = order.lines.order_by("line_no").first()

    expected = {
        "text": CANCEL_TEXT + "受け入れるまで、この受注は前の版の中身のままです。受信した注文の詳細で、受け入れるか断ってください。",
        "url": reverse("connect:inbox_detail", args=[revision.pk]),
    }
    for target in (order, line, line.pk, str(line.pk)):
        assert order_revision_notice(target) == expected, target
    # 受注・明細・明細の番号のどれでもない物と、番号でない字は、表を読まずに None
    with django_assert_num_queries(0):
        for target in (None, "", "abc", "1.5", True, revision):
            assert order_revision_notice(target) is None, target
    assert order_revision_notice(999999) is None
