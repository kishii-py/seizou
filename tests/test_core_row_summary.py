"""一覧の行の「要点を見る」(目の印) と受注の小窓 (受け渡しの seizou-部品の6案-20261107/指示書.md の 6。本人の決め 11-07 18:2x〜18:4x「5はmodalでいいかな」)。

受注の一覧と指示書の一覧の行の右に目の印の link (core/_row_summary_link.html) があり、押すと小窓 (#seizou-modal) に要点
(番号・相手・日付・状態・明細の先頭の数行・合計) と「詳細を見る」が出る。受注の小窓は OrderModalView (orders/_modal.html)、
指示書の小窓は今までの WorkOrderModalView (要点と工程の並び)。作り物のデータは services で作る。名前と番号は作り物。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.workorders import services as workorders_services

HTMX = {"HTTP_HX_REQUEST": "true"}
HEADER = '<th class="whitespace-nowrap font-medium">要点</th>'


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def cut(user):
    return masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)


@pytest.fixture
def order(user, customer, shaft):
    lines = [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": date(2026, 11, 20)}]
    return orders_services.create_order(customer, lines, user, order_date=date(2026, 11, 2), customer_order_no="PO-77")


@pytest.fixture
def workorder(user, order, cut):
    return workorders_services.issue_workorder(
        order.lines.get(), Decimal("10"), [{"process": cut, "planned_qty": Decimal("10")}], user
    )


def eye_links(html: str) -> list[tuple[str, str]]:
    """目の印の link の (href = 詳細, hx-get = 小窓) の並び。"""
    return re.findall(r'<a class="btn btn-ghost btn-xs" href="([^"]+)" hx-get="([^"]+)"[^>]*aria-label="要点を見る"', html)


def modal(client, order):
    return client.get(reverse("orders:order_modal", args=[order.pk]), **HTMX)


# ---- 一覧の行の目の印 ----


def test_order_list_rows_have_summary_link(logged_in, order):
    html = logged_in.get(reverse("orders:order_list")).content.decode()

    assert html.count(HEADER) == 1
    assert eye_links(html) == [(f"/orders/{order.pk}/", f"/orders/{order.pk}/modal/")]
    link = re.search(r'<a class="btn btn-ghost btn-xs"[^>]*aria-label="要点を見る"[^>]*>', html).group(0)
    assert 'hx-target="#seizou-modal-box"' in link and 'hx-swap="innerHTML"' in link and "showModal()" in link
    # 行の番号の link (詳細へ移る) はそのまま
    assert f'href="/orders/{order.pk}/" title="{order.order_no}"' in html
    # htmx の行の取り直しでも同じ
    assert eye_links(logged_in.get(reverse("orders:order_list"), **HTMX).content.decode()) == eye_links(html)


def test_workorder_list_rows_have_summary_link(logged_in, workorder):
    html = logged_in.get(reverse("workorders:workorder_list")).content.decode()

    assert html.count(HEADER) == 1
    assert eye_links(html) == [(f"/workorders/{workorder.pk}/", f"/workorders/{workorder.pk}/modal/")]
    # 指示書の小窓は今までの物 (要点と工程の並びと「詳細を見る」)
    html = logged_in.get(reverse("workorders:workorder_modal", args=[workorder.pk]), **HTMX).content.decode()
    assert workorder.wo_no in html and "工程の並び" in html and "切断" in html
    assert f'href="/workorders/{workorder.pk}/">詳細を見る' in html


def test_board_and_other_lists_are_unchanged(logged_in, order):
    html = logged_in.get(reverse("orders:orderline_list")).content.decode()

    assert HEADER not in html and eye_links(html) == []


# ---- 受注の小窓 ----


def test_order_modal_shows_summary_and_detail_link(logged_in, order):
    response = modal(logged_in, order)
    html = response.content.decode()

    assert response.status_code == 200 and "<html" not in html
    for word in (order.order_no, "作り物の得意先", "PO-77", "T-100", "作り物の軸", "2026-11-20", "10 個", "¥1,000"):
        assert word in html, word
    # 状態は題の横の印 (項目の「状態」は出さない)
    assert "未出荷</span>" in html and "<dt class=\"text-base-content/70\">状態</dt>" not in html
    assert 'data-order-total>¥1,000</span>' in html
    assert f'href="/orders/{order.pk}/">詳細を見る' in html
    assert html.count("btn-primary") == 1


def test_order_modal_lists_first_five_lines(logged_in, user, customer, shaft):
    lines = [{"item": shaft, "qty": Decimal(n), "unit_price": Decimal("10")} for n in range(1, 8)]
    order = orders_services.create_order(customer, lines, user, order_date=date(2026, 11, 2))

    html = modal(logged_in, order).content.decode()

    assert html.count("<tr>") == 5
    assert "先頭の 5 行を出しています (全 7 行)" in html
    assert 'data-order-total>¥280</span>' in html


def test_order_modal_hides_money_without_permission(settings, logged_in, order):
    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "on"}}

    html = modal(logged_in, order).content.decode()

    assert "¥" not in html and "data-order-total" not in html and "金額" not in html
    assert "T-100" in html and "10 個" in html


def test_order_modal_without_htmx_goes_to_detail(logged_in, order):
    response = logged_in.get(reverse("orders:order_modal", args=[order.pk]))

    assert response.status_code == 302 and response["Location"] == f"/orders/{order.pk}/"


def test_order_modal_guards(client, logged_in, order):
    assert logged_in.get("/orders/999999/modal/", **HTMX).status_code == 404

    client.logout()
    response = client.get(reverse("orders:order_modal", args=[order.pk]), **HTMX)
    assert response.status_code == 302 and "/login/" in response["Location"]


def test_urls_and_screen():
    from seizou.core.screens import all_screens

    assert reverse("orders:order_modal", args=[1]) == "/orders/1/modal/"
    screen = next(s for s in all_screens() if s.url_name == "orders:order_modal")
    assert screen.name == "受注の小窓" and "得意先・日付・状態" in screen.usecase
