"""生産計画の盤の見た目 (02 の 4 節)。頭・凡例・3 段の日の見出し・段の見出し・横スクロール・吹き出しの置き方。

fixture は tests/test_workorders_plan.py の物を写した (既にあるファイルから import しない)。
"""

import re
from datetime import date, datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.workorders import plans, services

ORDER_DATE = date(2026, 11, 2)
DUE_DATE = date(2026, 11, 20)
WEEK = "?start=2026-11-02"
SIX_WEEKS = "?start=2026-10-26&weeks=6"


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


def _second_item_step(user, order, cut, part_no="T-200", name="作り物の歯車"):
    """別の部品の受注の指示書 (切断の 1 工程)。返す物は切断の予定。"""
    item = masters_services.create_item({"part_no": part_no, "name": name, "unit": "個"}, user)
    other = orders_services.create_order(
        order.customer,
        [{"item": item, "qty": Decimal("5"), "unit_price": Decimal("100"), "due_date": DUE_DATE}],
        user,
        order_date=ORDER_DATE,
    )
    issued = services.issue_workorder(
        other.lines.get(), Decimal("5"), [{"process": cut, "planned_qty": Decimal("5")}], user
    )
    return issued.steps.get()


def _legend(html):
    begin = html.index('aria-label="凡例"')
    return html[begin : html.index("</ul>", begin)]


def _tooltip(html, step):
    """棒の箱 (棒と吹き出し) の HTML。棒の data-step から「未計画に戻す」のボタンの終わりまで。"""
    begin = html.index(f'data-step="{step.pk}"')
    return html[begin : html.index("未計画に戻す</button>", begin)]


# 1 段目: 頭・凡例・3 段の日の見出し・段の見出し・横スクロール・吹き出しの置き方


@pytest.mark.django_db
def test_head_has_process_period_and_unplanned_button(client_logged_in, cut, step):
    html = _board(client_logged_in, cut).content.decode()

    assert '<h2 class="text-lg font-bold">切断</h2>' in html
    assert "11/2 – 11/8" in html
    assert html.count('href="#plan-unplanned"') == 1
    head_button = html[html.index('href="#plan-unplanned"') :]
    assert head_button.index('<span class="badge badge-sm badge-neutral">1</span>') < 200
    assert html.count('id="plan-unplanned"') == 1

    base = reverse("workorders:plan_board", args=[cut.pk])
    board_at = html.index('id="plan-board"')
    heading = html[html.index("<h1") : board_at]
    period = {
        "prev": f'href="{base}?start=2026-10-26"',
        "today": f'href="{base}"',
        "next": f'href="{base}?start=2026-11-09"',
        "week": f'href="{base}?start=2026-11-02"',
        "six_weeks": f'href="{base}?start=2026-11-02&amp;weeks=6"',
    }
    for name, href in period.items():
        assert href in html[board_at:], name
        # 「週」の link は今の工程の pill (題と盤の間の nav) と同じ URL なので、題の右の見張りから外す。
        if name != "week":
            assert href not in heading, name


@pytest.mark.django_db
def test_head_refreshes_on_place_post(client_logged_in, cut, step):
    response = _place(client_logged_in, step, "2026-11-04", cut)

    assert response.status_code == 200
    html = response.content.decode()
    base = reverse("workorders:plan_board", args=[cut.pk])
    assert f'href="{base}?start=2026-10-26"' in html
    assert f'href="{base}?start=2026-11-09"' in html
    assert 'href="#plan-unplanned"' in html
    assert '<span class="badge badge-sm badge-neutral">0</span>' in html


MONTH_BAND = re.compile(
    r'style="grid-column: (\d+) / span (\d+); grid-row: 1;">(\d+ 月)</div>'
)


@pytest.mark.django_db
def test_month_band_splits_at_month_change(client_logged_in, cut):
    html = _board(client_logged_in, cut, SIX_WEEKS).content.decode()
    assert MONTH_BAND.findall(html) == [
        ("2", "6", "10 月"),
        ("8", "30", "11 月"),
        ("38", "6", "12 月"),
    ]

    html = _board(client_logged_in, cut).content.decode()
    assert MONTH_BAND.findall(html) == [("2", "7", "11 月")]


