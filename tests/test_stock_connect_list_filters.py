"""在庫の動きの一覧と受信した注文の一覧 (connect の InboxListView) の絞り (N121。N117 の土台の宣言。本人の決め 11-04 15:0x)。名前は作り物だけ。"""

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.connect.models import Inbox
from seizou.masters import services as masters_services
from seizou.stock import services as stock_services

TOKYO = ZoneInfo("Asia/Tokyo")


@pytest.fixture
def partners(user):
    north = masters_services.create_partner({"code": "C001", "name": "作り物の北の得意先", "role": "customer"}, user)
    south = masters_services.create_partner({"code": "C002", "name": "作り物の南の得意先", "role": "customer"}, user)
    return north, south


def _inbox(partner, user, order_no, at):
    return connect_services.record_inbox(partner, {"order_no": order_no, "lines": []}, user, received_at=at)


@pytest.mark.django_db
def test_stock_move_list_filters_by_kind_item_and_period(logged_in, user):
    shaft = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    plate = masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user)
    counted = stock_services.adjust(shaft, Decimal("10"), date(2026, 11, 1), user)
    issued = stock_services.record_issued(shaft, Decimal("2"), date(2026, 11, 2), user)
    plate_counted = stock_services.adjust(plate, Decimal("5"), date(2026, 11, 3), user)
    plate_issued = stock_services.record_issued(plate, Decimal("1"), date(2026, 11, 4), user)
    url = reverse("stock:stockmove_list")

    body = logged_in.get(url).content.decode()
    for name in ("f_kind", "f_item", "f_moved_from", "f_moved_to"):
        assert f'name="{name}"' in body
    for label in ("種類", "部品", "期間"):
        assert f">{label}</label>" in body
    assert '<option value="issued">払い出し</option>' in body
    assert '<option value="shipped">' not in body  # 出荷は動きの表に書かないので候補に出さない
    assert f'<option value="{plate.pk}">{plate}</option>' in body

    response = logged_in.get(url, {"f_kind": "issued"})
    assert [row["pk"] for row in response.context["rows"]] == [plate_issued.pk, issued.pk]
    response = logged_in.get(url, {"f_kind": "adjusted", "f_moved_from": "2026-11-02", "f_moved_to": "2026-11-03"})
    assert [row["pk"] for row in response.context["rows"]] == [plate_counted.pk]
    response = logged_in.get(url, {"f_item": str(shaft.pk), "f_moved_to": "2026-11-01"})
    assert [row["pk"] for row in response.context["rows"]] == [counted.pk]
    # 候補に無い種類 (出荷) は絞らない。今の GET の item と絞りはかけ合わせる
    assert logged_in.get(url, {"f_kind": "shipped"}).context["paginator"].count == 4
    response = logged_in.get(url, {"item": str(plate.pk), "f_kind": "issued"})
    assert [row["pk"] for row in response.context["rows"]] == [plate_issued.pk]

    csv_text = logged_in.get(url, {"f_kind": "issued", "format": "csv"}).content.decode("utf-8-sig")
    assert len(list(csv.reader(io.StringIO(csv_text)))) == 3  # 見出しと 2 行


@pytest.mark.django_db
def test_inbox_list_filters_by_status_and_partner_and_poll_keeps_filters(logged_in, user, partners):
    north, south = partners
    first = _inbox(north, user, "X-1", datetime(2026, 11, 2, 9, 0, tzinfo=TOKYO))
    second = _inbox(south, user, "X-2", datetime(2026, 11, 2, 10, 0, tzinfo=TOKYO))
    third = _inbox(north, user, "X-3", datetime(2026, 11, 2, 11, 0, tzinfo=TOKYO))
    Inbox.objects.filter(pk=first.pk).update(status=Inbox.STATUS_REJECTED)
    url = reverse("connect:inbox_list")

    body = logged_in.get(url).content.decode()
    for name in ("f_status", "f_partner", "f_received_from", "f_received_to"):
        assert f'name="{name}"' in body
    for label in ("状態", "取引先", "受信日"):
        assert f">{label}</label>" in body
    assert f'<option value="{south.pk}">{south}</option>' in body
    assert body.count('hx-include="[data-list-filter]"') >= 1  # 2 秒ごとの取りに行きも絞りの値を送る

    response = logged_in.get(url, {"f_status": Inbox.STATUS_REJECTED})
    assert [row["pk"] for row in response.context["rows"]] == [first.pk]
    response = logged_in.get(url, {"f_partner": str(north.pk)})
    assert [row["pk"] for row in response.context["rows"]] == [third.pk, first.pk]
    response = logged_in.get(url, {"f_partner": str(south.pk), "f_status": Inbox.STATUS_RECEIVED})
    assert [row["pk"] for row in response.context["rows"]] == [second.pk]

    # 絞りの値を持った取りに行き: 合う行だけを足し、上の「全 N 件」は絞りの後の数
    newer = _inbox(north, user, "X-4", datetime(2026, 11, 2, 12, 0, tzinfo=TOKYO))
    _inbox(south, user, "X-5", datetime(2026, 11, 2, 13, 0, tzinfo=TOKYO))
    poll = logged_in.get(url, {"after": str(third.pk), "f_partner": str(north.pk)}, HTTP_HX_REQUEST="true").content.decode()
    assert poll.count('id="row-') == 1 and f'id="row-{newer.pk}"' in poll
    assert 'id="list-count" class="font-medium tabular-nums text-base-content" hx-swap-oob="true">3</span>' in poll

    # 絞りに合う行が無い一覧の取りに行き (after=0) は、絞る前の表を返さない
    empty = {"f_status": Inbox.STATUS_REJECTED, "f_partner": str(south.pk)}
    assert logged_in.get(url, empty).context["paginator"].count == 0
    poll = logged_in.get(url, {"after": "0", **empty}, HTTP_HX_REQUEST="true").content.decode()
    assert "<table" not in poll and "に合う物はありません。" in poll and "絞りを外す" in poll


@pytest.mark.django_db
def test_inbox_received_period_compares_local_dates(logged_in, user, partners):
    north, _south = partners
    # 11-04 00:30 (東京) は UTC では 11-03。日付は東京の暦で比べる
    before_midnight = _inbox(north, user, "X-1", datetime(2026, 11, 3, 23, 30, tzinfo=TOKYO))
    after_midnight = _inbox(north, user, "X-2", datetime(2026, 11, 4, 0, 30, tzinfo=TOKYO))
    url = reverse("connect:inbox_list")

    response = logged_in.get(url, {"f_received_from": "2026-11-04"})
    assert [row["pk"] for row in response.context["rows"]] == [after_midnight.pk]
    response = logged_in.get(url, {"f_received_to": "2026-11-03"})
    assert [row["pk"] for row in response.context["rows"]] == [before_midnight.pk]
    response = logged_in.get(url, {"f_received_from": "2026-11-03", "f_received_to": "2026-11-04"})
    assert response.context["paginator"].count == 2
    received = response.context["filters"][2]
    assert (received["value_from"], received["value_to"]) == ("2026-11-03", "2026-11-04")
