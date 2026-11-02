"""受注の画面 (02 の受注。core の土台の上)。Django の test client で HTML と表の状態を見る。

ブラウザでの確かめ (選ぶ欄・行を足す JS) は朝の E2E。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import Order, OrderLine
from seizou.shipping import services as shipping_services

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)


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
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def plate(user):
    return masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user)


@pytest.fixture
def shipped_order(user, customer, shaft):
    """数量 10 のうち 4 を出荷した明細 1 行の受注。"""
    order = services.create_order(
        customer,
        [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    shipping_services.ship(order.lines.get(), Decimal("4"), SHIPPED_AT, user)
    return order


def _management(total, initial):
    return {
        "lines-TOTAL_FORMS": str(total),
        "lines-INITIAL_FORMS": str(initial),
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
    }


def _update_post(order, qty, **extra):
    """明細 1 行の受注を直す POST の値 (数量だけを qty に変える)。"""
    line = order.lines.get()
    data = {
        "customer": order.customer_id,
        "order_date": order.order_date.isoformat(),
        "customer_order_no": order.customer_order_no,
        "note": order.note,
        **_management(1, 1),
        "lines-0-order_line": line.pk,
        "lines-0-item": line.item_id,
        "lines-0-qty": qty,
        "lines-0-unit": line.unit,
        "lines-0-unit_price": str(line.unit_price),
        "lines-0-due_date": "",
        "lines-0-note": "",
    }
    data.update(extra)
    return data


def _row_objects(response):
    return [row["obj"] for row in response.context["rows"]]


@pytest.mark.django_db
def test_order_list_requires_login(client):
    for name in ("orders:order_list", "orders:orderline_list", "orders:order_create"):
        response = client.get(reverse(name))
        assert response.status_code == 302
        assert reverse("core:login") in response["Location"]


@pytest.mark.django_db
def test_create_order_with_two_lines_shows_in_list(logged_in, customer, shaft, plate):
    form_page = logged_in.get(reverse("orders:order_create"))
    assert form_page.status_code == 200
    body = form_page.content.decode()
    assert 'name="note"' not in body  # 入れる画面に備考の欄を置かない (create_order に note が無い)
    assert f'data-select-url="{reverse("masters:partner_select")}"' in body
    assert f'data-select-url="{reverse("masters:item_select")}"' in body
    assert 'id="order-line-template"' in body and "行を足す" in body

    response = logged_in.post(
        reverse("orders:order_create"),
        {
            "customer": customer.pk,
            "order_date": "2026-11-02",
            "customer_order_no": "PO-0001",
            **_management(3, 0),
            "lines-0-item": shaft.pk,
            "lines-0-qty": "10",
            "lines-0-unit": "",
            "lines-0-unit_price": "100",
            "lines-0-due_date": "2026-11-20",
            "lines-0-note": "作り物の備考",
            "lines-1-item": plate.pk,
            "lines-1-qty": "3",
            "lines-1-unit": "箱",
            "lines-1-unit_price": "250.5",
            # 3 行目は空の行 (lines に入れない)
        },
    )
    order = Order.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("orders:order_detail", args=[order.pk])
    assert order.customer == customer
    assert order.customer_order_no == "PO-0001"
    first, second = order.lines.order_by("line_no")
    assert (first.item, first.qty, first.unit, first.amount) == (shaft, Decimal("10"), "個", Decimal("1000"))
    assert first.due_date == date(2026, 11, 20)
    assert first.note == "作り物の備考"
    assert (second.item, second.qty, second.unit) == (plate, Decimal("3"), "箱")

    detail = logged_in.get(response["Location"])
    body = detail.content.decode()
    assert order.order_no in body and "明細" in body and "作り物の板" in body
    assert reverse("orders:order_cancel", args=[order.pk]) in body

    listing = logged_in.get(reverse("orders:order_list"))
    assert _row_objects(listing) == [order]
    assert order.order_no in listing.content.decode()


@pytest.mark.django_db
def test_order_form_single_mode_has_one_line(settings, logged_in, customer, shaft):
    settings.SEIZOU = {"order_form": "single"}
    response = logged_in.get(reverse("orders:order_create"))
    form = response.context["form"]
    assert len(form.lines.forms) == 1
    body = response.content.decode()
    assert 'name="lines-0-item"' in body and 'name="lines-1-item"' not in body
    assert "行を足す" not in body and "order-line-template" not in body

    posted = logged_in.post(
        reverse("orders:order_create"),
        {
            "customer": customer.pk,
            "order_date": "2026-11-02",
            **_management(1, 0),
            "lines-0-item": shaft.pk,
            "lines-0-qty": "2",
            "lines-0-unit_price": "100",
        },
    )
    order = Order.objects.get()
    assert posted.status_code == 302
    edit = logged_in.get(reverse("orders:order_update", args=[order.pk]))
    assert len(edit.context["form"].lines.forms) == 1

    # multi (既定) のときは、直す画面にも足す行が 1 つある。
    settings.SEIZOU = {"order_form": "multi"}
    edit = logged_in.get(reverse("orders:order_update", args=[order.pk]))
    assert len(edit.context["form"].lines.forms) == 2


@pytest.mark.django_db
def test_update_below_shipped_stop_is_red(settings, logged_in, shipped_order):
    settings.SEIZOU = {"over_shipment": "stop"}
    response = logged_in.post(reverse("orders:order_update", args=[shipped_order.pk]), _update_post(shipped_order, "3"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "alert-error" in body and "R04" in body
    assert "このまま保存" not in body
    assert shipped_order.lines.get().qty == Decimal("10")


@pytest.mark.django_db
def test_update_below_shipped_warn_then_confirm(settings, logged_in, shipped_order):
    settings.SEIZOU = {"over_shipment": "warn"}
    url = reverse("orders:order_update", args=[shipped_order.pk])
    first = logged_in.post(url, _update_post(shipped_order, "3"))
    assert first.status_code == 200
    body = first.content.decode()
    assert "alert-warning" in body and "R04" in body and "このまま保存" in body
    assert 'name="confirmed" value="1"' in body
    assert shipped_order.lines.get().qty == Decimal("10")

    second = logged_in.post(url, _update_post(shipped_order, "3", confirmed="1"))
    assert second.status_code == 302
    assert second["Location"] == reverse("orders:order_detail", args=[shipped_order.pk])
    line = shipped_order.lines.get()
    assert line.qty == Decimal("3")
    assert line.amount == Decimal("300")
    shipped_order.refresh_from_db()
    assert shipped_order.status == Order.STATUS_SHIPPED  # 出荷済み 4 >= 数量 3


@pytest.mark.django_db
def test_removing_shipped_line_on_screen_is_red(logged_in, shipped_order):
    data = _update_post(shipped_order, "10", **{"lines-0-DELETE": "on"})
    response = logged_in.post(reverse("orders:order_update", args=[shipped_order.pk]), data)
    assert response.status_code == 200
    body = response.content.decode()
    assert "alert-error" in body and "出荷のある明細は消せません" in body
    assert shipped_order.lines.count() == 1


@pytest.mark.django_db
def test_cancel_then_update_is_r05(logged_in, shipped_order):
    form_page = logged_in.get(reverse("orders:order_cancel", args=[shipped_order.pk]))
    assert form_page.status_code == 200 and "取消の理由" in form_page.content.decode()

    response = logged_in.post(reverse("orders:order_cancel", args=[shipped_order.pk]), {"reason": "作り物の理由"})
    assert response.status_code == 302
    assert response["Location"] == reverse("orders:order_detail", args=[shipped_order.pk])
    shipped_order.refresh_from_db()
    assert shipped_order.status == Order.STATUS_CANCELLED
    assert shipped_order.note == "取消の理由: 作り物の理由"
    detail = logged_in.get(response["Location"]).content.decode()
    assert reverse("orders:order_cancel", args=[shipped_order.pk]) not in detail

    again = logged_in.post(reverse("orders:order_cancel", args=[shipped_order.pk]), {"reason": ""})
    assert again.status_code == 200
    assert "alert-error" in again.content.decode() and "R05" in again.content.decode()

    update = logged_in.post(reverse("orders:order_update", args=[shipped_order.pk]), _update_post(shipped_order, "12"))
    assert update.status_code == 200
    body = update.content.decode()
    assert "alert-error" in body and "R05" in body
    assert shipped_order.lines.get().qty == Decimal("10")


@pytest.mark.django_db
def test_list_default_hides_shipped_and_cancelled(user, logged_in, customer, shaft, shipped_order):
    def make(qty):
        return services.create_order(
            customer, [{"item": shaft, "qty": Decimal(qty), "unit_price": Decimal("100")}], user, order_date=ORDER_DATE
        )

    open_order = make("5")
    done = make("2")
    shipping_services.ship(done.lines.get(), Decimal("2"), SHIPPED_AT, user)
    cancelled = make("1")
    services.cancel_order(cancelled, user)

    default = logged_in.get(reverse("orders:order_list"))
    assert set(_row_objects(default)) == {open_order, shipped_order}  # 受注と一部出荷
    assert reverse("orders:orderline_list") in default.content.decode()

    everything = logged_in.get(reverse("orders:order_list"), {"all": "1"})
    assert set(_row_objects(everything)) == {open_order, shipped_order, done, cancelled}
    assert 'hx-include="#orders-all"' in everything.content.decode()

    # 検索 (htmx の行の部分) でも all=1 を保つ。
    rows = logged_in.get(reverse("orders:order_list"), {"all": "1", "q": done.order_no}, HTTP_HX_REQUEST="true")
    assert _row_objects(rows) == [done]


@pytest.mark.django_db
def test_orderline_list_shows_remaining(user, logged_in, customer, plate, shipped_order):
    full = services.create_order(
        customer, [{"item": plate, "qty": Decimal("2"), "unit_price": Decimal("100")}], user, order_date=ORDER_DATE
    )
    shipping_services.ship(full.lines.get(), Decimal("2"), SHIPPED_AT, user)

    response = logged_in.get(reverse("orders:orderline_list"))
    assert response.status_code == 200
    rows = _row_objects(response)
    assert rows == [shipped_order.lines.get()]
    assert rows[0].shipped_total == Decimal("4")
    assert rows[0].remaining_total == Decimal("6")
    body = response.content.decode()
    assert "注文残" in body and "出荷済み" in body and "一部出荷" in body
    # 残の数の字は DB で桁が違う (SQLite は 6、PostgreSQL は 6.00)。
    assert 'title="6"' in body or 'title="6.00"' in body
    assert reverse("masters:item_modal", args=[shipped_order.lines.get().item_id]) in body

    everything = logged_in.get(reverse("orders:orderline_list"), {"all": "1"})
    assert set(_row_objects(everything)) == set(OrderLine.objects.all())


@pytest.mark.django_db
def test_hidden_fields_are_removed_except_rule_fields(settings, logged_in, shipped_order):
    settings.SEIZOU = {"hidden_fields": ["customer_order_no", "due_date", "customer", "qty"]}
    response = logged_in.get(reverse("orders:order_update", args=[shipped_order.pk]))
    form = response.context["form"]
    assert "customer_order_no" not in form.fields
    assert "customer" in form.fields  # 決まりが見る欄は外さない
    row = form.lines.forms[0]
    assert "due_date" not in row.fields
    assert "qty" in row.fields and "item" in row.fields and "unit_price" in row.fields


@pytest.mark.django_db
def test_menu_has_order_entry(logged_in):
    response = logged_in.get(reverse("core:home"))
    body = response.content.decode()
    assert reverse("orders:order_list") in body
    assert "受注" in body
