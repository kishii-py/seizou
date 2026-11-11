"""生産管理ボードの「進捗」の字の協力企業の数 (見比べのまとめの M01 の 3 段目。N406c)。

今の工程を協力企業に出していれば、「進捗」の字の後ろに出ている数 (・協力企業 n) とまだ渡していない数 (・渡す前 n) が付く。
数は purchasing.services.annotate_subcontract の値で、盤の 1 ページは工程の問い合わせに Subquery で足す (問い合わせを増やさない)。
prefetch_for を通らない道 (受注の詳細の段の帯) は、今の工程 1 つだけを同じ関数で 1 回読み、同じ字になる。

作り物のデータは services で作る (tests/test_purchasing_subcontract.py の build_workorder と subcontract_po)。
Django の test client で HTML を見る (本物のブラウザは使わない)。発注はつながりの無い協力企業で作り、相手に送らない。
"""

from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.orders import board
from seizou.orders import services as orders_services
from seizou.orders.models import OrderLine
from seizou.progress import services as progress_services
from seizou.purchasing import services as purchasing_services
from seizou.workorders import services as workorders_services
from tests.query_helpers import count_queries
from tests.test_purchasing_subcontract import DAY, build_workorder, line_of, subcontract_po

pytestmark = pytest.mark.django_db


def finish(user, step, qty="10"):
    """工程に完了の印の付いた実績を入れる (前の工程を終えて、次の工程を今の工程にする)。"""
    progress_services.record_progress(step, Decimal(qty), timezone.now(), user, completes_step=True)


def receive(user, line, qty):
    """協力企業からの戻りを受入で入れる (指示書の工程を指す明細なので、その工程の実績になる。R32)。"""
    return purchasing_services.receive(line_of(line.pk), Decimal(qty), DAY, user)


def board_progress(client):
    """生産管理ボード (prefetch_for を通る道) の「進捗」の枡の字を、受注の明細の pk ごとに返す。"""
    response = client.get(reverse("orders:orderline_list"))
    assert response.status_code == 200
    index = [col.label for col in response.context["columns"]].index("進捗")
    return {row["obj"].pk: row["cells"][index]["value"] for row in response.context["rows"]}


def plain_progress(line):
    """prefetch_for を通らない道 (受注の詳細の段の帯と同じ。取り直した明細の OrderLine.board) の「進捗」の字。"""
    return OrderLine.objects.get(pk=line.pk).board.progress_label


def both_progress(client, line):
    """2 つの道の字が同じことを確かめて、その字を返す。"""
    on_board = board_progress(client)[line.pk]
    assert plain_progress(line) == on_board
    return on_board


class _WithoutPurchasing:
    """purchasing の部品が入っていない会社の apps の代わり (is_installed だけを替え、ほかは本物に任せる)。"""

    def __getattr__(self, name):
        return getattr(django_apps, name)

    def is_installed(self, app_name):
        return app_name != "seizou.purchasing" and django_apps.is_installed(app_name)


