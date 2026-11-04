"""生産計画の盤の計画の詳細の小窓 (N113。決め 11-04 14:5x) の試験。

見る所: 小窓の HTML に指示書の要点・部品・受注の明細・工程の並び (押した工程を太字)・現品票と PDF・在庫の見込みの図があること、
在庫の見込みの日ごとの数 (stock.services.stock_projection)、盤の棒と未計画のカードが小窓を開く hx-get を持つこと。
見ない所 (理由): ドラッグの後の click を飲む script の動き・図の見た目 (ブラウザが要る。本体が画面で見る)。
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.stock import services as stock_services
from seizou.workorders import services
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
