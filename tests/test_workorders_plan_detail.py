"""生産計画の盤の計画の詳細の小窓 (N113。決め 11-04 14:5x) の試験。

見る所: 小窓の HTML に指示書の要点・部品・受注の明細・工程の並び (押した工程を太字)・現品票と PDF・在庫の見込みの図があること、
在庫の見込みの日ごとの数 (stock.services.stock_projection)、盤の棒と未計画のカードが小窓を開く hx-get を持つこと。
見ない所 (理由): ドラッグの後の click を飲む script の動き・図の見た目 (ブラウザが要る。本体が画面で見る)。
"""

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.stock import services as stock_services
from seizou.workorders import plans, services
from tests import test_workorders_plan

# 生産計画の試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
# user = 計画を置く人。order・line = 作り物の受注 (T-100 を 10、納期 2026-11-20)。cut = 計画に使う工程「切断」。
# bend = 印の無い工程「曲げ」。workorder = 切断 → 曲げ の指示書 (数量 4)。step = その切断の予定。
user = test_workorders_plan.user
order = test_workorders_plan.order
line = test_workorders_plan.line
cut = test_workorders_plan.cut
bend = test_workorders_plan.bend
workorder = test_workorders_plan.workorder
step = test_workorders_plan.step
client_logged_in = test_workorders_plan.client_logged_in

START = date(2026, 11, 9)


def _detail_url(step):
    return reverse("workorders:plan_detail", args=[step.pk])


@pytest.mark.django_db
def test_plan_detail_modal_shows_summary_steps_and_chart(settings, client_logged_in, user, order, cut, bend, workorder, step):
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 5), user)

    response = client_logged_in.get(_detail_url(step), HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    html = response.content.decode()
    assert "<html" not in html
    assert "計画の詳細" in html
    assert f'href="{reverse("workorders:workorder_detail", args=[workorder.pk])}">{workorder.wo_no}</a>' in html
    assert "T-100" in html and "作り物の軸" in html
    assert f'href="{reverse("orders:order_detail", args=[order.pk])}">{order.order_no}</a>' in html
    assert "作り物の得意先" in html
    assert "2026-11-20" in html
    steps = html[html.index("工程の並び") : html.index("</table>")]
    assert '<tr class="font-bold" aria-current="step"><td class="font-mono">1</td><td>切断</td><td>11/4 → 11/5</td></tr>' in steps
    assert "曲げ" in steps and "未計画" in steps
    assert reverse("workorders:workorder_tag", args=[workorder.pk]) in html
    assert reverse("workorders:workorder_tag_pdf", args=[workorder.pk]) in html
    assert 'id="plan-stock-chart"' in html
    assert "在庫の見込み (今日から 8 週)" in html
    assert "詳細を見る" in html

    settings.SEIZOU = {"production_plan": False}
    assert client_logged_in.get(_detail_url(step), HTTP_HX_REQUEST="true").status_code == 404


@pytest.mark.django_db
def test_plan_detail_opened_directly_is_a_whole_page(client_logged_in, user, order, workorder, step):
    """小窓の道を htmx でなく直に開いた (画面の目録・履歴・AI に質問の窓から) ときは、土台の画面の中に小窓と同じ中身を置いた画面の全体を返す
    (見つけ FB-12。指示書の工程には詳細の画面が無いので、ほかの小窓のように詳細へは移せない)。題は土台の題の段 (h1)。閉じる小窓が無いので、
    小窓の題と「閉じる」(method="dialog") は中身に出さない。「詳細を見る」と現品票の link は残す。同じ URL で中身と全体を返すので Vary: HX-Request。"""
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 5), user)

    response = client_logged_in.get(_detail_url(step))

    assert response.status_code == 200
    assert "HX-Request" in response["Vary"]
    html = response.content.decode()
    assert "<html" in html
    assert "<h1 class=\"text-3xl font-bold tracking-tight\">計画の詳細</h1>" in html
    main = html[html.index("<main") : html.index("</main>")]
    assert 'method="dialog"' not in main
    assert "閉じる" not in main
    assert "modal-action" not in main
    assert main.count("計画の詳細") == 1  # 土台の題だけ (小窓の h3 は描かない)
    assert f'href="{reverse("workorders:workorder_detail", args=[workorder.pk])}">{workorder.wo_no}</a>' in main
    assert f'href="{reverse("orders:order_detail", args=[order.pk])}">{order.order_no}</a>' in main
    assert "工程の並び" in main and "切断" in main
    assert reverse("workorders:workorder_tag", args=[workorder.pk]) in main
    assert 'id="plan-stock-chart"' in main
    assert "詳細を見る" in main

    # htmx の要求は今までどおり枠の無い中身 (題と「閉じる」つき)。詳細の画面のある表の小窓 (指示書) は今までどおり詳細へ移す
    modal = client_logged_in.get(_detail_url(step), HTTP_HX_REQUEST="true")
    assert "HX-Request" in modal["Vary"]
    assert "<html" not in modal.content.decode() and 'method="dialog"' in modal.content.decode()
    moved = client_logged_in.get(reverse("workorders:workorder_modal", args=[workorder.pk]))
    assert moved.status_code == 302
    assert moved["Location"] == reverse("workorders:workorder_detail", args=[workorder.pk])


