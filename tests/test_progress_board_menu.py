"""進捗の盤の行の ⋮ と右クリック (本人の言葉 11-08 00:25「進捗右クリックで編集、削除はできた方がいい」。指示書の候補 12)。

盤の 1 行は指示書の工程 1 つ (実績の行でない) なので、項目は「実績を入力」(行のボタンと同じ小窓) と「実績を直す・消す」
(その指示書の番号で絞った実績の一覧へ移る link。移った先の行の ⋮ と右クリックに「編集」「削除」がある) の 2 つ。
fixture は tests/test_progress_board.py の issue・cut と _record・_row の形を写した (そのファイルは変えない)。
作り物の得意先・部品・受注・指示書だけを使う。相手への HTTP は呼ばない。
"""

import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress import services
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
HX = {"HTTP_HX_REQUEST": "true"}
#: 行の操作の ⋮ (core/static/js/seizou-row-menu.js の markup の約束)
MENU = '<details class="dropdown dropdown-end row-menu">'


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="切断", sort_order=1)


@pytest.fixture
def issue(user):
    """作り物の受注を 1 つ作り、その明細に工程の並びの指示書を発行する関数 (返す物は指示書)。"""
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)

    def _issue(*processes, planned=Decimal("10")):
        order = orders_services.create_order(
            customer,
            [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
            user,
            order_date=ORDER_DATE,
        )
        return workorders_services.issue_workorder(
            order.lines.get(),
            Decimal("10"),
            [{"process": process, "planned_qty": planned} for process in processes],
            user,
        )

    return _issue


def _record(step, qty, user=None):
    return services.record_progress(step, Decimal(str(qty)), RECORDED_AT, user)


def _body(response):
    assert response.status_code == 200
    return response.content.decode()


def _row(body, step):
    """盤の 1 行 (<tr id="progress-step-<pk>" から </tr> まで)。"""
    match = re.search(rf'<tr id="progress-step-{step.pk}".*?</tr>', body, re.S)
    assert match is not None, f"progress-step-{step.pk} の行がありません"
    return match.group(0)


def _menu(row):
    """⋮ の中の項目の (字 → href) と、data-modal-form のある項目の (字 → data-modal-form)。"""
    assert MENU in row
    menu = row[row.index(MENU) :]
    hrefs = {label: href for href, label in re.findall(r'<li><a[^>]*href="([^"]*)"[^>]*>.*?(実績を入力|実績を直す・消す)</a></li>', menu, re.S)}
    modals = {label: modal for modal, label in re.findall(r'<li><a[^>]*data-modal-form="([^"]*)"[^>]*>.*?(実績を入力|実績を直す・消す)</a></li>', menu, re.S)}
    return hrefs, modals


def _board(client, **params):
    return client.get(reverse("progress:progress_list"), params)


@pytest.mark.django_db
def test_board_row_has_menu(logged_in, issue, cut, user):
    workorder = issue(cut)
    step = workorder.steps.get()
    _record(step, 3, user)

    row = _row(_body(_board(logged_in)), step)

    assert re.match(rf'<tr id="progress-step-{step.pk}" class="h-10" data-row-menu', row)
    hrefs, modals = _menu(row)
    create = f"{reverse('progress:progress_create')}?workorder_step={step.pk}"
    assert hrefs["実績を入力"] == modals["実績を入力"] == create
    assert hrefs["実績を直す・消す"] == f"/progress/records/?q={workorder.wo_no}"
    assert "実績を直す・消す" not in modals  # 実績の一覧へは小窓にせず移る


@pytest.mark.django_db
def test_menu_link_reaches_record_edit_and_delete(logged_in, issue, cut, user):
    workorder = issue(cut)
    other = issue(cut)
    mine = _record(workorder.steps.get(), 3, user)
    theirs = _record(other.steps.get(), 5, user)
    href = _menu(_row(_body(_board(logged_in)), workorder.steps.get()))[0]["実績を直す・消す"]

    body = _body(logged_in.get(href))

    row = re.search(rf'<tr id="row-{mine.pk}"[^>]*>.*?</tr>', body, re.S)
    assert row is not None, "その指示書の実績の行が無い"
    assert f'<tr id="row-{theirs.pk}"' not in body  # ほかの指示書の実績は出ない
    assert f'href="{reverse("progress:progress_update", args=[mine.pk])}?next=' in row.group(0)
    assert f'href="{reverse("progress:progress_delete", args=[mine.pk])}?next=' in row.group(0)
    assert ">編集</a>" in row.group(0) and ">削除</a>" in row.group(0)


@pytest.mark.django_db
def test_board_number_cells_are_unchanged(logged_in, issue, cut, user):
    step = issue(cut).steps.get()
    _record(step, 3, user)
    _record(step, 4, user)

    row = _row(_body(_board(logged_in)), step)

    cells = re.findall(r'<td class="whitespace-nowrap text-right tabular-nums[^"]*"[^>]*>([^<]*)</td>', row)
    assert cells == ["3", "10", "7", "0"]
    assert len(re.findall(r"<td[^>]*>.*?</td>", row, re.S)) == 8


@pytest.mark.django_db
def test_poll_row_has_menu(logged_in, issue, cut):
    step = issue(cut).steps.get()
    before = _record(step, 3)
    _record(step, 4)

    row = _row(_body(logged_in.get(reverse("progress:progress_list"), {"after": before.pk}, **HX)), step)

    assert 'hx-swap-oob="true"' in row
    assert "data-row-menu" in row
    hrefs, _modals = _menu(row)
    assert set(hrefs) == {"実績を入力", "実績を直す・消す"}
