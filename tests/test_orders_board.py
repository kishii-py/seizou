"""注残の盤 (注文残の一覧。02 の 8 節)。判断の列の値 (orders.board.BoardRow)・納期の絞り込み・N+1 の無い読み方。

受注・出荷・指示書・実績・予定は services で作る。関数の試験は today を渡し、画面の試験は timezone.localdate() から数える。
作り物のデータだけを使う。
"""

import io
import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from seizou.masters import services as masters_services
from seizou.orders import board
from seizou.orders import services as orders_services
from seizou.orders.models import OrderLine
from seizou.progress import services as progress_services
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
TODAY = date(2026, 11, 10)

COLUMNS = [
    "受注番号",
    "得意先",
    "部品",
    "納期",
    "納期まで",
    "数量",
    "出荷済み",
    "検収",
    "残",
    "在庫",
    "指示書",
    "進捗",
    "計画の終了日",
    "判断",
    "受注日",
    "状態",
]


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
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def cut(user):
    return masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)


@pytest.fixture
def bend(user):
    return masters_services.create_process({"code": "BEND", "name": "曲げ", "sort_order": 2}, user)


def _line(user, customer, item, *, qty="10", due=None):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100"), "due_date": due}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


def _issue(user, line, *processes, qty="10"):
    return workorders_services.issue_workorder(
        line, Decimal(qty), [{"process": p, "planned_qty": Decimal(qty)} for p in processes], user
    )


def _record(user, step, qty):
    return progress_services.record_progress(step, Decimal(qty), timezone.now(), user)


def _fresh(line):
    """board は cached_property なので、指示書や実績を足した後は読み直す。"""
    return OrderLine.objects.get(pk=line.pk)


def _row_pks(response):
    return {row["obj"].pk for row in response.context["rows"]}


# --- BoardRow (関数の試験。today を渡す) ---


@pytest.mark.django_db
def test_board_row_values(user, customer, item, cut, bend):
    due = TODAY + timedelta(days=5)
    line = _line(user, customer, item, due=due)
    shipping_services.ship(line, Decimal("4"), ORDER_DATE, user)
    workorder = _issue(user, line, cut, bend)
    first, second = workorder.steps.order_by("seq")
    _record(user, first, "3")
    workorders_services.plan_step(first, TODAY, TODAY + timedelta(days=1), user)
    workorders_services.plan_step(second, TODAY + timedelta(days=2), TODAY + timedelta(days=3), user)
    workorders_services.accept_workorder(workorder, user)

    row = board.BoardRow(_fresh(line), TODAY)

    assert row.issued_total == Decimal("10")
    assert row.workorder_count == 1 and row.accepted_count == 1
    assert row.workorder_label == "10 受けた"
    assert row.progress_label == "切断 3/10"
    assert row.current_step == first
    assert row.steps == [first, second]
    assert row.completed_total == Decimal("0")
    assert row.plan_end == TODAY + timedelta(days=3)
    assert row.plan_late is False
    assert row.days_left == 3  # 11-10 (火) → 11-15 (日): 稼働日は 11・12・13 の 3 日 (土日を除く)
    assert row.is_overdue is False
    assert row.latest_workorder == workorder
    assert row.remaining == Decimal("6")
    assert row.judgement_code == "making" and row.judgement == "製造中"


@pytest.mark.django_db
def test_board_row_labels_follow_accepted_and_plan(user, customer, item, cut):
    due = TODAY + timedelta(days=1)
    line = _line(user, customer, item, due=due)
    accepted = _issue(user, line, cut, qty="4.5")
    waiting = _issue(user, line, cut, qty="5.5")
    workorders_services.accept_workorder(accepted, user)
    workorders_services.plan_step(waiting.steps.get(), TODAY, TODAY + timedelta(days=3), user)

    row = board.BoardRow(_fresh(line), TODAY)
    assert row.workorder_label == "10 一部受けた"
    assert row.plan_late is True
    assert row.latest_workorder == waiting

    # 取消の指示書は数えない。
    workorders_services.cancel_workorder(waiting, user)
    row = board.BoardRow(_fresh(line), TODAY)
    assert row.workorder_label == "4.5 受けた"
    assert row.plan_end is None

    other = _line(user, customer, item, due=due)
    _issue(user, other, cut, qty="2")
    assert board.BoardRow(_fresh(other), TODAY).workorder_label == "2 発行"