def internal_line(user, wo):
    """協力企業に出す工程の無い指示書 (切断だけ) の受注の明細。"""
    order = orders_services.create_order(
        wo.customer,
        [{"item": wo.item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=wo.order.order_date,
    )
    line = order.lines.get()
    workorders_services.issue_workorder(line, Decimal("10"), [{"process": wo.processes[0], "planned_qty": Decimal("10")}], user)
    return line


# ---------------------------------------------------------------- (a) 「進捗」の字


def test_progress_label_shows_out_and_waiting_of_current_step(user, logged_in):
    wo = build_workorder(user)
    line = wo.order.lines.get()
    other = internal_line(user, wo)
    finish(user, wo.steps[0])
    # 協力企業に出していない行の字は今のまま (今の工程がめっきでも、発注の明細が無ければ数を足さない)
    assert both_progress(logged_in, line) == "めっき 0/10"
    assert both_progress(logged_in, other) == "切断 0/10"

    _po, po_line = subcontract_po(user, wo.subcontractor, wo.step)
    assert both_progress(logged_in, line) == "めっき 0/10・協力企業 10"
    # 受入を 2 回に分けて、工程に実績を 2 件作る (実績の Sum と協力企業の数の Subquery が同じ問い合わせで正しい値になる)
    receive(user, po_line, "2")
    receive(user, po_line, "2")
    assert both_progress(logged_in, line) == "めっき 4/10・協力企業 6"
    assert both_progress(logged_in, other) == "切断 0/10"

    # 渡した日を空にすると、出ている数でなく、まだ渡していない数になる
    purchasing_services.set_handed_on(line_of(po_line.pk), None, user)
    assert both_progress(logged_in, line) == "めっき 4/10・渡す前 6"

    # 両方あれば、協力企業 → 渡す前の順
    purchasing_services.set_handed_on(line_of(po_line.pk), DAY, user)
    _po2, waiting_line = subcontract_po(user, wo.subcontractor, wo.step, "2", handed_on=None)
    assert both_progress(logged_in, line) == "めっき 4/10・協力企業 6・渡す前 2"

    # 受入で明細が終わると、工程に完了の印が付いて今の工程が次 (検査) へ進み、協力企業の数は出ない
    receive(user, po_line, "6")
    assert both_progress(logged_in, line) == "検査 0/10"
    assert purchasing_services.waiting_qty(line_of(waiting_line.pk)) == Decimal("2")


def test_progress_label_without_purchasing_part_is_unchanged(user, monkeypatch):
    wo = build_workorder(user)
    line = wo.order.lines.get()
    finish(user, wo.steps[0])
    _po, po_line = subcontract_po(user, wo.subcontractor, wo.step)
    receive(user, po_line, "4")
    assert plain_progress(line) == "めっき 4/10・協力企業 6"

    # 協力企業の部品を使わない会社: prefetch_for の道も、通らない道も、今までの字のまま
    monkeypatch.setattr(board, "apps", _WithoutPurchasing())
    rows = board.prefetch_for(OrderLine.objects.filter(pk=line.pk))
    assert [row.board.progress_label for row in rows] == ["めっき 4/10"]
    assert plain_progress(line) == "めっき 4/10"


def test_done_and_empty_labels_are_unchanged(user, logged_in):
    wo = build_workorder(user)
    line = wo.order.lines.get()
    _po, po_line = subcontract_po(user, wo.subcontractor, wo.step)
    for step in wo.steps:
        finish(user, step)
    # 指示書が全部完了なら「完了」のまま (出した明細が残っていても数を足さない)
    assert both_progress(logged_in, line) == "完了"
    assert purchasing_services.out_qty(line_of(po_line.pk)) == Decimal("10")


def test_csv_progress_column_has_the_same_label(user, logged_in):
    wo = build_workorder(user)
    finish(user, wo.steps[0])
    _po, po_line = subcontract_po(user, wo.subcontractor, wo.step)
    receive(user, po_line, "4")
    csv_body = logged_in.get(reverse("orders:orderline_list"), {"format": "csv"}).content.decode()
    header, *rows = csv_body.lstrip("﻿").splitlines()
    index = header.split(",").index("進捗")
    assert [row.split(",")[index] for row in rows] == ["めっき 4/10・協力企業 6"]
    body = logged_in.get(reverse("orders:orderline_list")).content.decode()
    assert '<td class="whitespace-nowrap">めっき 4/10・協力企業 6</td>' in body


# ---------------------------------------------------------------- (b) 問い合わせの数


def subcontracted_rows(user, codes):
    """codes の数だけ、切断を終えてめっきを協力企業に出し (渡した日あり)、2 を受け入れた受注の明細を作る。"""
    lines = []
    for code in codes:
        wo = build_workorder(user, code=code)
        finish(user, wo.steps[0])
        _po, po_line = subcontract_po(user, wo.subcontractor, wo.step)
        receive(user, po_line, "2")
        lines.append(wo.order.lines.get())
    return lines


def test_board_query_count_does_not_grow_with_subcontracted_rows(user, logged_in):
    plain = []
    for code in ("1", "2", "3"):
        wo = build_workorder(user, code=code)
        finish(user, wo.steps[0])
        plain.append(wo)
    logged_in.get(reverse("orders:orderline_list"))  # 最初の 1 回の控え (session など) を外す
    without_po, response = count_queries(logged_in)
    assert len(response.context["rows"]) == 3

    for wo in plain:
        _po, po_line = subcontract_po(user, wo.subcontractor, wo.step)
        receive(user, po_line, "2")
    small, response = count_queries(logged_in)
    assert {row["obj"].board.progress_label for row in response.context["rows"]} == {"めっき 2/10・協力企業 8"}

    subcontracted_rows(user, ("4", "5", "6"))
    large, response = count_queries(logged_in)
    assert len(response.context["rows"]) == 6
    assert {row["obj"].board.progress_label for row in response.context["rows"]} == {"めっき 2/10・協力企業 8"}

    assert without_po == small == large, (without_po, small, large)


def test_plain_path_reads_the_current_step_once(user, monkeypatch):
    (line,) = subcontracted_rows(user, ("1",))

    def counted():
        fresh = OrderLine.objects.get(pk=line.pk)
        with CaptureQueriesContext(connection) as captured:
            label = fresh.board.progress_label
        return label, len(captured.captured_queries)

    with_part, with_count = counted()
    monkeypatch.setattr(board, "apps", _WithoutPurchasing())
    without_part, without_count = counted()
    assert (with_part, without_part) == ("めっき 2/10・協力企業 8", "めっき 2/10")
    # prefetch_for を通らない道は、今の工程 1 つだけを 1 回読む (工程の数や明細の数で増えない)
    assert with_count == without_count + 1