@pytest.mark.django_db
def test_day_header_has_number_and_weekday_rows(client_logged_in, cut):
    html = _board(client_logged_in, cut).content.decode()

    numbers = re.findall(r'style="grid-column: (\d+); grid-row: 2;"><span[^>]*>(\d+)</span>', html)
    assert numbers == [(str(col), str(day)) for col, day in zip(range(2, 9), range(2, 9))]
    weekdays = re.findall(r'style="grid-column: \d+; grid-row: 3;">([^<]*)</div>', html)
    assert weekdays == list("月火水木金土日")
    assert html.count("grid-row: 2;") == 7
    assert html.count("grid-row: 3;") == 7
    assert html.count('data-date="') == 7
    assert html.count('data-weekend="true"') == 2


@pytest.mark.django_db
def test_today_number_is_marked(client_logged_in, cut):
    html = _board(client_logged_in, cut, "").content.decode()

    assert html.count('data-today="true"') == 1
    assert re.findall(r'data-today="true">(\d+)</span>', html) == [str(timezone.localdate().day)]
    assert html.count('aria-current="date"') == 1


ROW_HEAD = re.compile(
    r'<div class="sticky left-0[^"]*" style="grid-column: 1; grid-row: (\d+);" title="([^"]*)">([^<]*)</div>'
)


def _bar_box(html, step):
    found = re.search(
        r'data-bar-box style="grid-column: (\d+) / span (\d+); grid-row: (\d+);">\s*'
        rf'<a class="plan-bar[^>]*data-step="{step.pk}"',
        html,
    )
    return found.groups() if found else None


@pytest.mark.django_db
def test_row_heads_show_first_item_name(client_logged_in, user, order, cut, step):
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 4), user)
    gear = _second_item_step(user, order, cut)
    services.plan_step(gear, date(2026, 11, 4), date(2026, 11, 5), user)

    html = _board(client_logged_in, cut).content.decode()

    heads = ROW_HEAD.findall(html)
    assert heads == [
        ("4", "作り物の軸", "作り物の軸"),
        ("5", "作り物の歯車", "作り物の歯車"),
        ("6", "", ""),
    ]
    assert len(heads) == plans.build_board(cut, date(2026, 11, 2), 1)["rows"]
    for head in re.findall(r'<div class="sticky left-0[^>]*>', html):
        assert "data-date" not in head
    # 日の枠と棒の列は同じ日で同じ数 (見出しの 1 列の分を両方で足している)。
    frame = re.search(r'style="grid-column: (\d+); grid-row: 2 / -1;" data-date="2026-11-04"', html)
    assert frame.group(1) == "4"
    assert _bar_box(html, step) == ("4", "1", "4")
    assert _bar_box(html, gear) == ("4", "2", "5")


@pytest.mark.django_db
def test_board_scrolls_with_sticky_heads(client_logged_in, user, cut, step):
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 4), user)

    html = _board(client_logged_in, cut).content.decode()
    section = html[html.index('aria-label="日の計画"') : html.index('id="plan-unplanned"')]
    assert '<div class="overflow-x-auto">' in section
    rows = plans.build_board(cut, date(2026, 11, 2), 1)["rows"]
    assert section.count('class="sticky left-0') == rows + 1
    assert "grid-template-columns: 9rem repeat(7, minmax(2.75rem, 1fr))" in section

    html = _board(client_logged_in, cut, SIX_WEEKS).content.decode()
    assert "grid-template-columns: 9rem repeat(42, minmax(2.75rem, 1fr))" in html


@pytest.mark.django_db
def test_tooltip_is_fixed_and_script_places_it(client_logged_in, user, cut, step):
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 4), user)

    html = _board(client_logged_in, cut).content.decode()

    tooltip = _tooltip(html, step)
    classes = re.search(r'<div class="([^"]*)" role="tooltip">', tooltip).group(1).split()
    assert "fixed" in classes
    assert "absolute" not in classes
    for word in ("getBoundingClientRect", "data-bar-box", "focusin", "mouseover"):
        assert word in html, word
    begin = html.index(f'data-step="{step.pk}"')
    assert "未計画に戻す</button>" in html[begin : begin + 4000]


@pytest.mark.django_db
def test_legend_has_holiday_and_today(client_logged_in, cut):
    legend = _legend(_board(client_logged_in, cut).content.decode())
    assert "休日" in legend
    assert "今日" in legend