def _judgement_case(case, user, customer, item, cut):
    """規則ごとの明細 1 つ (数量 10)。"""
    future = TODAY + timedelta(days=7)
    past = TODAY - timedelta(days=1)
    if case == "cancelled":
        line = _line(user, customer, item, due=past)
        _issue(user, line, cut)
        orders_services.cancel_order(line.order, user)
    elif case == "done":
        line = _line(user, customer, item, due=past)
        shipping_services.ship(line, Decimal("10"), ORDER_DATE, user)
    elif case == "ship":
        # 納期が過ぎていても、完成していれば「出荷できる」。
        line = _line(user, customer, item, due=past)
        workorder = _issue(user, line, cut)
        _record(user, workorder.steps.get(), "10")
    elif case == "late":
        line = _line(user, customer, item, due=past)
        workorder = _issue(user, line, cut)
        _record(user, workorder.steps.get(), "3")
    elif case == "need_workorder":
        line = _line(user, customer, item, due=future)
        _issue(user, line, cut, qty="4")
    else:
        line = _line(user, customer, item, due=future)
        workorder = _issue(user, line, cut)
        _record(user, workorder.steps.get(), "3")
    return line


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("case", "label"),
    [
        ("cancelled", "取消"),
        ("done", "出荷済み"),
        ("ship", "出荷できる"),
        ("late", "遅れ"),
        ("need_workorder", "指示書が要る"),
        ("making", "製造中"),
    ],
)
def test_judgement_rules(case, label, user, customer, item, cut):
    line = _judgement_case(case, user, customer, item, cut)

    row = board.BoardRow(_fresh(line), TODAY)

    assert row.judgement_code == case
    assert row.judgement == label
    assert dict(board.JUDGEMENTS)[case] == label


@pytest.mark.django_db
def test_judgement_late_without_workorder_and_ship_needs_last_step(user, customer, item, cut, bend):
    # 指示書が無くても、納期が過ぎていれば「遅れ」(指示書が要るより先)。
    late = _line(user, customer, item, due=TODAY - timedelta(days=1))
    assert board.BoardRow(_fresh(late), TODAY).judgement_code == "late"
    # 最初の工程だけが終わっても完成ではない (完成の数は最後の工程の実績)。
    line = _line(user, customer, item, due=TODAY + timedelta(days=3))
    workorder = _issue(user, line, cut, bend)
    _record(user, workorder.steps.get(seq=1), "10")
    row = board.BoardRow(_fresh(line), TODAY)
    assert row.completed_total == Decimal("0")
    assert row.progress_label == "曲げ 0/10"
    assert row.judgement_code == "making"
    _record(user, workorder.steps.get(seq=2), "10")
    row = board.BoardRow(_fresh(line), TODAY)
    assert row.progress_label == "完了"
    assert row.current_step is None
    assert row.judgement_code == "ship"


@pytest.mark.django_db
def test_board_without_workorders_is_empty_not_error(user, customer, item):
    line = _line(user, customer, item)

    row = board.BoardRow(_fresh(line), TODAY)

    assert row.workorders == []
    assert row.workorder_label == "未"
    assert row.progress_label == ""
    assert row.plan_end is None
    assert row.plan_late is False
    assert row.latest_workorder is None
    assert row.current_step is None
    assert row.days_left is None and row.is_overdue is False
    assert row.judgement_code == "need_workorder"


def test_num_drops_trailing_zeros():
    assert board._num(Decimal("10.00")) == "10"
    assert board._num(Decimal("4.50")) == "4.5"
    assert board._num(Decimal("100")) == "100"
    assert board._num(6) == "6"
    assert board._num(Decimal("0.25")) == "0.25"


