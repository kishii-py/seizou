"""発注の詳細の「流れ」の枠 (N139)。出来事の集め方 (purchasing.timeline.events_for) と、詳細の画面の枠。

作り物のデータは全部 services で作る。送った注文の日時と注文回答の日時は services が今を書くので、日付は今日から数える。
相手への HTTP は偽の requests.post (tests.test_connect_send の FakePost) に差し替え、鍵はテストのたびに作る。
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
import requests
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.connect import services as connect_services
from seizou.core.templatetags.seizou_tags import comma
from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.timeline import events_for
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

ENV_NAME = "SEIZOU_PARTNER_TESTSUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"
FLOW_KINDS = ["order", "sent", "reply", "shipping_notice", "receipt", "acceptance"]


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def no_http(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


@pytest.fixture
def send_connection(monkeypatch, subcontractor, user):
    monkeypatch.setenv(ENV_NAME, _new_secret())
    return connect_services.register_connection(subcontractor, PARTNER_URL, ENV_NAME, "send", user)


def _order(user, partner, item, order_date, *, send=True):
    return services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("25")}],
        user,
        order_date=order_date,
        send=send,
    )


def _notice(line, qty, shipped_at, shipment_no):
    return connect_services.record_shipping_notice(
        line, Decimal(qty), shipped_at, None, message_id=uuid.uuid4(), shipment_no=shipment_no
    )


def _flowed(monkeypatch, user, subcontractor, item):
    """発注 → 注文を送った (相手の受け口は 200) → 相手が受け入れた → 出荷の知らせ → 受入 → 検収を送った の発注 (全部今日)。"""
    today = timezone.localdate()
    monkeypatch.setattr("requests.post", FakePost())
    po = services.mark_accepted(_order(user, subcontractor, item, today), user)
    line = po.lines.get()
    _notice(line, "10", today, "SH-0001")
    receipt = services.receive(line, Decimal("10"), today, user)
    return po, receipt


def _detail(client, po):
    response = client.get(reverse("purchasing:purchaseorder_detail", args=[po.pk]))
    assert response.status_code == 200
    return response, response.content.decode()


def _flow_section(body):
    start = body.index('<section id="po-flow"')
    return body[start : body.index("</section>", start)]


@pytest.mark.django_db
def test_order_alone_has_one_registered_event(user, subcontractor, item, no_http):
    today = timezone.localdate()
    po = _order(user, subcontractor, item, today)

    events = events_for(po)

    assert [(e.kind, e.title, e.when, e.url, e.line_no, e.amount) for e in events] == [
        ("order", "発注を登録", today, None, None, None)
    ]


@pytest.mark.django_db
def test_events_from_sending_to_acceptance_line_up_in_order(monkeypatch, user, subcontractor, item, send_connection):
    po, receipt = _flowed(monkeypatch, user, subcontractor, item)
    assert receipt.acceptance_sent_at is not None

    events = events_for(po)

    assert [e.kind for e in events] == FLOW_KINDS
    assert [e.title for e in events] == [
        "発注を登録",
        "注文を送った (相手が受け取った)",
        "相手が受け入れた",
        "出荷の知らせ SH-0001 (10 個)",
        "受入 (10 個)",
        "検収を送った (受入 10 個)",
    ]
    assert [e.line_no for e in events] == [None, None, None, 1, 1, 1]
    assert [e.has_time for e in events] == [False, True, True, False, False, True]
    receipt_url = reverse("purchasing:receipt_detail", args=[receipt.pk])
    assert [e.url for e in events] == [None, None, None, None, receipt_url, receipt_url]
    assert [e.amount for e in events] == [None] * 4 + [receipt.amount, None]


@pytest.mark.django_db
def test_earlier_day_comes_before_later_kind(user, subcontractor, item, no_http):
    today = timezone.localdate()
    po = _order(user, subcontractor, item, today - timedelta(days=3))
    line = po.lines.get()
    services.receive(line, Decimal("4"), today - timedelta(days=2), user)
    _notice(line, "6", today - timedelta(days=1), "SH-0002")

    assert [e.kind for e in events_for(po)] == ["order", "receipt", "shipping_notice"]


@pytest.mark.django_db
def test_money_is_hidden_without_money_permission(monkeypatch, user, logged_in, subcontractor, item, send_connection, settings):
    po, receipt = _flowed(monkeypatch, user, subcontractor, item)
    receipt_url = reverse("purchasing:receipt_detail", args=[receipt.pk])

    shown = _flow_section(_detail(logged_in, po)[1])
    assert f'href="{receipt_url}">受入 (10 個)</a> <span class="tabular-nums">({comma(receipt.amount)} 円)</span>' in shown

    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "on"}}
    hidden = _flow_section(_detail(logged_in, po)[1])
    assert f'href="{receipt_url}">受入 (10 個)</a>' in hidden
    assert " 円)" not in hidden


@pytest.mark.django_db
def test_cancelled_order_ends_with_cancel_event(user, logged_in, subcontractor, item, no_http):
    today = timezone.localdate()
    po = services.cancel_purchase_order(_order(user, subcontractor, item, today), user, reason="作り物の取消")

    events = events_for(po)

    assert [(e.kind, e.title, e.when) for e in events] == [
        ("order", "発注を登録", today),
        ("cancel", "発注を取消", None),
    ]
    section = _flow_section(_detail(logged_in, po)[1])
    assert section.index("発注を登録") < section.index("発注を取消")
    assert '<span class="font-mono tabular-nums">—</span>' in section


@pytest.mark.django_db
def test_failed_send_and_rejected_reply(monkeypatch, user, subcontractor, item, send_connection):
    today = timezone.localdate()
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("作り物の失敗")))
    po = _order(user, subcontractor, item, today)
    (failed,) = po.outbox_messages.all()
    monkeypatch.setattr("requests.post", FakePost())
    po = services.resend_purchase_order(po, user)
    rejected = services.mark_rejected(po, user, reason="作り物の断り")

    titles = [(e.kind, e.title) for e in events_for(rejected)]
    assert titles == [
        ("order", "発注を登録"),
        ("sent", f"注文を送れなかった ({failed.response})"),
        ("sent", "注文を送った (相手が受け取った)"),
        ("reply", "相手に断られた (理由: 作り物の断り)"),
    ]

    # 断られた後に取り消しても、断られた後に全部を受け入れても (納品された)、注文回答は断りのまま
    cancelled = services.cancel_purchase_order(rejected, user)
    assert [e.title for e in events_for(cancelled)][-2:] == ["相手に断られた (理由: 作り物の断り)", "発注を取消"]
    other = services.mark_rejected(_order(user, subcontractor, item, today), user, reason="作り物の断り")
    services.receive(other.lines.get(), Decimal("10"), today, user)
    other.refresh_from_db()
    assert other.status == other.STATUS_DELIVERED
    assert "相手に断られた (理由: 作り物の断り)" in [e.title for e in events_for(other)]


@pytest.mark.django_db
def test_detail_shows_flow_section_in_live_part(monkeypatch, user, logged_in, subcontractor, item, send_connection):
    po, receipt = _flowed(monkeypatch, user, subcontractor, item)

    response, body = _detail(logged_in, po)

    assert "purchasing/_timeline.html" in [template.name for template in response.templates]
    assert '<h2 id="po-flow-title" class="text-lg font-bold">流れ</h2>' in body
    # 枠は明細と検収の節の後、2 秒ごとに入れ替わる #po-live の中 (取りに行く要素の前)
    assert body.index('<div id="po-live">') < body.index('id="po-lines-title"') < body.index('id="po-acceptance-title"')
    assert body.index('id="po-acceptance-title"') < body.index('id="po-flow-title"') < body.index('id="purchaseorder-detail-poll"')
    section = _flow_section(body)
    assert section.count("data-flow-kind=") == len(FLOW_KINDS)
    receipt_url = reverse("purchasing:receipt_detail", args=[receipt.pk])
    assert f'<a class="link link-primary" href="{receipt_url}">検収を送った (受入 10 個)</a>' in section
    assert "<span>明細 1</span>" in section
    assert "data-modal-form" not in section
    # 流れは枠で、関係する物のタブは今のまま
    assert [tab["label"] for tab in response.context["tabs"]] == ["送った記録", "受入"]