@pytest.mark.django_db
def test_drop_targets_unchanged(client_logged_in, cut):
    html = _board(client_logged_in, cut).content.decode()

    frames = re.findall(r'style="grid-column: (\d+); grid-row: 2 / -1;" data-date="', html)
    assert frames == [str(col) for col in range(2, 9)]
    heads = re.findall(r'<div class="([^"]*)" style="grid-column: \d+; grid-row: [23];"', html)
    assert len(heads) == 14
    assert all("pointer-events-none" in classes.split() for classes in heads)
    box = re.search(r'<div id="plan-board"[^>]*>', html).group(0)
    for attr in ("data-place-url=", "data-process=", "data-board-start=", "data-weeks="):
        assert attr in box, attr


# 2 段目: 棒 (3 行・部品ごとの塗り・状態の枠線・狭いときの 1 行) と凡例


def _bar_tag_and_body(html, step):
    """棒の <a> の開きの tag と中身。"""
    found = re.search(
        rf'(<a class="plan-bar[^>]*data-step="{step.pk}"[^>]*>)(.*?)</a>', html, re.DOTALL
    )
    assert found, step.pk
    return found.group(1), found.group(2)


def _bar_attr(html, step, name):
    tag, _ = _bar_tag_and_body(html, step)
    return re.search(rf'{name}="([^"]*)"', tag).group(1)


def _card_attr(html, step, name):
    tag = re.search(rf'<li class="plan-card[^>]*data-step="{step.pk}"[^>]*>', html).group(0)
    return re.search(rf'{name}="([^"]*)"', tag).group(1)


def _spans(body):
    return [text.strip() for text in re.findall(r"<span[^>]*>([^<]*)</span>", body)]


@pytest.mark.django_db
def test_bar_has_three_lines(client_logged_in, user, workorder, step):
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 4), user)

    html = _board(client_logged_in, step.process).content.decode()

    tag, body = _bar_tag_and_body(html, step)
    assert _spans(body) == [workorder.wo_no, "T-100 作り物の軸", "数量 4.00"]  # 予定の数は小数 2 桁で持ち、comma はそのまま出す
    assert "title=" not in tag


@pytest.mark.django_db
def test_bar_tone_follows_item_pk(client_logged_in, user, order, line, cut, step):
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 4), user)
    same_item = services.issue_workorder(line, 2, [{"process": cut, "planned_qty": 2}], user)
    gear = _second_item_step(user, order, cut)

    html = _board(client_logged_in, cut).content.decode()

    tone = _bar_attr(html, step, "data-tone")
    assert tone == plans.PART_TONES[line.item.pk % 6]
    assert tone == plans.part_tone(line.item.pk)
    assert _card_attr(html, same_item.steps.get(), "data-tone") == tone
    gear_item = gear.workorder.order_line.item
    assert gear_item.pk == line.item.pk + 1
    assert _card_attr(html, gear, "data-tone") == plans.part_tone(gear_item.pk)
    assert _card_attr(html, gear, "data-tone") != tone

    services.plan_step(gear, date(2026, 11, 5), date(2026, 11, 5), user)
    html = _board(client_logged_in, cut).content.decode()
    assert _bar_attr(html, gear, "data-tone") == plans.part_tone(gear_item.pk)