def test_filters_read_dates_and_overdue():
    assert board.filters({"due_from": "2026-11-01", "due_to": "2026-11-30", "overdue": "1"}) == {
        "due_from": date(2026, 11, 1),
        "due_to": date(2026, 11, 30),
        "overdue": True,
    }
    assert board.filters({"due_from": "abc", "due_to": "2026-1-5", "overdue": "yes"}) == {
        "due_from": None,
        "due_to": None,
        "overdue": False,
    }
    assert board.filters({}) == {"due_from": None, "due_to": None, "overdue": False}


# --- 画面 (注文残の一覧) ---


@pytest.mark.django_db
def test_list_columns_and_csv(user, logged_in, customer, item, cut, settings):
    settings.SEIZOU = {"weekly_holidays": []}  # 本物の今日から数えるので、曜日に左右されないよう定休日を無くす (稼働日 = 暦の日)
    today = timezone.localdate()
    late = _line(user, customer, item, due=today - timedelta(days=2))
    issued = _line(user, customer, item, due=today + timedelta(days=4))
    _issue(user, issued, cut, qty="4")

    response = logged_in.get(reverse("orders:orderline_list"))
    assert response.status_code == 200
    assert [col.label for col in response.context["columns"]] == COLUMNS
    cells = {row["obj"].pk: [cell["value"] for cell in row["cells"]] for row in response.context["rows"]}
    assert cells[late.pk][COLUMNS.index("納期まで")] == -2
    assert cells[late.pk][COLUMNS.index("指示書")] == "未"
    assert cells[late.pk][COLUMNS.index("判断")] == "遅れ"
    assert cells[issued.pk][COLUMNS.index("納期まで")] == 4
    assert cells[issued.pk][COLUMNS.index("指示書")] == "4 発行"
    assert cells[issued.pk][COLUMNS.index("進捗")] == "切断 0/4"
    assert cells[issued.pk][COLUMNS.index("判断")] == "指示書が要る"
    body = response.content.decode()
    for text in ("未", "遅れ", "4 発行", "指示書が要る", "切断 0/4"):
        assert text in body

    csv_body = logged_in.get(reverse("orders:orderline_list"), {"format": "csv"}).content.decode()
    header, *rows = csv_body.lstrip("﻿").splitlines()
    assert header == ",".join(COLUMNS)
    assert "操作" not in csv_body
    assert len(rows) == 2
    assert any(",-2," in row and ",遅れ," in row for row in rows)


@pytest.mark.django_db
def test_period_and_overdue_filters(user, logged_in, customer, item):
    today = timezone.localdate()
    yesterday = _line(user, customer, item, due=today - timedelta(days=1))
    same_day = _line(user, customer, item, due=today)
    next_week = _line(user, customer, item, due=today + timedelta(days=7))
    finished = _line(user, customer, item, due=today - timedelta(days=1))
    shipping_services.ship(finished, Decimal("10"), ORDER_DATE, user)

    url = reverse("orders:orderline_list")
    iso = today.isoformat()
    cases = [
        ({}, {yesterday, same_day, next_week}),
        ({"due_from": iso}, {same_day, next_week}),
        ({"due_to": iso}, {yesterday, same_day}),
        ({"due_from": iso, "due_to": iso}, {same_day}),
        ({"overdue": "1"}, {yesterday}),
        ({"due_from": "abc"}, {yesterday, same_day, next_week}),
        ({"due_to": "2026-1-5"}, {yesterday, same_day, next_week}),
        ({"all": "1", "overdue": "1"}, {yesterday, finished}),
        ({"all": "1", "due_from": iso}, {same_day, next_week}),
    ]
    for params, expected in cases:
        for headers in ({}, {"HTTP_HX_REQUEST": "true"}):
            response = logged_in.get(url, params, **headers)
            assert response.status_code == 200, params
            assert _row_pks(response) == {line.pk for line in expected}, (params, headers)


