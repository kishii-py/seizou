"""指示書の詳細の工程のタブの、協力企業に出す工程の行と「協力企業」のタブ (見比べのまとめの M01。N406b の 2 段目 = 画面と文書)。

協力企業に出す工程の行に「協力企業に出す」(発注の新規登録を ?step= で開く)、出した工程の行に取引先の名前と出ている数とまだ渡していない数、
終わっていない明細のある行に「実績を入力」の代わりの「戻りを受け入れる」(受入の新規登録を ?line= で小窓に開く) を出す。
行は workorders.plans.step_rows が作り、明細は 1 つの指示書の分を 1 回で読む (工程の数で問い合わせが増えない)。
Django の test client で HTML を見る。作り物のデータは tests/test_purchasing_subcontract.py の build_workorder と subcontract_po で作る。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.purchasing import services as purchasing_services
from seizou.workorders import plans
from seizou.workorders import services as workorders_services
from tests.test_purchasing_subcontract import build_workorder, subcontract_po

NO_WRITE = {"department": "off", "write": "on", "money": "off"}
DAY = date(2026, 11, 6)
DONE_BADGE = '<span class="badge badge-success">完了</span>'


@pytest.fixture
def wo(user):
    return build_workorder(user)


def _detail_url(workorder):
    return reverse("workorders:workorder_detail", args=[workorder.pk])


def _body(client, workorder):
    response = client.get(_detail_url(workorder))
    assert response.status_code == 200
    return response.content.decode()


def _step_items(body, workorder):
    """工程のタブの行 (<li>) を、工程の順ごとに {順: 行の HTML} で返す。"""
    items = re.findall(r'<li class="flex items-center justify-between gap-2 px-2 py-2.*?</li>', body, re.S)
    rows = {}
    for step in workorder.steps.select_related("process").order_by("seq"):
        found = [row for row in items if f'title="{step}">{step}</span>' in row]
        assert len(found) == 1, step
        rows[step.seq] = found[0]
    return rows


def _send_href(step):
    return f'href="{reverse("purchasing:purchaseorder_create")}?step={step.pk}&amp;next='


def _return_href(line):
    url = reverse("purchasing:receipt_create")
    return f'href="{url}?line={line.pk}&amp;next=', f'data-modal-form="{url}?line={line.pk}&amp;next='


# ---------------------------------------------------------------- (a) 「協力企業に出す」


@pytest.mark.django_db
def test_send_button_on_outsourced_step(logged_in, wo):
    rows = _step_items(_body(logged_in, wo.workorder), wo.workorder)
    assert "協力企業に出す" in rows[2]
    assert _send_href(wo.step) in rows[2]
    # 画面へ移る (発注の新規登録は表が広いので小窓にしない)
    assert "data-modal-form" not in rows[2].split("協力企業に出す", 1)[0].rsplit("<a ", 1)[1]
    # 協力企業に出さない工程の行には出ない。出していない工程の行は今のまま「実績を入力」
    assert "協力企業に出す" not in rows[1] and "協力企業に出す" not in rows[3]
    assert all("実績を入力" in row for row in rows.values())


@pytest.mark.django_db
def test_send_button_hidden_for_reader_and_cancelled_workorder(settings, logged_in, user, wo):
    settings.SEIZOU = {"access_control": dict(NO_WRITE)}
    assert "協力企業に出す" not in _step_items(_body(logged_in, wo.workorder), wo.workorder)[2]
    settings.SEIZOU = {}

    workorders_services.cancel_workorder(wo.workorder, user)
    assert "協力企業に出す" not in _step_items(_body(logged_in, wo.workorder), wo.workorder)[2]


@pytest.mark.django_db
def test_send_button_comes_back_after_cancelling_the_order(logged_in, user, wo):
    po, _line = subcontract_po(user, wo.subcontractor, wo.step)
    assert "協力企業に出す" not in _step_items(_body(logged_in, wo.workorder), wo.workorder)[2]
    purchasing_services.cancel_purchase_order(po, user)
    assert _send_href(wo.step) in _step_items(_body(logged_in, wo.workorder), wo.workorder)[2]


# ---------------------------------------------------------------- (b) 出した工程の行


@pytest.mark.django_db
def test_sent_step_shows_partner_counts_and_return_button(logged_in, user, wo):
    handed_po, handed = subcontract_po(user, wo.subcontractor, wo.step, qty="4")
    waiting_po, _waiting = subcontract_po(user, wo.subcontractor, wo.step, qty="6", handed_on=None)
    row = _step_items(_body(logged_in, wo.workorder), wo.workorder)[2]

    assert wo.subcontractor.name in row
    assert "出ている 4" in row
    assert "まだ渡していない 6" in row
    for po in (handed_po, waiting_po):
        assert f'href="{reverse("purchasing:purchaseorder_detail", args=[po.pk])}"' in row
    assert "戻りを受け入れる" in row
    href, modal = _return_href(handed)
    assert href in row and modal in row
    assert "実績を入力" not in row
    assert "協力企業に出す" not in row


@pytest.mark.django_db
def test_zero_counts_are_not_shown(logged_in, user, wo):
    subcontract_po(user, wo.subcontractor, wo.step, qty="4")
    row = _step_items(_body(logged_in, wo.workorder), wo.workorder)[2]
    assert "出ている 4" in row
    assert "まだ渡していない" not in row


@pytest.mark.django_db
def test_return_button_only_for_receipt_writer(settings, logged_in, user, wo):
    subcontract_po(user, wo.subcontractor, wo.step)
    settings.SEIZOU = {"access_control": dict(NO_WRITE)}
    row = _step_items(_body(logged_in, wo.workorder), wo.workorder)[2]
    assert "戻りを受け入れる" not in row
    assert wo.subcontractor.name in row and "出ている 10" in row


# ---------------------------------------------------------------- (c) 戻りを全部受け入れた行


@pytest.mark.django_db
def test_returned_step_goes_back_to_progress_button_with_done_badge(logged_in, user, wo):
    _po, line = subcontract_po(user, wo.subcontractor, wo.step)
    purchasing_services.receive(line, Decimal("10"), DAY, user)
    row = _step_items(_body(logged_in, wo.workorder), wo.workorder)[2]
    assert "戻りを受け入れる" not in row
    assert "実績を入力" in row
    assert DONE_BADGE in row
    assert "出ている" not in row and "まだ渡していない" not in row
    assert wo.subcontractor.name in row


@pytest.mark.django_db
def test_step_rows_values(user, wo):
    _po, handed = subcontract_po(user, wo.subcontractor, wo.step, qty="4")
    _po2, _waiting = subcontract_po(user, wo.subcontractor, wo.step, qty="6", handed_on=None)
    purchasing_services.receive(handed, Decimal("1"), DAY, user)
    rows = {row["step"].seq: row for row in plans.step_rows(wo.workorder)}

    assert rows[1]["subcontract"]["lines"] == [] and rows[1]["subcontract"]["can_send"] is False
    sub = rows[2]["subcontract"]
    assert sub["out_total"] == Decimal("3")
    assert sub["waiting_total"] == Decimal("6")
    assert sub["can_send"] is False
    assert [(entry["line_no"], entry["out"], entry["waiting"], entry["done"]) for entry in sub["lines"]] == [
        (1, Decimal("3"), Decimal("0"), False),
        (1, Decimal("0"), Decimal("6"), False),
    ]
    assert sub["lines"][0]["partner"] == wo.subcontractor.name
    assert sub["open_line"] == handed.pk


@pytest.mark.django_db
def test_step_rows_without_purchasing_part(monkeypatch, wo):
    """purchasing の部品が無い置き場 (指示書の property が AttributeError) では、協力企業の値は None。"""

    def missing(self):
        raise AttributeError("subcontract_lines")

    monkeypatch.setattr(type(wo.workorder), "subcontract_lines", property(missing))
    rows = plans.step_rows(wo.workorder)
    assert all(row["subcontract"] is None for row in rows)


# ---------------------------------------------------------------- (d) 「協力企業」のタブ


@pytest.mark.django_db
def test_subcontract_tab_lists_lines(logged_in, user, wo):
    po, line = subcontract_po(user, wo.subcontractor, wo.step)
    cancelled_po, _cancelled = subcontract_po(user, wo.subcontractor, wo.step, qty="2")
    purchasing_services.cancel_purchase_order(cancelled_po, user)
    body = _body(logged_in, wo.workorder)

    assert re.search(r'aria-label="協力企業">協力企業<span class="badge badge-sm tabular-nums">1</span>', body)
    text = f"{po.po_no}-1 {wo.item.part_no} {wo.item.name}"
    # 行の行き先: 発注の明細に詳細の画面が無いので、土台は行を link にしない (字だけ)
    assert f'<span class="block truncate px-2 py-2" title="{text}">{text}</span>' in body
    assert f"{cancelled_po.po_no}-1" not in body.split('aria-label="協力企業"', 1)[1]
    assert list(wo.workorder.subcontract_lines) == [line]


@pytest.mark.django_db
def test_subcontract_tab_empty(logged_in, wo):
    body = _body(logged_in, wo.workorder)
    assert re.search(r'aria-label="協力企業">協力企業<span class="badge badge-sm tabular-nums">0</span>', body)


# ---------------------------------------------------------------- (e) 問い合わせの数


def _issue(user, wo, processes):
    return workorders_services.issue_workorder(
        wo.order.lines.get(), Decimal("10"), [{"process": process, "planned_qty": Decimal("10")} for process in processes], user
    )


@pytest.mark.django_db
def test_query_count_does_not_grow_with_steps(logged_in, user, wo):
    cut, plate, check = wo.processes
    paint = masters_services.create_process({"code": "PAINTX", "name": "塗装", "is_outsourced": True}, user)
    heat = masters_services.create_process({"code": "HEATX", "name": "熱処理", "is_outsourced": True}, user)
    grind = masters_services.create_process({"code": "GRINDX", "name": "研磨"}, user)
    three = _issue(user, wo, (cut, plate, paint))
    six = _issue(user, wo, (cut, plate, grind, paint, heat, check))

    def fill(workorder):
        steps = [step for step in workorder.steps.select_related("process").order_by("seq") if step.process.is_outsourced]
        # 協力企業に出す工程のうち、最後の 1 つを出さずに残す (「協力企業に出す」の行)。ほかは渡した明細とまだ渡していない明細
        for step in steps[:-1]:
            subcontract_po(user, wo.subcontractor, step, qty="4")
            subcontract_po(user, wo.subcontractor, step, qty="6", handed_on=None)

    fill(three)
    fill(six)

    def count(workorder):
        url = _detail_url(workorder)
        assert logged_in.get(url).status_code == 200
        with CaptureQueriesContext(connection) as ctx:
            assert logged_in.get(url).status_code == 200
        return len(ctx.captured_queries)

    body = _body(logged_in, six)
    assert body.count("戻りを受け入れる") >= 2 and "協力企業に出す" in body
    assert count(three) == count(six)
