"""生産計画 (02 の 4 節)。工程の予定に日を置く関数 plan_step と、工程の「計画に使う」の印。"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.core import demo
from seizou.core import fields as company_fields
from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.workorders import services
from seizou.workorders.models import WorkOrderStep

ORDER_DATE = date(2026, 11, 2)
DUE_DATE = date(2026, 11, 20)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("planner", password="pass-for-tests-only")


@pytest.fixture
def order(user):
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    item = masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user
    )
    return orders_services.create_order(
        customer,
        [
            {
                "item": item,
                "qty": Decimal("10"),
                "unit_price": Decimal("100"),
                "due_date": DUE_DATE,
            }
        ],
        user,
        order_date=ORDER_DATE,
    )


@pytest.fixture
def line(order):
    return order.lines.get()


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="切断", sort_order=1, use_in_plan=True)


@pytest.fixture
def bend(db):
    return Process.objects.create(code="BEND", name="曲げ", sort_order=2)


@pytest.fixture
def workorder(user, line, cut, bend):
    return services.issue_workorder(
        line,
        Decimal("4"),
        [
            {"process": cut, "planned_qty": Decimal("4")},
            {"process": bend, "planned_qty": Decimal("4")},
        ],
        user,
    )


@pytest.fixture
def step(workorder, cut):
    return workorder.steps.get(process=cut)


def _dates(step):
    step.refresh_from_db()
    return step.planned_start, step.planned_end


# 1 段目: plan_step


@pytest.mark.django_db
def test_plan_step_sets_dates(user, step):
    assert _dates(step) == (None, None)
    result = services.plan_step(step, date(2026, 11, 5), date(2026, 11, 5), user)
    assert isinstance(result, WorkOrderStep)
    assert result.pk == step.pk
    assert _dates(step) == (date(2026, 11, 5), date(2026, 11, 5))


@pytest.mark.django_db
def test_plan_step_moves_dates(user, step):
    services.plan_step(step, date(2026, 11, 5), date(2026, 11, 6), user)
    services.plan_step(step, date(2026, 11, 9), date(2026, 11, 10), user)
    assert _dates(step) == (date(2026, 11, 9), date(2026, 11, 10))


@pytest.mark.django_db
def test_plan_step_clears_to_unplanned(user, step):
    services.plan_step(step, date(2026, 11, 5), date(2026, 11, 6), user)
    services.plan_step(step, None, None, user)
    assert _dates(step) == (None, None)


@pytest.mark.django_db
def test_plan_step_start_after_end_is_value_error(user, step):
    services.plan_step(step, date(2026, 11, 5), date(2026, 11, 6), user)
    with pytest.raises(ValueError) as caught:
        services.plan_step(step, date(2026, 11, 7), date(2026, 11, 6), user)
    assert str(caught.value) == (
        "開始日が終了日より後です。開始日を終了日と同じ日か前の日にしてください。"
    )
    assert _dates(step) == (date(2026, 11, 5), date(2026, 11, 6))


@pytest.mark.django_db
def test_plan_step_one_side_none_is_value_error(user, step):
    for start, end in ((date(2026, 11, 5), None), (None, date(2026, 11, 5))):
        with pytest.raises(ValueError) as caught:
            services.plan_step(step, start, end, user)
        assert str(caught.value) == "開始日と終了日は、両方を入れるか両方を空にしてください。"
    assert _dates(step) == (None, None)


@pytest.mark.django_db
def test_plan_step_cancelled_order_is_r05(user, order, step):
    services.plan_step(step, date(2026, 11, 5), date(2026, 11, 5), user)
    orders_services.cancel_order(order, user)
    with pytest.raises(RuleViolation) as caught:
        services.plan_step(step, date(2026, 11, 9), date(2026, 11, 9), user)
    assert caught.value.code == "R05"
    assert caught.value.message == (
        "取消した受注には、出荷も指示書も作れない。取消していない受注の指示書を選んでください。"
    )
    assert _dates(step) == (date(2026, 11, 5), date(2026, 11, 5))


@pytest.mark.django_db
def test_plan_step_does_not_touch_qty(user, workorder, step):
    services.plan_step(step, date(2026, 11, 5), date(2026, 11, 6), user)
    step.refresh_from_db()
    workorder.refresh_from_db()
    assert step.planned_qty == Decimal("4")
    assert workorder.qty == Decimal("4")


@pytest.mark.django_db
def test_create_process_accepts_use_in_plan(user):
    marked = masters_services.create_process(
        {"code": "PAINT", "name": "塗装", "sort_order": 3, "use_in_plan": True}, user
    )
    plain = masters_services.create_process({"code": "PACK", "name": "梱包"}, user)
    marked.refresh_from_db()
    plain.refresh_from_db()
    assert marked.use_in_plan is True
    assert plain.use_in_plan is False


# 2 段目: 生産計画の画面

WEEK = "?start=2026-11-02"


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


def _board(client, process, query=WEEK):
    return client.get(reverse("workorders:plan_board", args=[process.pk]) + query)


def _place(client, step, start, process, **extra):
    data = {
        "step": step.pk,
        "start": start,
        "process": process.pk,
        "board_start": "2026-11-02",
        "weeks": "1",
        **extra,
    }
    return client.post(reverse("workorders:plan_place"), data, HTTP_HX_REQUEST="true")


def _bar_pattern(step):
    return re.compile(rf'<a class="plan-bar[^>]*data-step="{step.pk}"')


def _card_pattern(step):
    return re.compile(rf'<li class="plan-card[^>]*data-step="{step.pk}"')


def _tooltip(html, step):
    """棒の箱 (棒と吹き出し) の HTML。棒の data-step から「未計画に戻す」のボタンの終わりまで。"""
    begin = html.index(f'data-step="{step.pk}"')
    return html[begin : html.index("未計画に戻す</button>", begin)]


@pytest.mark.django_db
def test_board_requires_login(client):
    response = client.get(reverse("workorders:plan_index"))
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


@pytest.mark.django_db
def test_board_shows_bar_and_unplanned(client_logged_in, user, line, cut, workorder, step):
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 4), user)
    second = services.issue_workorder(line, 2, [{"process": cut, "planned_qty": 2}], user)
    waiting = second.steps.get()

    response = _board(client_logged_in, cut)

    assert response.status_code == 200
    html = response.content.decode()
    assert _bar_pattern(step).search(html)
    assert workorder.wo_no in _tooltip(html, step)
    unplanned = html[html.index("<h2 class=\"text-lg font-bold\">未計画</h2>") :]
    assert '<span class="badge badge-neutral">1</span>' in unplanned
    assert _card_pattern(waiting).search(unplanned)
    assert second.wo_no in unplanned
    assert not _card_pattern(step).search(html)


@pytest.mark.django_db
def test_place_post_returns_board_partial(client_logged_in, cut, step):
    response = _place(client_logged_in, step, "2026-11-04", cut)

    assert response.status_code == 200
    html = response.content.decode()
    assert 'id="plan-board"' in html
    assert "<html" not in html
    assert _bar_pattern(step).search(html)
    assert _dates(step) == (date(2026, 11, 4), date(2026, 11, 4))


@pytest.mark.django_db
def test_place_requires_login_post_and_htmx(client, user, cut, step):
    url = reverse("workorders:plan_place")
    data = {"step": step.pk, "start": "2026-11-04", "process": cut.pk}

    anonymous = client.post(url, data, HTTP_HX_REQUEST="true")
    assert anonymous.status_code == 302
    assert reverse("core:login") in anonymous["Location"]

    client.force_login(user)
    assert client.get(url, HTTP_HX_REQUEST="true").status_code == 405
    assert client.post(url, data).status_code == 400
    assert _dates(step) == (None, None)


@pytest.mark.django_db
def test_move_post_keeps_length(client_logged_in, user, cut, step):
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 5), user)

    response = _place(client_logged_in, step, "2026-11-10", cut)

    assert response.status_code == 200
    assert _dates(step) == (date(2026, 11, 10), date(2026, 11, 11))


@pytest.mark.django_db
def test_unplan_post_clears_dates(client_logged_in, user, cut, step):
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 5), user)

    response = _place(client_logged_in, step, "", cut)

    assert response.status_code == 200
    assert _dates(step) == (None, None)
    html = response.content.decode()
    assert _card_pattern(step).search(html)
    assert not _bar_pattern(step).search(html)


@pytest.mark.django_db
def test_place_cancelled_order_shows_r05(client_logged_in, user, order, cut, step):
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 4), user)
    orders_services.cancel_order(order, user)

    response = _place(client_logged_in, step, "2026-11-06", cut)

    assert response.status_code == 200
    assert response["HX-Retarget"] == "#plan-notice"
    assert response["HX-Reswap"] == "innerHTML"
    html = response.content.decode()
    assert "alert-error" in html
    assert "R05" in html
    assert 'id="plan-board"' not in html
    assert _dates(step) == (date(2026, 11, 4), date(2026, 11, 4))


@pytest.mark.django_db
def test_place_bad_date_shows_form_error(client_logged_in, cut, step):
    response = _place(client_logged_in, step, "2026-13-40", cut)

    assert response.status_code == 200
    assert response["HX-Retarget"] == "#plan-notice"
    assert "alert-error" in response.content.decode()
    assert _dates(step) == (None, None)


@pytest.mark.django_db
def test_tooltip_has_neighbor_steps(client_logged_in, user, cut, bend, workorder, step):
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 4), user)

    tooltip = _tooltip(_board(client_logged_in, cut).content.decode(), step)
    assert "曲げ" in tooltip
    assert "未計画" in tooltip

    services.plan_step(workorder.steps.get(process=bend), date(2026, 11, 6), date(2026, 11, 7), user)
    tooltip = _tooltip(_board(client_logged_in, cut).content.decode(), step)
    assert "曲げ" in tooltip
    assert "11/6 → 11/7" in tooltip
    assert "11/20" in tooltip or "2026-11-20" in tooltip
    html = _board(client_logged_in, cut).content.decode()
    begin = html.index(f'data-step="{step.pk}"')
    assert "未計画に戻す" in html[begin : begin + 4000]
    assert 'hx-target="#plan-board"' in html
    assert 'hx-swap="outerHTML"' in html


@pytest.mark.django_db
def test_bar_opens_workorder_modal(client_logged_in, user, cut, workorder, step):
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 4), user)
    modal_url = reverse("workorders:workorder_modal", args=[workorder.pk])

    html = _board(client_logged_in, cut).content.decode()
    assert f'hx-get="{modal_url}"' in html

    response = client_logged_in.get(modal_url, HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    modal = response.content.decode()
    assert workorder.wo_no in modal
    assert "詳細を開く" in modal
    assert "工程の並び" in modal
    assert "切断" in modal
    assert "曲げ" in modal
    assert "11/4 → 11/4" in modal


@pytest.mark.django_db
def test_no_marked_process_shows_sentence(client_logged_in, bend):
    response = client_logged_in.get(reverse("workorders:plan_index"))

    assert response.status_code == 200
    html = response.content.decode()
    assert "工程の画面で「計画に使う」に印を付けると、ここに工程ごとの計画が並びます" in html
    assert "data-date" not in html
    assert 'id="plan-board"' not in html


@pytest.mark.django_db
def test_pill_for_each_marked_process(client_logged_in, cut, bend):
    paint = Process.objects.create(code="PAINT", name="塗装", sort_order=3, use_in_plan=True)

    html = client_logged_in.get(reverse("workorders:plan_index") + WEEK).content.decode()

    pills = html[html.index('aria-label="工程"') : html.index("</nav>", html.index('aria-label="工程"'))]
    assert "切断" in pills
    assert "塗装" in pills
    assert "曲げ" not in pills
    assert reverse("workorders:plan_board", args=[cut.pk]) in pills
    assert reverse("workorders:plan_board", args=[paint.pk]) in pills


@pytest.mark.django_db
def test_unmarked_process_board_is_404(client_logged_in, cut, bend):
    assert _board(client_logged_in, bend).status_code == 404
    missing = client_logged_in.get(reverse("workorders:plan_board", args=[cut.pk + bend.pk + 100]))
    assert missing.status_code == 404


@pytest.mark.django_db
def test_week_and_six_weeks(client_logged_in, cut):
    html = _board(client_logged_in, cut).content.decode()
    assert html.count('data-date="') == 7
    assert html.count('data-weekend="true"') == 2
    assert 'data-date="2026-11-02"' in html
    assert 'data-date="2026-11-08"' in html

    html = _board(client_logged_in, cut, WEEK + "&weeks=6").content.decode()
    assert html.count('data-date="') == 42
    assert html.count('data-weekend="true"') == 12


@pytest.mark.django_db
def test_today_marker(client_logged_in, cut):
    today = timezone.localdate().isoformat()

    html = _board(client_logged_in, cut, "").content.decode()

    assert html.count('aria-current="date"') == 1
    assert re.search(rf'data-date="{today}"[^>]*aria-current="date"', html)


@pytest.mark.django_db
def test_menu_has_plan_entry(client_logged_in):
    html = client_logged_in.get(reverse("core:home")).content.decode()
    assert "生産計画" in html
    assert f'href="{reverse("workorders:plan_index")}"' in html


# 2 段目: 作り物の種の印

COMPANIES_DIR = Path(__file__).resolve().parents[1] / "examples" / "companies"
PLAN_CODES = {"a_machining": {"TURN", "MILL", "GRIND"}, "b_plating": {"PLATE"}}


@pytest.mark.django_db
@pytest.mark.parametrize("company_id", sorted(PLAN_CODES))
def test_demo_seeds_mark_plan_processes(company_id, monkeypatch):
    # 試験用の settings には会社ごとの項目の登録が無い。ほかの試験の登録が残っていても空にする。
    monkeypatch.setattr(company_fields, "_REGISTRY", {})

    demo.load(COMPANIES_DIR / company_id)

    marked = set(Process.objects.filter(use_in_plan=True).values_list("code", flat=True))
    assert marked == PLAN_CODES[company_id]
    assert Process.objects.filter(use_in_plan=False).count() == 5 - len(PLAN_CODES[company_id])


# 2 段目: 会社の設定 production_plan (立ち上げの問 3)


def _menu_html(client):
    return client.get(reverse("core:home")).content.decode()


@pytest.mark.django_db
def test_production_plan_off_hides_menu_and_board(settings, client_logged_in, cut, step):
    settings.SEIZOU = {"production_plan": False}

    html = _menu_html(client_logged_in)
    assert "生産計画" not in html
    assert "指示書" in html
    assert reverse("workorders:plan_index") not in html
    assert client_logged_in.get(reverse("workorders:plan_index")).status_code == 404
    assert _board(client_logged_in, cut).status_code == 404
    assert _place(client_logged_in, step, "2026-11-04", cut).status_code == 404
    assert _dates(step) == (None, None)


@pytest.mark.django_db
def test_production_plan_on_shows_menu_and_board(settings, client_logged_in, cut):
    settings.SEIZOU = {"production_plan": True}

    html = _menu_html(client_logged_in)
    assert "生産計画" in html
    assert f'href="{reverse("workorders:plan_index")}"' in html
    assert _board(client_logged_in, cut).status_code == 200


@pytest.mark.django_db
def test_production_plan_default_is_true(settings, client_logged_in, cut):
    from seizou import cli
    from seizou.core import conf

    assert conf.DEFAULTS["production_plan"] is True
    assert cli.SETTING_DEFAULTS["production_plan"] is True
    settings.SEIZOU = {"order_form": "multi"}
    html = _menu_html(client_logged_in)
    assert "生産計画" in html
    assert _board(client_logged_in, cut).status_code == 200