def _lines_with_workorders(user, customer, item, cut, bend, count):
    for number in range(count):
        line = _line(user, customer, item, due=timezone.localdate() + timedelta(days=number))
        workorder = _issue(user, line, cut, bend)
        _record(user, workorder.steps.get(seq=1), "2")
        workorders_services.plan_step(
            workorder.steps.get(seq=2), timezone.localdate(), timezone.localdate() + timedelta(days=1), user
        )
        if number % 2:
            workorders_services.accept_workorder(workorder, user)
        shipping_services.ship(line, Decimal("1"), ORDER_DATE, user)


def _count_queries(client):
    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("orders:orderline_list"))
    assert response.status_code == 200
    return len(queries), response


@pytest.mark.django_db
def test_query_count_does_not_grow_with_rows(user, logged_in, customer, item, cut, bend):
    _lines_with_workorders(user, customer, item, cut, bend, 3)
    logged_in.get(reverse("orders:orderline_list"))  # 最初の 1 回の控え (session など) を外す
    small, response = _count_queries(logged_in)
    assert len(response.context["rows"]) == 3

    _lines_with_workorders(user, customer, item, cut, bend, 9)
    large, response = _count_queries(logged_in)
    assert len(response.context["rows"]) == 12
    assert all(row["obj"].board.workorder_label.startswith("10 ") for row in response.context["rows"])

    assert small == large, (small, large)


@pytest.mark.django_db
def test_existing_entrances_still_present(user, logged_in, customer, item):
    line = _line(user, customer, item, due=timezone.localdate())
    shipping_services.ship(line, Decimal("4"), ORDER_DATE, user)

    body = logged_in.get(reverse("orders:orderline_list")).content.decode()

    assert f'href="{reverse("shipping:shipment_create")}?order_line={line.pk}"' in body
    assert f'href="{reverse("workorders:workorder_create")}?order_line={line.pk}"' in body


# --- 2 段目: 盤の行の部分 (印・合計・行のメニュー・絞り込みの欄) ---


def _tr(body, pk):
    """一覧の行 (<tr id="row-<pk>" ...>...</tr>) の HTML。"""
    match = re.search(rf'<tr id="row-{pk}".*?</tr>', body, re.S)
    assert match, f"row-{pk} が無い"
    return match.group(0)