@pytest.mark.django_db
def test_bar_state_precedence(client_logged_in, user, order, cut, bend, workorder, step):
    from seizou.progress import services as progress_services

    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 4), user)
    html = _board(client_logged_in, cut).content.decode()
    assert _bar_attr(html, step, "data-state") == "planned"

    services.accept_workorder(workorder, user)
    html = _board(client_logged_in, cut).content.decode()
    assert _bar_attr(html, step, "data-state") == "accepted"

    # 納期 11/20 より後に置き直す (11/24 は 11/2 の週の外なので、11/23 の週の盤で見る)。
    services.plan_step(step, date(2026, 11, 24), date(2026, 11, 24), user)
    late_week = "?start=2026-11-23"
    html = _board(client_logged_in, cut, late_week).content.decode()
    assert _bar_attr(html, step, "data-state") == "late"

    # 最後の工程 (曲げ) の実績が予定の数に達すると、遅れていても「完了」。
    bend_step = workorder.steps.get(process=bend)
    recorded_at = timezone.make_aware(datetime(2026, 11, 5, 10, 30))
    progress_services.record_progress(bend_step, Decimal("3"), recorded_at, user)
    html = _board(client_logged_in, cut, late_week).content.decode()
    assert _bar_attr(html, step, "data-state") == "late"
    progress_services.record_progress(bend_step, Decimal("1"), recorded_at, user)
    html = _board(client_logged_in, cut, late_week).content.decode()
    assert _bar_attr(html, step, "data-state") == "done"

    # 納期の無い明細は、いつに置いても「遅れ」にならない。
    item = masters_services.create_item({"part_no": "T-300", "name": "作り物の板", "unit": "個"}, user)
    undated = orders_services.create_order(
        order.customer,
        [{"item": item, "qty": Decimal("3"), "unit_price": Decimal("100"), "due_date": None}],
        user,
        order_date=ORDER_DATE,
    )
    free = services.issue_workorder(
        undated.lines.get(), Decimal("3"), [{"process": cut, "planned_qty": Decimal("3")}], user
    ).steps.get()
    services.plan_step(free, date(2026, 11, 24), date(2026, 11, 24), user)
    html = _board(client_logged_in, cut, late_week).content.decode()
    assert _bar_attr(html, free, "data-state") == "planned"


@pytest.mark.django_db
def test_board_queries_do_not_grow_with_bars(client_logged_in, user, line, cut, step):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 4), user)
    _board(client_logged_in, cut)
    with CaptureQueriesContext(connection) as one_bar:
        assert _board(client_logged_in, cut).status_code == 200

    for _ in range(3):
        more = services.issue_workorder(line, 1, [{"process": cut, "planned_qty": 1}], user)
        services.plan_step(more.steps.get(), date(2026, 11, 4), date(2026, 11, 4), user)
    with CaptureQueriesContext(connection) as four_bars:
        html = _board(client_logged_in, cut).content.decode()
    assert html.count('<a class="plan-bar') == 4
    assert len(four_bars.captured_queries) == len(one_bar.captured_queries)


@pytest.mark.django_db
def test_compact_bar_in_six_weeks(client_logged_in, user, line, cut, workorder, step):
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 4), user)
    wide = services.issue_workorder(line, 2, [{"process": cut, "planned_qty": 2}], user).steps.get()
    services.plan_step(wide, date(2026, 11, 9), date(2026, 11, 10), user)

    html = _board(client_logged_in, cut, WEEK + "&weeks=6").content.decode()

    tag, body = _bar_tag_and_body(html, step)
    assert _spans(body) == [workorder.wo_no]
    assert "数量" not in body
    assert f'title="{workorder.wo_no} T-100 作り物の軸 数量 4.00 (計画)"' in tag
    wide_tag, wide_body = _bar_tag_and_body(html, wide)
    assert len(_spans(wide_body)) == 3
    assert "title=" not in wide_tag

    html = _board(client_logged_in, cut).content.decode()
    tag, body = _bar_tag_and_body(html, step)
    assert len(_spans(body)) == 3
    assert "title=" not in tag


@pytest.mark.django_db
def test_legend_lists_states_and_fill(client_logged_in, cut):
    legend = _legend(_board(client_logged_in, cut).content.decode())
    for word in ("計画", "着手", "完了", "遅れ", "部品の色", "休日", "今日"):
        assert word in legend, word


@pytest.mark.django_db
def test_tooltip_shows_state(client_logged_in, user, cut, step):
    services.plan_step(step, date(2026, 11, 4), date(2026, 11, 4), user)

    html = _board(client_logged_in, cut).content.decode()

    tooltip = _tooltip(html, step)
    assert '<dt class="text-base-content/70">状態</dt><dd>計画</dd>' in tooltip
    begin = html.index(f'data-step="{step.pk}"')
    assert "未計画に戻す</button>" in html[begin : begin + 4000]


@pytest.mark.django_db
def test_unplanned_card_has_tone_border(client_logged_in, cut, step):
    html = _board(client_logged_in, cut).content.decode()

    tag = re.search(rf'<li class="plan-card[^>]*data-step="{step.pk}"[^>]*>', html).group(0)
    assert "border-l-4" in tag.split('"')[1].split()
    assert "data-tone=" in tag
