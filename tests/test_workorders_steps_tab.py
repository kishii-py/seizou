"""指示書の詳細の工程のタブ (初見テスト R5 の候補 C27 の 1。U58・rb23)。

各行に「実績の合計/予定の数」、実績が予定の数に達した工程に「完了」の印、まだ達していない最初の工程に太字と
aria-current="step" を出す。行は plans.step_rows が作り、実績の合計は 1 回の問い合わせで読む (工程の数で問い合わせが増えない)。
Django の test client で HTML を見る。名前と品番は作り物だけ。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress import services as progress_services
from seizou.workorders import plans, services

ORDER_DATE = date(2026, 11, 2)
DUE_DATE = date(2026, 11, 20)
DONE_BADGE = '<span class="badge badge-success">完了</span>'


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
def order(user, customer, item):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": DUE_DATE}],
        user,
        order_date=ORDER_DATE,
    )


@pytest.fixture
def line(order):
    return order.lines.get()


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="切断", sort_order=1)


@pytest.fixture
def bend(db):
    return Process.objects.create(code="BEND", name="曲げ", sort_order=2)


@pytest.fixture
def workorder(user, line, cut, bend):
    """工程 2 つ (1 = 切断、2 = 曲げ。予定の数 4 ずつ) の指示書。"""
    return services.issue_workorder(
        line,
        Decimal("4"),
        [{"process": cut, "planned_qty": Decimal("4")}, {"process": bend, "planned_qty": Decimal("4")}],
        user,
    )


def _detail_url(workorder):
    return reverse("workorders:workorder_detail", args=[workorder.pk])


def _step_items(body, workorder):
    """工程のタブの行 (<li>) を、工程ごとに {工程: 行の HTML} で返す。"""
    items = re.findall(r'<li class="flex items-center justify-between gap-2 px-2 py-2.*?</li>', body, re.S)
    rows = {}
    for step in workorder.steps.select_related("process").order_by("seq"):
        found = [row for row in items if f'title="{step}">{step}</span>' in row]
        assert len(found) == 1, step
        rows[step.process.name] = found[0]
    return rows


@pytest.mark.django_db
def test_done_step_and_current_step(logged_in, user, workorder):
    first, _second = workorder.steps.order_by("seq")
    progress_services.record_progress(first, Decimal("4"), timezone.now(), user)

    body = logged_in.get(_detail_url(workorder)).content.decode()
    rows = _step_items(body, workorder)

    assert DONE_BADGE in rows["切断"]
    assert "4/4" in rows["切断"]
    assert 'aria-current="step"' not in rows["切断"]
    assert "font-bold" not in rows["切断"]

    assert 'aria-current="step"' in rows["曲げ"]
    assert "font-bold" in rows["曲げ"]
    assert "0/4" in rows["曲げ"]
    assert "完了" not in rows["曲げ"]
    # 行の字は今のまま (指示書番号 順 工程名)
    assert f"{workorder.wo_no} 1 切断" in body


@pytest.mark.django_db
def test_no_progress_marks_first_step(logged_in, workorder):
    body = logged_in.get(_detail_url(workorder)).content.decode()
    rows = _step_items(body, workorder)

    assert 'aria-current="step"' in rows["切断"]
    assert "0/4" in rows["切断"]
    assert 'aria-current="step"' not in rows["曲げ"]
    assert all("完了" not in row for row in rows.values())


@pytest.mark.django_db
def test_all_steps_done(logged_in, user, workorder):
    for step in workorder.steps.order_by("seq"):
        progress_services.record_progress(step, Decimal("4"), timezone.now(), user)

    body = logged_in.get(_detail_url(workorder)).content.decode()
    rows = _step_items(body, workorder)

    assert all(DONE_BADGE in row for row in rows.values())
    assert all('aria-current="step"' not in row for row in rows.values())
    assert body.count('aria-current="step"') == 0


@pytest.mark.django_db
def test_query_count_does_not_grow_with_steps(logged_in, user, line, cut, bend):
    two = services.issue_workorder(
        line,
        Decimal("4"),
        [{"process": cut, "planned_qty": Decimal("4")}, {"process": bend, "planned_qty": Decimal("4")}],
        user,
    )
    paint = Process.objects.create(code="PAINT", name="塗装", sort_order=3)
    check = Process.objects.create(code="CHECK", name="検査", sort_order=4)
    four = services.issue_workorder(
        line,
        Decimal("4"),
        [
            {"process": cut, "planned_qty": Decimal("4")},
            {"process": bend, "planned_qty": Decimal("4")},
            {"process": paint, "planned_qty": Decimal("4")},
            {"process": check, "planned_qty": Decimal("4")},
        ],
        user,
    )

    def count(workorder):
        url = _detail_url(workorder)
        assert logged_in.get(url).status_code == 200
        with CaptureQueriesContext(connection) as ctx:
            assert logged_in.get(url).status_code == 200
        return len(ctx.captured_queries)

    assert count(two) == count(four)


@pytest.mark.django_db
def test_step_rows_without_progress_part(monkeypatch, workorder):
    """進捗の部品が無い置き場 (_recorded_totals が空の辞書) は、数と印を出さない。"""
    monkeypatch.setattr(plans, "_recorded_totals", lambda step_pks: {})

    rows = plans.step_rows(workorder)

    assert [row["step"].process.name for row in rows] == ["切断", "曲げ"]
    assert all(row["recorded"] is None for row in rows)
    assert not any(row["done"] or row["current"] for row in rows)


@pytest.mark.django_db
def test_progress_tab_unchanged(logged_in, user, workorder):
    first = workorder.steps.order_by("seq").first()
    progress_services.record_progress(first, Decimal("2"), timezone.now(), user)

    body = logged_in.get(_detail_url(workorder)).content.decode()

    assert ">進捗<" in body
    rows = _step_items(body, workorder)
    assert "2/4" in rows["切断"]
    assert 'aria-current="step"' in rows["切断"]
