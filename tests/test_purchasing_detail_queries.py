"""発注の詳細の問い合わせの数が、明細の数で増えない事を見る (本体の直し 11-12)。

訳: 発注の詳細は 2 秒ごとに取り直す。段の帯 (stage_band)・流れの枠 (events_for)・明細の表の「相手からの出荷の知らせ」が、
明細ごとに出荷の知らせと受入を問い合わせていたので、明細が多い発注では取り直すたびに問い合わせが明細の数だけ増えた
(明細 1 行につき 5 回ほど。受入 1 件につき受け入れた人で 1 回)。明細・出荷の知らせ・受入をまとめて読む形にした。
作り物の協力企業と部品だけを使う。相手への HTTP は偽物 (呼ばれたら落とす) にする。
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.masters import services as masters_services
from seizou.purchasing import services, timeline


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", refuse)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_superuser(username="tester", password="tests-only")


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


def _order(partner, user, lines: int):
    """明細が lines 行の発注を「送った」にし、どの明細にも出荷の知らせ 1 件と受入 1 件を付ける。"""
    items = [
        masters_services.create_item({"part_no": f"Q-{uuid.uuid4().hex[:8]}", "name": "作り物の軸", "unit": "個"}, user)
        for _ in range(lines)
    ]
    po = services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")} for item in items],
        user,
        order_date=date(2026, 11, 2),
        send=False,
    )
    po = services.mark_sent(po, user)
    for index, line in enumerate(po.lines.order_by("line_no")):
        connect_services.record_shipping_notice(
            line, Decimal("2"), date(2026, 11, 3), user, message_id=uuid.uuid4(), shipment_no=f"SH-{po.pk}-{index}"
        )
        services.receive(line, Decimal("2"), date(2026, 11, 4), user)
    return po


def _detail_queries(client, po) -> int:
    url = reverse("purchasing:purchaseorder_detail", args=[po.pk])
    assert client.get(url).status_code == 200  # 最初の 1 回の控え (session など) を外す
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    assert response.status_code == 200
    body = response.content.decode()
    assert body.count("の出荷の知らせ (相手の出荷番号") == po.lines.count()  # 流れの枠に、明細ごとの出荷の知らせが出ている
    return len(queries.captured_queries)


@pytest.mark.django_db
def test_detail_query_count_does_not_grow_with_lines(client, user, subcontractor):
    client.force_login(user)
    small = _detail_queries(client, _order(subcontractor, user, 1))
    large = _detail_queries(client, _order(subcontractor, user, 5))
    assert small == large, (small, large)


@pytest.mark.django_db
def test_timeline_reads_lines_notices_and_receipts_in_three_queries(user, subcontractor):
    po = _order(subcontractor, user, 4)
    with CaptureQueriesContext(connection) as queries:
        lines = timeline.lines_for(po)
        # 明細ごとの出荷の知らせ・受入・受け入れた人は、先に読んだ物から辿る (問い合わせを足さない)
        seen = [(len(line.shipping_notices.all()), [str(receipt.received_by) for receipt in line.receipts.all()]) for line in lines]
    assert len(lines) == 4
    assert seen == [(1, ["tester"])] * 4
    assert len(queries.captured_queries) == 3  # 明細・出荷の知らせ・受入 (受け入れた人は join)


@pytest.mark.django_db
def test_band_and_events_are_the_same_with_and_without_given_lines(user, subcontractor):
    """段の帯と流れの出来事は、先に読んだ明細の並びを渡しても、渡さなくても同じ (渡さなければ自分で読む)。"""
    po = _order(subcontractor, user, 3)
    lines = timeline.lines_for(po)

    assert timeline.stage_band(po, lines) == timeline.stage_band(po)
    assert timeline.events_for(po, lines) == timeline.events_for(po)
    with CaptureQueriesContext(connection) as queries:
        timeline.stage_band(po, lines)
        timeline.events_for(po, lines)
    # 渡した時に読むのは、送った注文の行だけ (段の帯と流れの枠で 1 回ずつ)
    assert len(queries.captured_queries) == 2