ZERO_NOTE = "今日から 8 週、見込みの在庫はずっと 0 です。"


@pytest.mark.django_db
def test_plan_detail_chart_is_capped_at_its_own_width(client_logged_in, user, workorder, step):
    """在庫の見込みの図は viewBox の幅 (448px = max-w-md) より広げず、左に寄せ、横に滑らせない (候補 C52 の 3)。棒のある図に 0 の 1 行は出ない (4)。"""
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 5), user)

    html = client_logged_in.get(_detail_url(step), HTTP_HX_REQUEST="true").content.decode()

    # 明細 T-100 × 10 (納期 2026-11-20) が今日か納期の日に引かれ、足りない数の棒が出る
    classes = re.search(r'<svg id="plan-stock-chart" class="([^"]*)"', html).group(1).split()
    assert "max-w-md" in classes
    assert "w-full" in classes
    assert "mx-auto" not in classes
    assert "overflow-x-auto" not in html
    assert ZERO_NOTE not in html


@pytest.mark.django_db
def test_plan_detail_chart_words_outside_svg(client_logged_in, user, line, workorder, step):
    """在庫の見込みの図の目盛りと日付の字は svg の外の HTML の 12px の字 (text-xs と text-base-content/70) で、svg の箱に対する割合の
    位置に置く (候補 C50 の B。C50-1 = a)。字を置く包みも max-w-md で svg と同じ幅。C52 の上限 (max-w-md・w-full・横の送り無し) はそのまま。"""
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 5), user)

    html = client_logged_in.get(_detail_url(step), HTTP_HX_REQUEST="true").content.decode()
    chart = plans.item_projection(line.item)["chart"]

    start = html.index('<svg id="plan-stock-chart"')
    end = html.index("</svg>", start)
    svg = html[start:end]
    words = html[end : html.index("</div>", end)]
    assert html[:start].rstrip().endswith('<div class="relative mt-2 max-w-md">')
    assert "<text" not in svg
    assert svg.count('<line class="stroke-base-300"') == len(chart["ticks"])
    assert '<div class="text-xs text-base-content/70 tabular-nums" aria-hidden="true">' in words
    assert chart["ticks"] and len(chart["labels"]) == 8
    for tick in chart["ticks"]:
        place = f'top: {tick["top"]}%; right: {chart["tick_label_right"]}%; transform: translateY(-50%)'
        assert f'<span class="absolute whitespace-nowrap" style="{place}">{tick["label"]}</span>' in words
    for label in chart["labels"]:
        assert f'<span class="absolute bottom-0 whitespace-nowrap" style="left: {label["left"]}%">{label["label"]}</span>' in words
    classes = re.search(r'<svg id="plan-stock-chart" class="([^"]*)"', html).group(1).split()
    assert "max-w-md" in classes and "w-full" in classes and "mx-auto" not in classes
    assert "overflow-x-auto" not in html


