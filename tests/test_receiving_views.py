"""入荷の画面 (一覧・詳細・新規登録の全体と小窓・?order_line= の初期値・受注の明細の候補の口・左のメニュー・部署の判定)。

Django の test client で HTML と JSON を見る。受注は services で作る。作り物のデータだけを使う。
"""

import json
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.accounts import services as accounts_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.receiving import services
from seizou.receiving.models import SOURCE_CONSIGNED, SOURCE_FREE, SOURCE_PAID, Allocation, Receiving

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def items(user):
    return [
        masters_services.create_item({"part_no": "W-100", "name": "作り物のワーク", "unit": "個"}, user),
        masters_services.create_item({"part_no": "M-200", "name": "作り物の支給材", "unit": "本"}, user),
    ]


def _line(user, customer, item, qty="10"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100")}],
        user,
        order_date=timezone.localdate(),
    )
    return order.lines.get()


def _receive(user, item, customer, qty, source=SOURCE_CONSIGNED, *, line=None, lot=""):
    data = {
        "item": item,
        "partner": customer,
        "source": source,
        "qty": Decimal(qty),
        "received_on": timezone.localdate(),
        "lot": lot,
    }
    return services.create_receiving(data, user, order_line=line)


def _post_data(item, qty, source=SOURCE_FREE, **more):
    return {"item": item.pk, "source": source, "qty": qty, "received_on": timezone.localdate().isoformat(), **more}


def test_menu_and_screens_open(logged_in, user, customer, items):
    body = logged_in.get(reverse("core:home")).content.decode()
    assert f'href="{reverse("receiving:receiving_list")}"' in body
    assert "入荷" in body

    receiving = _receive(user, items[0], customer, "5")
    for url in (
        reverse("receiving:receiving_list"),
        reverse("receiving:receiving_create"),
        reverse("receiving:receiving_detail", args=[receiving.pk]),
    ):
        assert logged_in.get(url).status_code == 200, url


def test_list_shows_rows_and_filters_by_source(logged_in, user, customer, items):
    _receive(user, items[0], customer, "5", SOURCE_CONSIGNED, lot="LOT-A1")
    _receive(user, items[1], customer, "3", SOURCE_PAID, lot="LOT-B2")

    body = logged_in.get(reverse("receiving:receiving_list")).content.decode()
    assert "LOT-A1" in body and "LOT-B2" in body
    assert "預かり" in body and "有償支給" in body
    # 土台が「新規登録」のボタンを出す (名前の約束 receiving_create)
    assert reverse("receiving:receiving_create") in body

    filtered = logged_in.get(reverse("receiving:receiving_list"), {"f_source": SOURCE_PAID}).content.decode()
    assert "LOT-B2" in filtered
    assert "LOT-A1" not in filtered

    searched = logged_in.get(reverse("receiving:receiving_list"), {"q": "M-200"}).content.decode()
    assert "LOT-B2" in searched
    assert "LOT-A1" not in searched


def test_detail_shows_allocation_tab(logged_in, user, customer, items):
    line = _line(user, customer, items[0], "10")
    receiving = _receive(user, items[0], customer, "4", line=line)

    body = logged_in.get(reverse("receiving:receiving_detail", args=[receiving.pk])).content.decode()

    assert "引当" in body
    assert str(receiving.allocations.get()) in body
    assert f"{line.order.order_no}-1" in body


def test_create_without_order_line_saves_and_goes_to_detail(logged_in, user, customer, items):
    response = logged_in.post(reverse("receiving:receiving_create"), _post_data(items[1], "12", SOURCE_FREE, partner=customer.pk, lot="L-9"))

    receiving = Receiving.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("receiving:receiving_detail", args=[receiving.pk])
    assert (receiving.item, receiving.partner, receiving.source, receiving.qty) == (items[1], customer, SOURCE_FREE, Decimal("12"))
    assert receiving.lot == "L-9"
    assert receiving.created_by == user
    assert Allocation.objects.count() == 0