def _cells(html):
    """<td> の字 (タグを外して前後の空白を落とす)。"""
    return [re.sub(r"<[^>]+>", "", cell).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", html, re.S)]


def _png():
    buffer = io.BytesIO()
    Image.new("1", (40, 30), 1).save(buffer, format="PNG")
    return SimpleUploadedFile("作り物の図面.png", buffer.getvalue(), content_type="image/png")


@pytest.mark.django_db
def test_row_menu_markup_and_links(settings, tmp_path, user, logged_in, customer, item, cut, bend):
    settings.MEDIA_ROOT = tmp_path
    drawn = masters_services.create_item({"part_no": "T-300", "name": "作り物の図面つき", "drawing": _png()}, user)
    line = _line(user, customer, item, due=timezone.localdate() + timedelta(days=3))
    shipping_services.ship(line, Decimal("4"), ORDER_DATE, user)
    workorder = _issue(user, line, cut, bend)
    first = workorder.steps.get(seq=1)
    _record(user, first, "3")
    other = _line(user, customer, drawn, due=timezone.localdate() + timedelta(days=5))

    body = logged_in.get(reverse("orders:orderline_list")).content.decode()

    tr_html = _tr(body, line.pk)
    assert re.match(rf'<tr id="row-{line.pk}" class="row-hover h-10" data-row-menu>', tr_html)
    assert "onclick=" not in tr_html.split(">", 1)[0]
    assert '<details class="dropdown dropdown-end row-menu">' in tr_html
    assert '<summary class="btn btn-ghost btn-xs" aria-label="行の操作" title="行の操作">⋮</summary>' in tr_html
    assert '<ul class="menu dropdown-content z-50 w-56 rounded-lg border border-base-300 bg-base-100 p-2 shadow-sm">' in tr_html
    hrefs = [
        f'href="{reverse("shipping:shipment_create")}?order_line={line.pk}"',
        f'href="{reverse("workorders:workorder_create")}?order_line={line.pk}"',
        f'href="{reverse("progress:progress_create")}?workorder_step={first.pk}"',
        f'href="{reverse("orders:order_detail", args=[line.order_id])}"',
        f'hx-get="{reverse("masters:item_modal", args=[item.pk])}"',
        f'href="{reverse("workorders:workorder_tag", args=[workorder.pk])}"',
        f'href="{reverse("purchasing:purchaseorder_create")}?from_line={line.pk}"',
    ]
    if apps.is_installed("seizou.attachments"):
        hrefs.append(f'href="{reverse("orders:order_detail", args=[line.order_id])}#attachments"')
    for href in hrefs:
        assert href in tr_html, href
    for word in ("出荷する", "指示書を発行する", "実績を入れる", "受注の詳細", "部品の小窓", "現品票", "発注する"):
        assert word in tr_html, word
    assert "item_drawing_viewer" not in tr_html
    assert reverse("masters:item_drawing_viewer", args=[item.pk]) not in tr_html
    assert ">図面<" not in re.sub(r"<svg.*?</svg>", "", tr_html, flags=re.S)

    other_html = _tr(body, other.pk)
    assert f'href="{reverse("masters:item_drawing_viewer", args=[drawn.pk])}"' in other_html
    # 指示書の無い明細には、実績と現品票の項目が無い。
    assert "workorder_step=" not in other_html
    assert "/tag/" not in other_html


def _finished_and_cancelled(user, customer, item):
    done = _line(user, customer, item, qty="2")
    shipping_services.ship(done, Decimal("2"), ORDER_DATE, user)
    cancelled = _line(user, customer, item)
    orders_services.cancel_order(cancelled.order, user)
    return done, cancelled


@pytest.mark.django_db
def test_finished_and_cancelled_rows_have_no_action_links(user, logged_in, customer, item, cut):
    done, cancelled = _finished_and_cancelled(user, customer, item)
    _issue(user, done, cut, qty="2")

    body = logged_in.get(reverse("orders:orderline_list"), {"all": "1"}).content.decode()

    for line in (done, cancelled):
        tr_html = _tr(body, line.pk)
        assert "?order_line=" not in tr_html
        assert "from_line=" not in tr_html
        assert "受注の詳細" in tr_html
        assert "部品の小窓" in tr_html
        assert f'hx-get="{reverse("masters:item_modal", args=[item.pk])}"' in tr_html
    # 出荷済みの明細の判断は「出荷済み」、取消は「取消」(薄い印)。
    assert '<span class="badge badge-ghost text-base-content/50">出荷済み</span>' in _tr(body, done.pk)
    assert '<span class="badge badge-ghost text-base-content/50">取消</span>' in _tr(body, cancelled.pk)


@pytest.mark.django_db
def test_issue_item_follows_write_permission(settings, client, user, customer, item):
    line = _line(user, customer, item)
    issue_href = f'href="{reverse("workorders:workorder_create")}?order_line={line.pk}"'
    ship_href = f'href="{reverse("shipping:shipment_create")}?order_line={line.pk}"'
    admin = get_user_model().objects.create_superuser("admin-tester", password="pass-for-tests-only")

    def row_for(person):
        client.force_login(person)
        return _tr(client.get(reverse("orders:orderline_list")).content.decode(), line.pk)

    # 既定 (log) では誰にでも出る。
    assert issue_href in row_for(user)

    settings.SEIZOU = {"access_control": {"department": "log", "write": "on", "money": "log"}}
    denied = row_for(user)
    assert issue_href not in denied
    assert "指示書を発行する" not in denied
    assert ship_href not in denied
    assert issue_href in row_for(admin)


@pytest.mark.django_db
def test_judgement_badges_and_marks(settings, user, logged_in, customer, item, cut):
    # 本物の今日に頼る試験なので、定休日を無しにして「納期まで」を暦の日と同じにする (曜日に左右されない)
    settings.SEIZOU = {"weekly_holidays": []}
    today = timezone.localdate()
    late = _line(user, customer, item, due=today - timedelta(days=3))
    ready = _line(user, customer, item, due=today + timedelta(days=2))
    _record(user, _issue(user, ready, cut).steps.get(), "10")
    needed = _line(user, customer, item, due=today + timedelta(days=4))
    planned = _line(user, customer, item, due=today + timedelta(days=1))
    step = _issue(user, planned, cut).steps.get()
    workorders_services.plan_step(step, today, today + timedelta(days=3), user)

    body = logged_in.get(reverse("orders:orderline_list")).content.decode()

    late_html = _tr(body, late.pk)
    assert '<span class="badge badge-soft badge-error">遅れ</span>' in late_html
    assert '<span class="text-error">-3</span>' in late_html
    assert '<span class="text-base-content/50">未</span>' in late_html
    assert '<span class="badge badge-soft badge-success">出荷できる</span>' in _tr(body, ready.pk)
    assert '<span class="badge badge-soft badge-warning">指示書が要る</span>' in _tr(body, needed.pk)
    planned_html = _tr(body, planned.pk)
    assert '<span class="badge badge-soft badge-primary">製造中</span>' in planned_html
    assert '<span class="badge badge-sm badge-soft badge-warning">納期より後</span>' in planned_html
    assert (today + timedelta(days=3)).isoformat() in planned_html
    assert "納期より後" not in _tr(body, needed.pk)


def _totals(html):
    tfoot = re.search(r"<tfoot>.*?</tfoot>", html, re.S)
    assert tfoot, "tfoot が無い"
    return _cells(tfoot.group(0))


def _dec(text):
    return Decimal(text.replace(",", ""))


@pytest.mark.django_db
def test_totals_row_follows_filter(user, logged_in, customer, item):
    plate = masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user)
    first = _line(user, customer, item, qty="10")
    shipping_services.ship(first, Decimal("4"), ORDER_DATE, user)
    _line(user, customer, item, qty="5")
    _line(user, customer, plate, qty="3")
    url = reverse("orders:orderline_list")

    cells = _totals(logged_in.get(url).content.decode())
    assert cells[0] == "合計"
    assert len(cells) == len(COLUMNS) + 1
    qty, shipped, remaining = (cells[COLUMNS.index(name)] for name in ("数量", "出荷済み", "残"))
    assert (_dec(qty), _dec(shipped), _dec(remaining)) == (Decimal("18"), Decimal("4"), Decimal("14"))

    filtered = _totals(logged_in.get(url, {"q": "T-200"}).content.decode())
    assert [_dec(filtered[COLUMNS.index(name)]) for name in ("数量", "出荷済み", "残")] == [3, 0, 3]

    partial = logged_in.get(url, {"q": "T-100"}, HTTP_HX_REQUEST="true").content.decode()
    assert "<html" not in partial
    assert [_dec(_totals(partial)[COLUMNS.index(name)]) for name in ("数量", "出荷済み", "残")] == [15, 4, 11]
    assert '<div id="list-meta"' in partial and 'hx-swap-oob="true"' in partial