def test_projection_chart_places_are_inside():
    """在庫の見込みの図の割合の鍵 (目盛りの top・日付の left・tick_label_right) が 0〜100 で、座標の鍵 (目盛りの y・日付の x・
    tick_label_x) と同じ所を指す (候補 C50 の B。小数 1 桁の字なので 0.1 までずれてよい)。0 より下の見込みも入れる。"""
    pattern = [5, 5, 3, 0, -4, -4, 2, 8]
    days = [{"date": START + timedelta(days=offset), "qty": Decimal(pattern[offset % 8])} for offset in range(56)]

    chart = plans.projection_chart({"days": days})

    width, height = chart["width"], chart["height"]
    right = float(chart["tick_label_right"])
    assert 0 <= right <= 100
    assert abs(right - (width - float(chart["tick_label_x"])) * 100 / width) <= 0.1
    assert any(tick["label"].startswith("-") for tick in chart["ticks"])
    for tick in chart["ticks"]:
        assert 0 <= float(tick["top"]) <= 100
        assert abs(float(tick["top"]) - float(tick["y"]) * 100 / height) <= 0.1
    assert [label["label"] for label in chart["labels"]] == ["11/9", "11/16", "11/23", "11/30", "12/7", "12/14", "12/21", "12/28"]
    for label in chart["labels"]:
        assert 0 <= float(label["left"]) <= 100
        assert abs(float(label["left"]) - float(label["x"]) * 100 / width) <= 0.1


@pytest.mark.django_db
def test_plan_detail_zero_only_chart_has_note(client_logged_in, user, cut, bend):
    """8 週ずっと 0 の見込みでは、図の前に 0 だと書いた 1 行を出し、図も残す (候補 C52 の 4)。今日の日付に左右されない作り物。"""
    customer = masters_services.create_partner({"code": "C003", "name": "作り物の得意先 3", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-400", "name": "作り物の棒", "unit": "個"}, user)
    # 在庫を数えない部品 (adjust を呼ばない = 0 から数える)・納期の無い明細・最後の工程が未計画の指示書 = どれも数えない
    sold = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("6"), "unit_price": Decimal("100"), "due_date": None}],
        user,
        order_date=START,
    )
    made = services.issue_workorder(
        sold.lines.get(),
        Decimal("6"),
        [{"process": cut, "planned_qty": Decimal("6")}, {"process": bend, "planned_qty": Decimal("6")}],
        user,
    )
    first = made.steps.get(process=cut)
    services.plan_step(first, START, START + timedelta(days=1), user)
    assert [day["qty"] for day in stock_services.stock_projection(item, 56)["days"]] == [0] * 56

    html = client_logged_in.get(_detail_url(first), HTTP_HX_REQUEST="true").content.decode()

    assert ZERO_NOTE in html
    assert 'id="plan-stock-chart"' in html


@pytest.mark.django_db
def test_stock_projection_by_day(user, cut, bend):
    customer = masters_services.create_partner({"code": "C002", "name": "作り物の得意先 2", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-300", "name": "作り物の板", "unit": "個"}, user)
    sold = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("8"), "unit_price": Decimal("100"), "due_date": START + timedelta(days=5)}],
        user,
        order_date=START,
    )
    made = services.issue_workorder(
        sold.lines.get(),
        Decimal("5"),
        [{"process": cut, "planned_qty": Decimal("5")}, {"process": bend, "planned_qty": Decimal("5")}],
        user,
    )
    last = made.steps.get(process=bend)
    services.plan_step(last, START + timedelta(days=2), START + timedelta(days=3), user)
    stock_services.adjust(item, Decimal("10"), START, user)

    projection = stock_services.stock_projection(item, 8, start=START)

    assert [day["date"] for day in projection["days"]] == [START + timedelta(days=offset) for offset in range(8)]
    assert [day["qty"] for day in projection["days"]] == [10, 10, 10, 15, 15, 7, 7, 7]
    assert projection["current"] == 10
    assert projection["counted"] is True
    assert (projection["workorders"], projection["workorder_qty"]) == (1, 5)
    assert (projection["lines"], projection["line_qty"]) == (1, 8)
    assert projection["undated"] == 0


@pytest.mark.django_db
def test_board_bar_and_card_open_plan_detail(client_logged_in, user, line, cut, step):
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 4), user)
    waiting = services.issue_workorder(line, 2, [{"process": cut, "planned_qty": 2}], user).steps.get()

    html = client_logged_in.get(reverse("workorders:plan_board", args=[cut.pk]) + "?start=2026-11-02").content.decode()

    bar = html[html.index(f'data-step="{step.pk}"') :]
    assert bar[: bar.index(">")].count(f'hx-get="{_detail_url(step)}"') == 1
    card = html[html.index('<li class="plan-card') :]
    assert f'data-step="{waiting.pk}"' in card[: card.index(">")]
    assert f'hx-get="{_detail_url(waiting)}"' in card[: card.index(">")]
    assert reverse("workorders:workorder_modal", args=[step.workorder.pk]) not in html
    for word in ("dragend", "stopImmediatePropagation"):
        assert word in html, word
