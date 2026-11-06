"""ダッシュボードの数字のカード「出荷の残りがある明細」に添える受注の件数 (初見ユーザーテスト R5 の V78。候補 C30 の 3)。

見る所: 数の下の 1 行「受注 n 件」の n が、受注の一覧の既定 (タブ「出荷の残りがある」と同じ条件) と、今日やることの
出荷の行の件数と同じ・大きい数 (明細の行) は今のまま生産管理ボードの件数と同じ・受注の件数を数えられないときは
1 行ごと出さずカードは出す・今日やることの出荷の段が同じ関数で数える。Django の test client で HTML を見る。名前は作り物だけ。
行き先の画面の名前の link と (i) の小窓 (候補 C30 の 1 と 2) の試験は、その直しと一緒にこのファイルへ足す。
"""

import re
from decimal import Decimal
from html import unescape

import pytest
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone

from seizou.core import dashboard, views
from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.shipping import services as shipping_services

LINES_LABEL = "出荷の残りがある明細"
TITLE = re.compile(r'<p class="text-xs font-medium text-base-content/70">([^<]*)</p>')
DETAIL = '<p class="mt-1 text-sm text-base-content/70">'


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def make_order(user, customer, item, qtys):
    """数量の並び qtys の明細を持つ、今日の受注を作る (tests/test_core_dashboard_cards.py の make_order と同じ作り)。"""
    return services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100"), "due_date": None} for qty in qtys],
        user,
        order_date=timezone.localdate(),
    )


def lines_stat():
    return next(stat for stat in views.dashboard_stats() if stat["label"] == LINES_LABEL)


def card_html(client, label):
    """ダッシュボードの数字のカードの枠 (data-tour="stats") の中で、題が label のカードの HTML (題の <p> から次のカードの題の手前まで)。"""
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    body = response.content.decode()
    start = body.index('data-tour="stats"')
    end = body.find('data-tour="flow-guide"', start)
    part = body[start : end if end != -1 else len(body)]
    found = list(TITLE.finditer(part))
    for index, match in enumerate(found):
        if unescape(match.group(1)) == label:
            return part[match.start() : found[index + 1].start() if index + 1 < len(found) else len(part)]
    raise AssertionError(f"カードが無い: {label}")


@pytest.mark.django_db
def test_lines_card_shows_open_order_count(logged_in, user, customer, shaft):
    today = timezone.localdate()
    make_order(user, customer, shaft, ["1", "2"])  # 未出荷 (明細 2 行)
    partial = make_order(user, customer, shaft, ["3", "4"])
    shipping_services.ship(partial.lines.get(line_no=1), Decimal("3"), today, user)  # 一部出荷 (残りは 2 行目だけ)
    shipped = make_order(user, customer, shaft, ["5"])
    shipping_services.ship(shipped.lines.get(), Decimal("5"), today, user)  # 全部を出荷
    cancelled = make_order(user, customer, shaft, ["6"])
    services.cancel_order(cancelled, user)
    partial.refresh_from_db()
    shipped.refresh_from_db()
    assert (partial.status, shipped.status) == (partial.STATUS_PARTIAL, shipped.STATUS_SHIPPED)

    stat = lines_stat()

    # 大きい数は明細の行 (未出荷の 2 行と一部出荷の 2 行目) で、押した先の生産管理ボードの件数と同じ。添える件数は受注 2 件
    assert stat["value"] == 3
    assert stat["unit"] == "行"
    assert stat["detail"] == "受注 2 件"
    assert logged_in.get(stat["url"]).context["paginator"].count == stat["value"]
    # 受注の一覧の既定 (タブ「出荷の残りがある」と同じ条件) と、今日やることの出荷の行と同じ数
    assert logged_in.get(reverse("orders:order_list")).context["paginator"].count == 2
    shipment_rows = [row for row in dashboard.today_tasks()["rows"] if row["key"] == "shipment"]
    assert [row["count"] for row in shipment_rows] == [2]
    # 画面では数の下の 1 行に出る
    assert DETAIL + "受注 2 件</p>" in card_html(logged_in, LINES_LABEL)


class _UnreadableOrders:
    """count で DB の誤りを投げる作り物 (DB が読めないときの形)。"""

    def count(self):
        raise DatabaseError("作り物の読めない DB")


@pytest.mark.parametrize("orders", [None, _UnreadableOrders()], ids=["no-orders", "db-error"])
@pytest.mark.django_db
def test_lines_card_without_order_count(logged_in, user, customer, shaft, monkeypatch, orders):
    make_order(user, customer, shaft, ["1"])
    monkeypatch.setattr(dashboard, "orders_to_ship", lambda: orders)

    stat = lines_stat()

    # 受注の件数を数えられないときは 1 行ごと出さない (偽の 0 を置かない)。カードと大きい数は出る
    assert "detail" not in stat
    assert stat["value"] == 1
    card = card_html(logged_in, LINES_LABEL)
    assert DETAIL not in card
    assert "件</p>" not in card


def test_today_tasks_count_shipment_with_same_function():
    # カードの受注の件数と今日やることの出荷の行は、同じ 1 つの関数で数える
    assert dashboard._FLOW_COUNTS["shipment"] is dashboard.orders_to_ship