def test_order_line_in_get_prefills_item_customer_rest_and_consigned(logged_in, user, customer, items):
    line = _line(user, customer, items[0], "10")
    _receive(user, items[0], customer, "4", line=line)

    response = logged_in.get(reverse("receiving:receiving_create"), {"order_line": line.pk})

    assert response.status_code == 200
    initial = response.context["form"].initial
    assert str(initial["order_line"]) == str(line.pk)
    assert initial["item"] == items[0].pk
    assert initial["partner"] == customer.pk
    assert initial["source"] == SOURCE_CONSIGNED
    assert initial["qty"] == Decimal("6")
    body = response.content.decode()
    assert f'<option value="{line.pk}" selected>' in body
    assert f'<option value="{SOURCE_CONSIGNED}" selected>' in body


def test_post_with_order_line_allocates(logged_in, user, customer, items):
    line = _line(user, customer, items[0], "10")

    response = logged_in.post(
        reverse("receiving:receiving_create"),
        _post_data(items[0], "10", SOURCE_CONSIGNED, partner=customer.pk, order_line=line.pk),
    )

    assert response.status_code == 302
    receiving = Receiving.objects.get()
    assert receiving.allocations.get().order_line == line
    assert services.receiving_state(line) == services.STATE_FULL


def test_zero_qty_shows_red_notice_and_keeps_values(logged_in, items):
    response = logged_in.post(reverse("receiving:receiving_create"), _post_data(items[0], "0", lot="L-KEEP"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "0 より大きい数" in body
    assert "L-KEEP" in body
    assert Receiving.objects.count() == 0


def test_create_in_modal(logged_in, items):
    page = logged_in.get(reverse("receiving:receiving_create"), **MODAL)
    assert page.status_code == 200
    assert "<html" not in page.content.decode()

    response = logged_in.post(reverse("receiving:receiving_create"), _post_data(items[0], "2"), **MODAL)

    assert response.status_code == 204
    assert "seizou:saved" in json.loads(response["HX-Trigger"])
    assert Receiving.objects.count() == 1


def test_orderline_select_excludes_cancelled_and_fully_allocated(logged_in, user, customer, items):
    open_line = _line(user, customer, items[0], "10")
    partial_line = _line(user, customer, items[0], "10")
    full_line = _line(user, customer, items[0], "10")
    cancelled_line = _line(user, customer, items[1], "5")
    _receive(user, items[0], customer, "3", line=partial_line)
    _receive(user, items[0], customer, "10", line=full_line)
    orders_services.cancel_order(cancelled_line.order, user)

    data = logged_in.get(reverse("receiving:orderline_select")).json()

    ids = {row["id"] for row in data["items"]}
    assert ids == {open_line.pk, partial_line.pk}

    searched = logged_in.get(reverse("receiving:orderline_select"), {"q": open_line.order.order_no}).json()
    assert [row["id"] for row in searched["items"]] == [open_line.pk]


def test_department_without_receiving_cannot_open(settings, client, db):
    settings.SEIZOU = {"access_control": {"department": "on", "write": "on", "money": "on"}}
    factory = accounts_services.create_department("作り物の受付", ["orders", "masters"], ["orders"], None)
    accounts_services.issue_account("uketsuke", "kumo-tests-2611", [factory], [], {}, None)
    client.force_login(get_user_model().objects.get(username="uketsuke"))

    body = client.get(reverse("orders:order_list")).content.decode()
    assert reverse("receiving:receiving_list") not in body
    assert client.get(reverse("receiving:receiving_list")).status_code == 403
    assert client.get(reverse("receiving:receiving_create")).status_code == 403


def test_department_with_receiving_sees_menu(settings, client, db):
    settings.SEIZOU = {"access_control": {"department": "on", "write": "on", "money": "on"}}
    factory = accounts_services.create_department("作り物の製造", ["workorders", "receiving", "masters"], ["workorders"], None)
    accounts_services.issue_account("genba", "kumo-tests-2611", [factory], [], {}, None)
    client.force_login(get_user_model().objects.get(username="genba"))

    body = client.get(reverse("receiving:receiving_list")).content.decode()
    assert f'href="{reverse("receiving:receiving_list")}"' in body


def test_received_on_input_has_year_limit():
    """入荷日の欄に年の上限 max="9999-12-31" が付き、class は今までどおり input w-full (C31 の 1 と同じ IsoDateInput。289 回目の要望。本体 11-07)。"""
    from seizou.receiving.forms import ReceivingForm

    tag = ReceivingForm.base_fields["received_on"].widget.render("received_on", None)
    assert tag.startswith('<input type="date" name="received_on"')
    assert 'max="9999-12-31"' in tag and 'class="input w-full"' in tag
