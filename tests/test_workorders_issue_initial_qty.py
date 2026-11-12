"""指示書の発行の入力を受注の明細から開いた時の「数量」の初めの値 (依頼書 N441)。

受注の詳細の緑の「指示書を発行する」と明細の ⋮ は、入力を ?order_line=<pk> つきで開く。
その時だけ、数量の初めの値に「受注の明細の残 − 取消でない指示書の数量の合計」を入れる (0 以下なら空)。
得意先・品番・工程は作り物。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress import services as progress_services
from seizou.shipping import services as shipping_services
from seizou.workorders import services
from seizou.workorders.models import WorkOrder

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="切断", sort_order=1)


def _line(user, customer, item, qty="10"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


@pytest.fixture
def line(user, customer, item):
    return _line(user, customer, item)


def _issue(line, qty, cut, user):
    return services.issue_workorder(line, Decimal(qty), [{"process": cut, "planned_qty": Decimal(qty)}], user)


def _open(client, line=None, **headers):
    url = reverse("workorders:workorder_create")
    if line is not None:
        url += f"?order_line={line.pk}"
    response = client.get(url, **headers)
    assert response.status_code == 200
    return response


def _qty_input(response) -> str:
    """描いた「数量」の欄の value の字 (value が無ければ空の字)。"""
    match = re.search(r'<input[^>]*name="qty"[^>]*>', response.content.decode())
    assert match, "数量の欄が無い"
    value = re.search(r'value="([^"]*)"', match.group(0))
    return value.group(1) if value else ""


@pytest.mark.django_db
def test_line_without_workorder_opens_with_order_qty(client_logged_in, line):
    response = _open(client_logged_in, line)
    assert response.context["form"].initial["qty"] == Decimal("10")
    assert _qty_input(response) == "10"


@pytest.mark.django_db
def test_partly_shipped_line_opens_with_remaining(client_logged_in, user, line):
    shipping_services.ship(line, Decimal("3"), SHIPPED_AT, user)
    response = _open(client_logged_in, line)
    assert response.context["form"].initial["qty"] == Decimal("7")
    assert _qty_input(response) == "7"


@pytest.mark.django_db
def test_issued_workorders_are_subtracted_but_not_cancelled_ones(client_logged_in, user, line, cut):
    _issue(line, "3", cut, user)
    dropped = _issue(line, "4", cut, user)
    services.cancel_workorder(dropped, user, reason="作り物の取消")
    response = _open(client_logged_in, line)
    assert response.context["form"].initial["qty"] == Decimal("7")
    assert _qty_input(response) == "7"


@pytest.mark.django_db
def test_shipped_and_issued_are_both_subtracted(client_logged_in, user, line, cut):
    shipping_services.ship(line, Decimal("2"), SHIPPED_AT, user)
    _issue(line, "3", cut, user)
    response = _open(client_logged_in, line)
    assert response.context["form"].initial["qty"] == Decimal("5")


@pytest.mark.django_db
def test_lot_made_and_shipped_opens_with_the_rest(client_logged_in, user, line, cut):
    """5 の指示書を出して完成させ、その 5 を出荷した明細: 残りの 5 が入る (生産管理ボードの「指示書が要る」と同じ見方。本人の決め 11-13 06:5x)。

    前は、出荷と指示書の両方を引いて 0 になり、空で開いた (525 回目の本体の直しで、判断の式と同じ数え方にそろえた)。
    """
    workorder = _issue(line, "5", cut, user)
    progress_services.record_progress(workorder.steps.get(seq=1), Decimal("5"), timezone.now(), user, completes_step=True)
    shipping_services.ship(line, Decimal("5"), SHIPPED_AT, user)
    response = _open(client_logged_in, line)
    assert response.context["form"].initial["qty"] == Decimal("5")


@pytest.mark.django_db
def test_line_ready_to_ship_opens_empty(client_logged_in, user, line, cut):
    """10 を完成させて 4 を出荷した明細: 残りの 6 は完成していて出せるので、数量は空 (作る指示書は要らない)。"""
    workorder = _issue(line, "10", cut, user)
    progress_services.record_progress(workorder.steps.get(seq=1), Decimal("10"), timezone.now(), user, completes_step=True)
    shipping_services.ship(line, Decimal("4"), SHIPPED_AT, user)
    assert "qty" not in _open(client_logged_in, line).context["form"].initial


@pytest.mark.django_db
def test_fully_issued_line_opens_empty(client_logged_in, user, line, cut):
    _issue(line, "10", cut, user)
    response = _open(client_logged_in, line)
    assert "qty" not in response.context["form"].initial
    assert _qty_input(response) == ""


@pytest.mark.django_db
def test_over_issued_line_opens_empty(client_logged_in, user, line, cut):
    _issue(line, "12", cut, user)
    response = _open(client_logged_in, line)
    assert "qty" not in response.context["form"].initial
    assert _qty_input(response) == ""


@pytest.mark.django_db
def test_completed_line_opens_empty(client_logged_in, user, line):
    # 何も出荷せずに完了にした明細 (残は 0。RULES.md R25)
    orders_services.mark_completed(line, user)
    response = _open(client_logged_in, line)
    assert "qty" not in response.context["form"].initial
    assert _qty_input(response) == ""


@pytest.mark.django_db
def test_cancelled_order_line_opens_empty(client_logged_in, user, line):
    # 指示書の無い明細の受注を取り消す (残は数量のままでも入れない)
    orders_services.cancel_order(line.order, user)
    response = _open(client_logged_in, line)
    assert "qty" not in response.context["form"].initial
    assert _qty_input(response) == ""
    # 選んだ明細は今のまま欄に残る
    assert f'<option value="{line.pk}" selected>' in response.content.decode()


@pytest.mark.django_db
def test_open_without_order_line_stays_empty(client_logged_in, line):
    response = _open(client_logged_in)
    assert "qty" not in response.context["form"].initial
    assert _qty_input(response) == ""


@pytest.mark.django_db
def test_given_qty_is_not_replaced(client_logged_in, line):
    # GET に数量があれば、その字のまま (初めの値で上書きしない)
    url = reverse("workorders:workorder_create") + f"?order_line={line.pk}&qty=4"
    response = client_logged_in.get(url)
    assert response.context["form"].initial["qty"] == "4"
    assert _qty_input(response) == "4"


@pytest.mark.django_db
def test_rerender_after_error_keeps_typed_value(client_logged_in, line):
    # 工程を選ばず、部品に標準の工程の並びも無い → 赤の知らせで描き直す
    url = reverse("workorders:workorder_create") + f"?order_line={line.pk}"
    response = client_logged_in.post(url, {"order_line": line.pk, "qty": "2"})
    assert response.status_code == 200
    assert "工程がありません" in response.content.decode()
    assert response.context["form"]["qty"].value() == "2"
    assert _qty_input(response) == "2"
    assert WorkOrder.objects.count() == 0


@pytest.mark.django_db
def test_rerender_with_empty_qty_stays_empty(client_logged_in, line):
    url = reverse("workorders:workorder_create") + f"?order_line={line.pk}"
    response = client_logged_in.post(url, {"order_line": line.pk, "qty": ""})
    assert response.status_code == 200
    assert _qty_input(response) == ""
    assert WorkOrder.objects.count() == 0


@pytest.mark.django_db
def test_modal_opens_with_same_initial(client_logged_in, user, line, cut):
    _issue(line, "3", cut, user)
    response = _open(client_logged_in, line, **MODAL)
    assert "core/_form_modal.html" in [template.name for template in response.templates]
    assert response.context["form"].initial["qty"] == Decimal("7")
    assert _qty_input(response) == "7"


@pytest.mark.django_db
def test_save_with_initial_value_issues_that_qty(client_logged_in, user, customer, item, cut):
    big = _line(user, customer, item, qty="1200")
    response = _open(client_logged_in, big)
    typed = _qty_input(response)
    assert typed == "1,200"

    saved = client_logged_in.post(
        reverse("workorders:workorder_create"),
        {"order_line": big.pk, "qty": typed, "processes": [cut.pk]},
    )
    assert saved.status_code == 302
    workorder = WorkOrder.objects.get()
    assert workorder.qty == Decimal("1200")
    assert [step.planned_qty for step in workorder.steps.all()] == [Decimal("1200")]