@pytest.mark.django_db
def test_filter_form_and_hx_include(user, logged_in, customer, item):
    _line(user, customer, item, due=timezone.localdate() - timedelta(days=1))
    url = reverse("orders:orderline_list")

    body = logged_in.get(url).content.decode()
    assert body.count('id="board-filter"') == 1
    assert 'name="due_from"' in body and 'name="due_to"' in body and 'name="overdue"' in body
    assert 'hx-include="#orders-all, #board-filter"' in body
    assert 'hx-include="#list-q, #list-per, #list-meta"' in body
    assert "納期が過ぎた物だけ" in body
    assert 'name="overdue" value="1" class="checkbox checkbox-sm">' in body

    checked = logged_in.get(url, {"overdue": "1", "due_from": "2026-11-01"}).content.decode()
    assert 'name="overdue" value="1" class="checkbox checkbox-sm" checked>' in checked
    assert 'name="due_from" class="input input-sm" value="2026-11-01"' in checked
    everything = logged_in.get(url, {"all": "1"}).content.decode()
    form = re.search(r'<form id="board-filter".*?</form>', everything, re.S).group(0)
    assert '<input type="hidden" name="all" value="1">' in form


@pytest.mark.django_db
def test_order_no_links_to_detail(user, logged_in, customer, item):
    line = _line(user, customer, item)

    tr_html = _tr(logged_in.get(reverse("orders:orderline_list")).content.decode(), line.pk)

    first_td = re.search(r"<td[^>]*>.*?</td>", tr_html, re.S).group(0)
    assert f'href="{reverse("orders:order_detail", args=[line.order_id])}">{line.order.order_no}</a>' in first_td


