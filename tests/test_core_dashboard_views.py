"""ダッシュボード (core:home) の節の並び・納期アラート・出荷と生産の図・行のクリック。Django の test client で HTML を見る。

日は timezone.localdate() から数える (月の鍵も今日から数える)。実績の日時は timezone.now()、指示書の発行日は
services が今日にする物のまま。名前は作り物だけ。図の見た目 (字の重なりと棒の色) は朝に本体が目で見る。
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


def chart_svg(body, chart_id):
    """図の svg だけを切り出す (画面の全体には絵の svg があり、fill="none" を持つため)。"""
    start = body.index(f'<svg id="{chart_id}"')
    return body[start : body.index("</svg>", start)]


#: 2 つの枠を lg から横に並べる段の包み / 片方だけのときの 1 列の包み (home.html の字のまま)
ROW_TWO = '<div class="mb-6 grid gap-6 lg:grid-cols-2">'
ROW_ONE = '<div class="mb-6 grid gap-6">'


def row_of(body, title_id):
    """節の題 (id="<title_id>") を含む段の包みの HTML (包みの開きから、次の段の包みかよく使う画面の手前まで)。"""
    at = body.index(f'id="{title_id}"')
    start = max(body.rfind(ROW_TWO, 0, at), body.rfind(ROW_ONE, 0, at))
    assert start != -1, title_id
    ends = [found for found in (body.find(ROW_TWO, at), body.find(ROW_ONE, at), body.find('id="entrances-title"', at)) if found != -1]
    return body[start : min(ends)]


def section_of(body, title_id):
    """節 (<section aria-labelledby="<title_id>">) の HTML。"""
    return re.search(rf'<section[^>]*aria-labelledby="{title_id}".*?</section>', body, re.S).group(0)


class _NoParts:
    def get_model(self, app_label, name):
        raise LookupError(f"{app_label}.{name}")


@pytest.fixture
def cutting(user):
    return masters_services.create_process({"code": "P01", "name": "作り物の切削", "sort_order": 1}, user)


@pytest.fixture
def chart_data(user, customer, shaft, cutting):
    """納期が 3 日前の受注 1 つを今日 4 だけ出荷し、指示書 (数量 4・工程 1 つ) に今 4 の実績を入れた物。"""
    order = make_order(user, customer, shaft, timezone.localdate() - timedelta(days=3))
    line = order.lines.get()
    shipping_services.ship(line, Decimal("4"), timezone.localdate(), user)
    workorder = workorders_services.issue_workorder(
        line, Decimal("4"), [{"process": cutting, "planned_qty": Decimal("4")}], user
    )
    progress_services.record_progress(workorder.steps.get(), Decimal("4"), timezone.now(), user)
    return order


@pytest.mark.django_db
def test_home_shows_alerts_and_charts(logged_in, chart_data):
    today = timezone.localdate()
    body = home(logged_in)
    for word in ("納期アラート", "出荷 (直近 6 か月)", "生産 (直近 6 か月)"):
        assert word in body, word
    for chart_id in ("shipping-qty-chart", "shipping-amount-chart", "production-chart"):
        assert f'<svg id="{chart_id}"' in body, chart_id
    for key in month_keys(today):
        assert key in body, key
    assert "月次の推移" not in body and "trend-chart" not in body


@pytest.mark.django_db
def test_sections_are_in_the_decided_order(logged_in, chart_data):
    body = home(logged_in)
    # 数字のカード → [今日やること | 納期アラート] → [出荷 | 生産] → [最近の受注 | 注文の入口] → よく使う画面 (本人の決め 11-05 11:0x)
    marks = [
        "今月の受注</p>",
        'id="flow-guide-title"',
        'id="due-alerts-title"',
        'id="shipping-title"',
        'id="production-title"',
        'id="recent-orders-title"',
        'id="inlets-title"',
        'id="entrances-title"',
    ]
    positions = [body.index(mark) for mark in marks]
    assert positions == sorted(positions)
    # よく使う画面は登録が 0 件なので、1 行の案内だけ。
    entrances = re.search(r'<section[^>]*aria-labelledby="entrances-title".*?</section>', body, re.S).group(0)
    assert "上の帯の ☆ で登録した画面がここに並びます。" in entrances
    assert "<ul" not in entrances


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
    assert f'href="{reverse("orders:orderline_list")}"' in body and "生産管理ボードを見る" in body
    assert "遅れている納期はありません。" not in body


@pytest.mark.django_db
def test_no_overdue_sentence(logged_in, user, customer, shaft):
    today = timezone.localdate()
    make_order(user, customer, shaft, today)
    body = home(logged_in)
    assert "遅れている納期はありません。" in body
    assert '<span class="badge badge-sm badge-soft badge-warning">今日</span>' in body


@pytest.mark.django_db
def test_empty_charts_sentences(logged_in):
    body = home(logged_in)
    assert 'id="shipping-qty-chart"' not in body and 'id="production-chart"' not in body
    assert "直近 6 か月の出荷はまだありません。" in body
    assert "直近 6 か月の指示書と実績はまだありません。" in body
    # 遅れも近いも 0 件なら、表を出さずに 1 文だけ。
    assert "遅れている納期はありません。" in body
    assert "<th class=\"font-medium\">品番</th>" not in body


@pytest.mark.django_db
def test_chart_svgs_use_theme_classes_only(logged_in, chart_data):
    today = timezone.localdate()
    body = home(logged_in)
    svgs = {
        chart_id: chart_svg(body, chart_id)
        for chart_id in ("shipping-qty-chart", "shipping-amount-chart", "production-chart")
    }
    for chart_id, svg in svgs.items():
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", svg), chart_id
        for word in ('fill="', 'stroke="', "rgb(", "<script"):
            assert word not in svg, (chart_id, word)
        for word in ('role="img"', "aria-label=", "<title>", "fill-primary", "stroke-base-300"):
            assert word in svg, (chart_id, word)
    assert "fill-secondary" in svgs["production-chart"]
    # 棒の title の数と図の下の表の数が合う。
    key = today.strftime("%Y-%m")
    assert f"{key} 出荷の数量 4</title>" in svgs["shipping-qty-chart"]
    assert f"{key} 出荷の金額 400 円</title>" in svgs["shipping-amount-chart"]
    assert f"{key} 発行 4</title>" in svgs["production-chart"]
    assert f"{key} 完成 4</title>" in svgs["production-chart"]
    shipping = body[body.index('id="shipping-title"') : body.index('id="production-title"')]
    production = body[body.index('id="production-title"') : body.index('id="recent-orders-title"')]
    shipping_table = shipping[shipping.rindex("</svg>") :]
    production_table = production[production.rindex("</svg>") :]
    assert ">400</td>" in shipping_table and ">4</td>" in shipping_table
    assert ">4</td>" in production_table


@pytest.mark.django_db
def test_money_hidden_drops_amount_chart(settings, logged_in, chart_data):
    # Access の行の無い普通の利用者は、金額の閲覧が on の会社では金額を見られない。
    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "on"}}
    response = logged_in.get(reverse("core:home"))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'id="shipping-qty-chart"' in body and "出荷 (直近 6 か月)" in body
    assert 'id="shipping-amount-chart"' not in body
    assert response.context["shipping"]["amount_chart"] is None
    # 表の金額の欄は残り、枡は「—」。
    shipping = body[body.index('id="shipping-title"') : body.index('id="production-title"')]
    assert "金額 (円)</th>" in shipping
    assert '<span class="text-base-content/50">—</span>' in shipping
    assert ">400</td>" not in shipping
    assert 'id="production-chart"' in body


@pytest.mark.django_db
def test_home_keeps_cards_and_words(logged_in, user, customer, shaft):
    make_order(user, customer, shaft, timezone.localdate() - timedelta(days=1))
    body = home(logged_in)
    assert "今月の受注" in body and "最近の受注" in body
    assert "月次の推移" not in body
    for word in ("品目", "注残の明細", "はじめに"):
        assert word not in body, word


@pytest.mark.django_db
def test_home_without_orders_part_hides_sections(logged_in, monkeypatch):
    monkeypatch.setattr(dashboard, "apps", _NoParts())
    body = home(logged_in)
    assert "納期アラート" not in body and "月次の推移" not in body
    assert 'id="trend-chart"' not in body
    assert "出荷 (直近 6 か月)" not in body and "生産 (直近 6 か月)" not in body


@pytest.mark.django_db
def test_today_left_alerts_right_in_one_row(logged_in, user, customer, shaft):
    # 今日やることは半分の幅で、右に納期アラート (本人の決め 11-05 11:0x「今日やることは col-md-6 的な大きさの方がいい」)
    make_order(user, customer, shaft, timezone.localdate() - timedelta(days=3))
    body = home(logged_in)
    row = row_of(body, "flow-guide-title")
    assert row.startswith(ROW_TWO)
    assert row == row_of(body, "due-alerts-title")
    assert row.index('id="flow-guide-title"') < row.index('id="due-alerts-title"')
    assert 'id="shipping-title"' not in row and 'id="recent-orders-title"' not in row
    # 半分の幅の枠: 格子の子が中身の幅で広がらない (min-w-0)。段の余白は包みが持つ。行の字とボタンは折り返す
    today = section_of(body, "flow-guide-title")
    assert today.startswith('<section class="min-w-0 rounded-xl border border-base-300 bg-base-100 p-6 shadow-xs"')
    assert '<li class="flex flex-wrap items-center justify-between gap-2 py-2">' in today
    assert "生産管理ボード" in today
    assert section_of(body, "due-alerts-title").startswith('<section class="min-w-0 ')


@pytest.mark.django_db
def test_recent_orders_left_inlets_right_in_one_row(logged_in, user, customer, shaft):
    # 注文の入口の図は最近の受注の右 (本人の決め 11-05 11:0x)。よく使う画面はこの段の外の全幅
    make_order(user, customer, shaft, timezone.localdate() - timedelta(days=3))
    body = home(logged_in)
    row = row_of(body, "recent-orders-title")
    assert row.startswith(ROW_TWO)
    assert row == row_of(body, "inlets-title")
    assert row.index('id="recent-orders-title"') < row.index('id="inlets-title"')
    assert '<svg id="inlet-chart"' in row
    assert 'id="due-alerts-title"' not in row and 'id="entrances-title"' not in row
    assert section_of(body, "inlets-title").startswith('<section class="min-w-0 rounded-xl border border-base-300 bg-base-100 p-6 shadow-xs"')


@pytest.mark.django_db
def test_rows_go_one_column_when_one_side_is_missing(logged_in, monkeypatch):
    # 得意先が無い = 注文の入口の節が無い。最近の受注だけの 1 列 (今日やることと納期アラートは 2 列のまま)
    body = home(logged_in)
    assert 'id="inlets-title"' not in body
    assert row_of(body, "recent-orders-title").startswith(ROW_ONE)
    assert row_of(body, "flow-guide-title").startswith(ROW_TWO)
    # 業務の流れが無い = 今日やることの枠が無い。納期アラートだけの 1 列
    monkeypatch.setattr(dashboard, "flow_guide", lambda: None)
    body = home(logged_in)
    assert 'id="flow-guide-title"' not in body
    assert row_of(body, "due-alerts-title").startswith(ROW_ONE)


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
    recent = section_of(body, "recent-orders-title")
    alerts = section_of(body, "due-alerts-title")
    for part in (recent, alerts):
        assert row in part
        # 行の中の受注番号の link も残る (キーボードは Tab で link に乗って Enter)。
        tail = part[part.index(row) :]
        assert re.search(rf'<a [^>]*href="{re.escape(url)}">{re.escape(late.order_no)}</a>', tail)
