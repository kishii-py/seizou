"""ダッシュボード (core:home) の納期アラートと月次の推移の図。Django の test client で HTML を見る。

日は timezone.localdate() から数える (月の鍵も今日から数える)。名前は作り物だけ。
図の見た目 (字の重なりと棒の色) は朝に本体が目で見る。
"""

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.core import dashboard
from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.shipping import services as shipping_services


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def make_order(user, customer, item, due):
    """今日の受注 (数量 10・単価 100 の明細 1 行。納期は due)。"""
    return services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": due}],
        user,
        order_date=timezone.localdate(),
    )


def home(client):
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    return response.content.decode()


def month_keys(today, months=6):
    last = today.year * 12 + today.month - 1
    return [f"{index // 12:04d}-{index % 12 + 1:02d}" for index in range(last - months + 1, last + 1)]


def chart_svg(body):
    """図の svg だけを切り出す (画面の全体には絵の svg があり、fill="none" を持つため)。"""
    start = body.index('<svg id="trend-chart"')
    return body[start : body.index("</svg>", start)]


class _NoParts:
    def get_model(self, app_label, name):
        raise LookupError(f"{app_label}.{name}")


@pytest.mark.django_db
def test_home_shows_alerts_and_trend(logged_in, user, customer, shaft):
    today = timezone.localdate()
    make_order(user, customer, shaft, today - timedelta(days=3))
    body = home(logged_in)
    assert "納期アラート" in body and "月次の推移" in body
    assert '<svg id="trend-chart"' in body
    for key in month_keys(today):
        assert key in body, key


@pytest.mark.django_db
def test_overdue_row_links_to_order(logged_in, user, customer, shaft):
    today = timezone.localdate()
    late = make_order(user, customer, shaft, today - timedelta(days=3))
    soon = make_order(user, customer, shaft, today + timedelta(days=3))
    body = home(logged_in)
    assert late.order_no in body and soon.order_no in body
    assert f'href="{reverse("orders:order_detail", args=[late.pk])}"' in body
    assert '<span class="badge badge-sm badge-soft badge-error">3 日遅れ</span>' in body
    assert '<span class="badge badge-sm badge-soft badge-warning">あと 3 日</span>' in body
    assert "遅れ 1 件" in body and "7 日以内 1 件" in body
    assert f'href="{reverse("orders:orderline_list")}"' in body and "注文残の一覧を見る" in body
    assert "遅れている納期はありません。" not in body


@pytest.mark.django_db
def test_no_overdue_sentence(logged_in, user, customer, shaft):
    today = timezone.localdate()
    make_order(user, customer, shaft, today)
    body = home(logged_in)
    assert "遅れている納期はありません。" in body
    assert '<span class="badge badge-sm badge-soft badge-warning">今日</span>' in body


@pytest.mark.django_db
def test_empty_trend_sentence(logged_in):
    body = home(logged_in)
    assert 'id="trend-chart"' not in body
    assert "直近 6 か月の受注と出荷はまだありません。" in body
    # 遅れも近いも 0 件なら、表を出さずに 1 文だけ。
    assert "遅れている納期はありません。" in body
    assert "<th class=\"font-medium\">品番</th>" not in body


@pytest.mark.django_db
def test_chart_svg_uses_theme_classes_only(logged_in, user, customer, shaft):
    today = timezone.localdate()
    order = make_order(user, customer, shaft, None)
    shipping_services.ship(order.lines.get(), Decimal("4"), today, user)
    body = home(logged_in)
    svg = chart_svg(body)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", svg)
    for word in ('fill="', 'stroke="', "rgb(", "<script"):
        assert word not in svg, word
    for word in ('role="img"', "aria-label=", "<title>", "fill-primary", "fill-secondary", "stroke-base-300"):
        assert word in svg, word
    # 棒の title の数と図の下の表の数が合う。
    key = today.strftime("%Y-%m")
    assert f"{key} 受注 1,000 円" in svg and f"{key} 出荷 400 円" in svg
    table = body[body.index("</svg>") :]
    assert ">1,000</td>" in table and ">400</td>" in table


@pytest.mark.django_db
def test_home_keeps_cards_and_words(logged_in, user, customer, shaft):
    make_order(user, customer, shaft, timezone.localdate() - timedelta(days=1))
    body = home(logged_in)
    assert "今月の受注" in body and "最近の受注" in body
    for word in ("品目", "注残の明細", "はじめに"):
        assert word not in body, word


@pytest.mark.django_db
def test_home_without_orders_part_hides_sections(logged_in, monkeypatch):
    monkeypatch.setattr(dashboard, "apps", _NoParts())
    body = home(logged_in)
    assert "納期アラート" not in body and "月次の推移" not in body
    assert 'id="trend-chart"' not in body


@pytest.mark.django_db
def test_bottom_row_is_orders_left_alerts_right(logged_in, user, customer, shaft):
    make_order(user, customer, shaft, timezone.localdate() - timedelta(days=3))
    body = home(logged_in)
    recent = body.index('id="recent-orders-title"')
    alerts = body.index('id="due-alerts-title"')
    entrances = body.index('id="entrances-title"')
    assert recent < alerts < entrances


@pytest.mark.django_db
def test_rows_open_order_detail_on_click(logged_in, user, customer, shaft):
    late = make_order(user, customer, shaft, timezone.localdate() - timedelta(days=3))
    url = reverse("orders:order_detail", args=[late.pk])
    body = home(logged_in)
    row = (
        '<tr class="row-hover h-10 cursor-pointer" '
        f"onclick=\"if(!event.target.closest('a')){{window.location='{url}'}}\">"
    )
    # 「最近の受注」の行と、納期アラートの遅れの行の 2 つ。
    assert body.count(row) == 2
    recent = body[body.index('id="recent-orders-title"') : body.index('id="due-alerts-title"')]
    alerts = body[body.index('id="due-alerts-title"') : body.index('id="entrances-title"')]
    for part in (recent, alerts):
        assert row in part
        # 行の中の受注番号の link も残る (キーボードは Tab で link に乗って Enter)。
        tail = part[part.index(row) :]
        assert re.search(rf'<a [^>]*href="{re.escape(url)}">{re.escape(late.order_no)}</a>', tail)
