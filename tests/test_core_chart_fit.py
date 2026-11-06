"""ダッシュボードの棒の図 3 つ (出荷の数量・出荷の金額・生産) を箱の幅に収め、図の字を svg の外の 12px の字にする (候補 C50 の 1 と 2。C50-1 = a)。

見る所: 3 つの svg の class に最小の幅 (min-w-) が無く、svg の中に <text が無い。各図の svg のすぐ後ろの字の包み (class に text-xs) に、
今月を含む 6 か月の月の字と目盛りの字があり、dashboard の割合の鍵 (目盛りの top・月の left・tick_label_right) の位置に置かれる。
画面の幅で測る確かめ (箱に収まる・字が 12px 以上) は test client では見られない (本体の手の確かめ)。
fixture (logged_in・chart_data ほか) と chart_svg は tests/test_core_dashboard_views.py から写した (そのファイルはほかの依頼が直すので import しない)。
名前は作り物だけ。
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
from seizou.progress import services as progress_services
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services

CHART_IDS = ("shipping-qty-chart", "shipping-amount-chart", "production-chart")


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


@pytest.fixture
def cutting(user):
    return masters_services.create_process({"code": "P01", "name": "作り物の切削", "sort_order": 1}, user)


@pytest.fixture
def chart_data(user, customer, shaft, cutting):
    """納期が 3 日前の受注 1 つを今日 4 だけ出荷し、指示書 (数量 4・工程 1 つ) に今 4 の実績を入れた物。"""
    order = services.create_order(
        customer,
        [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": timezone.localdate() - timedelta(days=3)}],
        user,
        order_date=timezone.localdate(),
    )
    line = order.lines.get()
    shipping_services.ship(line, Decimal("4"), timezone.localdate(), user)
    workorder = workorders_services.issue_workorder(
        line, Decimal("4"), [{"process": cutting, "planned_qty": Decimal("4")}], user
    )
    progress_services.record_progress(workorder.steps.get(), Decimal("4"), timezone.now(), user)
    return order


def home(client):
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    return response.content.decode()


def chart_svg(body, chart_id):
    """図の svg だけを切り出す (画面の全体には絵の svg があり、fill="none" を持つため)。"""
    start = body.index(f'<svg id="{chart_id}"')
    return body[start : body.index("</svg>", start)]


def chart_words(body, chart_id):
    """図の svg のすぐ後ろの字の包み: (包みの class, [(span の style, 字), ...])。"""
    start = body.index(f'<svg id="{chart_id}"')
    after = body[body.index("</svg>", start) + len("</svg>") :]
    found = re.match(r'\s*<div class="([^"]*)" aria-hidden="true">(.*?)</div>', after, re.S)
    assert found, chart_id
    spans = re.findall(r'<span class="[^"]*" style="([^"]*)">([^<]*)</span>', found.group(2))
    return found.group(1), spans


def expected_charts():
    """画面と同じ今日で数えた、3 つの図の数 (dashboard が返す物)。"""
    shipping = dashboard.shipping_trend()
    return {
        "shipping-qty-chart": shipping["qty_chart"],
        "shipping-amount-chart": shipping["amount_chart"],
        "production-chart": dashboard.production_trend()["chart"],
    }


@pytest.mark.django_db
def test_bar_charts_fit_box_without_text_in_svg(logged_in, chart_data):
    body = home(logged_in)
    for chart_id in CHART_IDS:
        svg = chart_svg(body, chart_id)
        classes = re.match(r'<svg id="[^"]*" class="([^"]*)"', svg).group(1).split()
        assert not [name for name in classes if name.startswith("min-w-")], (chart_id, classes)
        assert {"w-full", "h-auto"} <= set(classes), chart_id
        assert "<text" not in svg, chart_id
        # 棒と目盛りの線と読み上げの字は svg に残る
        for word in ('role="img"', "aria-label=", "<title>", "<path", '<line class="stroke-base-300"'):
            assert word in svg, (chart_id, word)


@pytest.mark.django_db
def test_bar_chart_words_outside_svg_in_text_xs(logged_in, chart_data):
    today = timezone.localdate()
    body = home(logged_in)
    charts = expected_charts()
    for chart_id in CHART_IDS:
        chart = charts[chart_id]
        wrapper_class, spans = chart_words(body, chart_id)
        assert {"text-xs", "text-base-content/70"} <= set(wrapper_class.split()), chart_id
        words = [word for _style, word in spans]
        # 目盛りの字 (下から) と、今月を含む 6 か月の月の字
        tick_words = [tick["label"] for tick in chart["ticks"]]
        month_words = [label["label"] for label in chart["labels"]]
        assert len(month_words) == 6 and month_words[-1] == f"{today.month} 月"
        assert words == tick_words + month_words, chart_id
        styles = [style for style, _word in spans]
        for tick, style in zip(chart["ticks"], styles):
            assert f"top: {tick['top']}%" in style and f"right: {chart['tick_label_right']}%" in style, (chart_id, style)
        for label, style in zip(chart["labels"], styles[len(tick_words) :]):
            assert f"left: {label['left']}%" in style, (chart_id, style)
