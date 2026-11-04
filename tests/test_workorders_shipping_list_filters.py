"""指示書・出荷・納品書の一覧の絞り (N119。N117 の土台 BaseListView.filters の宣言だけ。本人の決め 11-04 15:0x)。

指示書の工程の絞り (f_process) は逆向きの FK なので、同じ工程を 2 回持つ指示書も 1 回だけ出る。
古い GET の process (製造向けの別の form) は、N128 で読まなくなった (絞りは f_process だけ)。名前は作り物だけ。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from seizou.shipping.models import DeliveryNote, Shipment
from seizou.workorders import services as workorders_services
from seizou.workorders.models import WorkOrder


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _customer(user, code, name):
    return masters_services.create_partner({"code": code, "name": name, "role": "customer"}, user)


def _line(user, customer, item, qty="50"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100")}],
        user,
        order_date=date(2026, 11, 2),
    )
    return order.lines.get()


@pytest.fixture
def workorders(user, item):
    """指示書 3 つ。1 つ目は 切断 → 切断 (同じ工程が 2 回)、2 つ目は 曲げ、3 つ目は 切断 → 曲げ。発行日は 11-01・11-02・11-03。"""
    cut = masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)
    bend = masters_services.create_process({"code": "BEND", "name": "曲げ", "sort_order": 2}, user)
    line = _line(user, _customer(user, "C001", "作り物の得意先"), item)

    def issue(*processes):
        steps = [{"process": process, "planned_qty": Decimal("2")} for process in processes]
        return workorders_services.issue_workorder(line, Decimal("2"), steps, user)

    first, second, third = issue(cut, cut), issue(bend), issue(cut, bend)
    for workorder, day in ((first, 1), (second, 2), (third, 3)):
        WorkOrder.objects.filter(pk=workorder.pk).update(issued_on=date(2026, 11, day))
    return {"first": first, "second": second, "third": third, "cut": cut, "bend": bend}


def listed(response) -> list:
    return [row["obj"].pk for row in response.context["rows"]]


@pytest.mark.django_db
def test_workorder_list_has_four_filters_and_process_lists_each_workorder_once(client_logged_in, user, item, workorders):
    first, second, third, cut = workorders["first"], workorders["second"], workorders["third"], workorders["cut"]
    url = reverse("workorders:workorder_list")

    body = client_logged_in.get(url).content.decode()
    for name in ("f_status", "f_process", "f_item", "f_issued_from", "f_issued_to"):
        assert f'name="{name}"' in body
    for label in ("状態", "工程", "部品", "発行日"):
        assert f">{label}</label>" in body
    # 名前の無い欄が出ない。製造向けの別の form は無い (N128 で絞り込みの枠の中へ移した)。
    assert 'for="list-f-"' not in body
    assert 'id="workorder-filter"' not in body
    assert f'<option value="{cut.pk}">{cut.name}</option>' in body  # 工程の候補の字は名前だけ (N142)
    assert f'<option value="{item.pk}">{item}</option>' in body

    response = client_logged_in.get(url, {"f_process": str(cut.pk)})
    assert listed(response) == [third.pk, first.pk]
    assert response.context["paginator"].count == 2

    workorders_services.cancel_workorder(second, user)
    response = client_logged_in.get(url, {"f_status": WorkOrder.STATUS_CANCELLED})
    assert listed(response) == [second.pk]
    response = client_logged_in.get(url, {"f_issued_from": "2026-11-02", "f_item": str(item.pk)})
    assert listed(response) == [third.pk, second.pk]


@pytest.mark.django_db
def test_old_process_get_is_not_read(client_logged_in, workorders):
    first, second, third = workorders["first"], workorders["second"], workorders["third"]
    cut, bend = workorders["cut"], workorders["bend"]
    url = reverse("workorders:workorder_list")

    # 古い GET の process は読まない (N128。別名を足さない)。絞らずに 3 つとも出る。
    assert listed(client_logged_in.get(url, {"process": cut.pk})) == [third.pk, second.pk, first.pk]
    assert listed(client_logged_in.get(url, {"process": bend.pk, "f_process": ""})) == [third.pk, second.pk, first.pk]
    # 両方あれば f_process だけで絞る (古い process とかけ合わせない)。
    both = client_logged_in.get(url, {"process": bend.pk, "f_process": str(cut.pk)})
    assert listed(both) == [third.pk, first.pk]
    assert both.context["paginator"].count == 2


@pytest.mark.django_db
def test_shipment_list_filters_customer_and_shipped_date(client_logged_in, user, item):
    north = _customer(user, "C001", "作り物の北の得意先")
    south = _customer(user, "C002", "作り物の南の得意先")
    north_line, south_line = _line(user, north, item), _line(user, south, item)
    early = shipping_services.ship(north_line, Decimal("5"), date(2026, 11, 3), user)
    middle = shipping_services.ship(south_line, Decimal("5"), date(2026, 11, 4), user)
    late = shipping_services.ship(north_line, Decimal("5"), date(2026, 11, 5), user)
    url = reverse("shipping:shipment_list")

    body = client_logged_in.get(url).content.decode()
    for name in ("f_customer", "f_shipped_from", "f_shipped_to"):
        assert f'name="{name}"' in body
    assert f'<option value="{north.pk}">{north}</option>' in body

    assert listed(client_logged_in.get(url, {"f_customer": str(north.pk)})) == [late.pk, early.pk]
    assert listed(client_logged_in.get(url, {"f_shipped_from": "2026-11-04", "f_shipped_to": "2026-11-04"})) == [middle.pk]
    response = client_logged_in.get(url, {"f_customer": str(north.pk), "f_shipped_to": "2026-11-04"})
    assert listed(response) == [early.pk]
    assert Shipment.objects.count() == 3


@pytest.mark.django_db
def test_delivery_note_list_filters_status_customer_and_issued(client_logged_in, user, item):
    north = _customer(user, "C001", "作り物の北の得意先")
    south = _customer(user, "C002", "作り物の南の得意先")
    north_line, south_line = _line(user, north, item), _line(user, south, item)
    kept = shipping_services.create_delivery_note(
        north, [shipping_services.ship(north_line, Decimal("5"), date(2026, 11, 3), user)], user
    )
    cancelled = shipping_services.create_delivery_note(
        north, [shipping_services.ship(north_line, Decimal("5"), date(2026, 11, 4), user)], user
    )
    other = shipping_services.create_delivery_note(
        south, [shipping_services.ship(south_line, Decimal("5"), date(2026, 11, 4), user)], user
    )
    shipping_services.cancel_delivery_note(cancelled, user)
    for note, day in ((kept, 3), (cancelled, 4), (other, 5)):
        DeliveryNote.objects.filter(pk=note.pk).update(issued_on=date(2026, 11, day))
    url = reverse("shipping:deliverynote_list")

    body = client_logged_in.get(url).content.decode()
    for name in ("f_customer", "f_issued_from", "f_issued_to", "f_status"):
        assert f'name="{name}"' in body
    assert '<option value="cancelled">取消</option>' in body

    assert listed(client_logged_in.get(url, {"f_status": DeliveryNote.STATUS_CANCELLED})) == [cancelled.pk]
    response = client_logged_in.get(url, {"f_status": DeliveryNote.STATUS_ISSUED, "f_customer": str(north.pk)})
    assert listed(response) == [kept.pk]
    assert listed(client_logged_in.get(url, {"f_issued_from": "2026-11-04"})) == [other.pk, cancelled.pk]