def _heads(html):
    """thead の見出し: 字 (タグを外した物) → (th の開きの tag, th の中の HTML)。"""
    thead = re.search(r"<thead>.*?</thead>", html, re.S)
    assert thead, "thead が無い"
    heads = {}
    for opening, inner in re.findall(r"(<th[^>]*>)(.*?)</th>", thead.group(0), re.S):
        heads[re.sub(r"<[^>]+>", "", inner).strip()] = (opening, inner)
    return heads


@pytest.mark.django_db
def test_board_sorts_by_annotated_columns_only(user, logged_in, customer, item):
    shipped = _line(user, customer, item, qty="10")
    shipping_services.ship(shipped, Decimal("4"), ORDER_DATE, user)  # 出荷済み 4・残 6
    plain = _line(user, customer, item, qty="10")  # 出荷済み 0・残 10
    url = reverse("orders:orderline_list")

    def first_pk(sort):
        response = logged_in.get(url, {"sort": sort})
        assert response.context["sort"] == sort
        return response.context["rows"][0]["obj"].pk

    assert first_pk("-shipped_total") == shipped.pk
    assert first_pk("shipped_total") == plain.pk
    assert first_pk("-remaining_total") == plain.pk
    assert first_pk("remaining_total") == shipped.pk

    # board__ の Python の値は sort_key で並べる (納期まで = 納期の順)
    response = logged_in.get(url, {"sort": "board__days_left"})
    assert response.status_code == 200
    assert response.context["sort"] == "board__days_left"

    body = logged_in.get(url).content.decode()
    heads = _heads(body)
    for label in ("納期まで", "数量", "出荷済み", "検収", "残", "在庫"):
        assert "text-right" in heads[label][0], label
    for label in ("指示書", "判断"):
        assert "text-right" not in heads[label][0], label
    assert "sort=shipped_total" in heads["出荷済み"][1]
    assert "sort=remaining_total" in heads["残"][1]
    assert "sort=board__days_left" in heads["納期まで"][1]
    assert len(_totals(body)) == len(COLUMNS) + 1


@pytest.mark.django_db
def test_board_sorts_python_values_by_sort_key(user, logged_in, customer, item):
    late = _line(user, customer, item, due=date(2026, 11, 20))
    early = _line(user, customer, item, due=date(2026, 11, 10))
    url = reverse("orders:orderline_list")

    def order_by(sort):
        return logged_in.get(url, {"sort": sort}).context["paginator"].object_list.query.order_by

    # 納期まで = 納期の順 (表の項目の道)
    response = logged_in.get(url, {"sort": "board__days_left"})
    assert response.context["rows"][0]["obj"].pk == early.pk
    response = logged_in.get(url, {"sort": "-board__days_left"})
    assert response.context["rows"][0]["obj"].pk == late.pk
    assert order_by("board__days_left") == ("due_date", "pk")
    # 検収・在庫 = annotate の列 (tests/settings.py は PARTS の全部 = workorders と stock がある)
    assert order_by("board__stock_balance") == ("stock_balance", "pk")
    assert order_by("-board__acceptance_total") == ("-acceptance_total", "-pk")

    body = logged_in.get(url).content.decode()
    heads = _heads(body)
    for label, sort in (("納期まで", "board__days_left"), ("検収", "board__acceptance_total"), ("在庫", "board__stock_balance")):
        assert f"sort={sort}" in heads[label][1], label
    for label in ("指示書", "進捗", "計画の終了日", "判断"):
        assert "<a " not in heads[label][1], label
    assert len(_totals(body)) == len(COLUMNS) + 1
