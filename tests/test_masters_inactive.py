"""部品と取引先の使用停止 (決め 11-04 22:0x。N135)。消さずに候補から外し、今までの記録はそのまま見える。

作り物のデータは全部 services で作る。使用停止は update_item / update_partner に active=False を渡す (編集の画面と同じ道)。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.intake.forms import UploadForm
from seizou.masters import services as masters_services
from seizou.masters.models import Item, Partner
from seizou.orders import services as orders_services
from seizou.orders.forms import OrderForm, OrderLineForm
from seizou.orders.models import Order
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.forms import PurchaseOrderForm, PurchaseOrderLineForm
from seizou.purchasing.models import PurchaseOrder

DIMMED = " text-base-content/60"


@pytest.fixture
def customers(user):
    """使用中の得意先 C001 と、使用停止の得意先 C002。"""
    used = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    stopped = masters_services.create_partner({"code": "C002", "name": "作り物の古い得意先", "role": "customer"}, user)
    masters_services.update_partner(stopped, {"active": False}, user)
    return used, stopped


@pytest.fixture
def items(user):
    """使用中の部品 T-100 と、使用停止の部品 T-900。"""
    used = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    stopped = masters_services.create_item({"part_no": "T-900", "name": "作り物の古い軸", "unit": "個"}, user)
    masters_services.update_item(stopped, {"active": False}, user)
    return used, stopped


@pytest.fixture
def subcontractors(user):
    used = masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)
    stopped = masters_services.create_partner({"code": "S002", "name": "作り物の古い協力企業", "role": "subcontractor"}, user)
    masters_services.update_partner(stopped, {"active": False}, user)
    return used, stopped


def _line(item, qty="10", unit_price="100"):
    return {"item": item, "qty": Decimal(qty), "unit_price": Decimal(unit_price)}


def _row(body, pk):
    start = body.index(f'<tr id="row-{pk}"')
    return body[start : body.index(">", start)]


@pytest.mark.django_db
def test_lists_show_active_only_until_flag_and_dim_inactive_rows(logged_in, customers, items):
    for url_name, (used, stopped), key in (
        ("masters:item_list", items, "part_no"),
        ("masters:partner_list", customers, "code"),
    ):
        body = logged_in.get(reverse(url_name)).content.decode()
        assert getattr(used, key) in body and getattr(stopped, key) not in body
        assert "使用停止も出す" in body
        assert DIMMED not in _row(body, used.pk)

        shown = logged_in.get(reverse(url_name), {"f_inactive": "1"}).content.decode()
        assert getattr(used, key) in shown and getattr(stopped, key) in shown
        assert DIMMED in _row(shown, stopped.pk)
        assert DIMMED not in _row(shown, used.pk)

        rows = logged_in.get(reverse(url_name), {"f_inactive": "1"}, HTTP_HX_REQUEST="true").content.decode()
        assert "<html" not in rows and DIMMED in _row(rows, stopped.pk)


@pytest.mark.django_db
def test_list_csv_follows_the_flag(logged_in, items):
    used, stopped = items

    default = logged_in.get(reverse("masters:item_list"), {"format": "csv"}).content.decode("utf-8-sig")
    shown = logged_in.get(reverse("masters:item_list"), {"format": "csv", "f_inactive": "1"}).content.decode("utf-8-sig")

    assert used.part_no in default and stopped.part_no not in default
    assert stopped.part_no in shown


@pytest.mark.django_db
def test_select_candidates_are_active_only(logged_in, customers, items):
    item_ids = [row["id"] for row in logged_in.get(reverse("masters:item_select")).json()["items"]]
    partner_ids = [row["id"] for row in logged_in.get(reverse("masters:partner_select")).json()["items"]]

    assert item_ids == [items[0].pk]
    assert partner_ids == [customers[0].pk]
    assert [row["id"] for row in masters_services.candidates("item", "", 1)["results"]] == [items[0].pk]
    assert [row["id"] for row in masters_services.candidates("partner", "作り物", 1)["results"]] == [customers[0].pk]


@pytest.mark.django_db
def test_edit_screen_turns_item_off_and_detail_shows_notice(logged_in, user):
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸"}, user)
    assert "active" not in logged_in.get(reverse("masters:item_create")).context["form"].fields
    form = logged_in.get(reverse("masters:item_update", args=[item.pk])).context["form"]
    assert form["active"].value() is True

    # チェックを外して保存 (チェックの無い欄は送られない) すると使用停止
    response = logged_in.post(reverse("masters:item_update", args=[item.pk]), {"part_no": "T-100", "name": "作り物の軸"})
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.active is False
    body = logged_in.get(reverse("masters:item_detail", args=[item.pk])).content.decode()
    assert "使用停止の部品です。" in body

    logged_in.post(reverse("masters:item_update", args=[item.pk]), {"part_no": "T-100", "name": "作り物の軸", "active": "on"})
    item.refresh_from_db()
    assert item.active is True
    assert "使用停止の部品です。" not in logged_in.get(reverse("masters:item_detail", args=[item.pk])).content.decode()


@pytest.mark.django_db
def test_edit_screen_turns_partner_off_and_detail_shows_notice(logged_in, user):
    partner = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    assert "active" not in logged_in.get(reverse("masters:partner_create")).context["form"].fields

    response = logged_in.post(
        reverse("masters:partner_update", args=[partner.pk]), {"code": "C001", "name": "作り物の得意先", "role": "customer"}
    )

    assert response.status_code == 302
    partner.refresh_from_db()
    assert partner.active is False
    assert "使用停止の取引先です。" in logged_in.get(reverse("masters:partner_detail", args=[partner.pk])).content.decode()


@pytest.mark.django_db
def test_item_modal_shows_inactive_notice_above_fields(logged_in, customers, items):
    """部品の小窓の使用停止の黄色の 1 行 (本人の決め 11-06 17:5x「小窓にも黄色の1行」。C55)。題の下・項目の上に、詳細と同じ字で出す。"""
    used, stopped = items
    _, stopped_customer = customers

    body = logged_in.get(reverse("masters:item_modal", args=[stopped.pk]), HTTP_HX_REQUEST="true").content.decode()

    notice = 'class="alert alert-warning alert-soft mb-4" role="status"'
    words = "使用停止の部品です。受注と発注の候補に出ません。今までの受注と発注の記録はそのまま残ります。"
    assert notice in body and words in body
    assert body.index(notice) < body.index("<dl") and body.index(words) < body.index("<dl")
    # 字が詳細とそろう (詳細の字だけを直して小窓の字が古いまま残るのを止める)
    detail = logged_in.get(reverse("masters:item_detail", args=[stopped.pk])).content.decode()
    start = detail.index("使用停止の部品です。")
    assert detail[start : detail.index("</span>", start)] in body

    used_body = logged_in.get(reverse("masters:item_modal", args=[used.pk]), HTTP_HX_REQUEST="true").content.decode()
    partner_body = logged_in.get(
        reverse("masters:partner_modal", args=[stopped_customer.pk]), HTTP_HX_REQUEST="true"
    ).content.decode()
    assert "使用停止の部品です。" not in used_body
    assert "使用停止の部品です。" not in partner_body


@pytest.mark.django_db
def test_create_order_refuses_inactive_customer_or_item(user, customers, items):
    used_customer, stopped_customer = customers
    used_item, stopped_item = items

    with pytest.raises(ValueError, match="使用停止の取引先です"):
        orders_services.create_order(stopped_customer, [_line(used_item)], user, order_date=date(2026, 11, 4))
    with pytest.raises(ValueError, match="使用停止の部品です"):
        orders_services.create_order(used_customer, [_line(used_item), _line(stopped_item)], user, order_date=date(2026, 11, 4))

    assert not Order.objects.exists()


@pytest.mark.django_db
def test_create_purchase_order_refuses_inactive_partner_or_item(user, subcontractors, items):
    used_partner, stopped_partner = subcontractors
    used_item, stopped_item = items

    with pytest.raises(ValueError, match="使用停止の取引先です"):
        purchasing_services.create_purchase_order(stopped_partner, [_line(used_item)], user, order_date=date(2026, 11, 4), send=False)
    with pytest.raises(ValueError, match="使用停止の部品です"):
        purchasing_services.create_purchase_order(used_partner, [_line(stopped_item)], user, order_date=date(2026, 11, 4), send=False)

    assert not PurchaseOrder.objects.exists()


@pytest.mark.django_db
def test_existing_order_stays_visible_and_editable_after_stop(logged_in, user):
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(customer, [_line(item)], user, order_date=date(2026, 11, 4))
    masters_services.update_partner(customer, {"active": False}, user)
    masters_services.update_item(item, {"active": False}, user)

    # 今までの記録はそのまま見える (受注の詳細・取引先の詳細の受注履歴)
    detail = logged_in.get(reverse("orders:order_detail", args=[order.pk])).content.decode()
    assert "作り物の得意先" in detail and "T-100" in detail
    assert order.order_no in logged_in.get(reverse("masters:partner_detail", args=[customer.pk])).content.decode()

    # 直す画面は今の得意先と部品を残す (直しを止めない)
    edit = logged_in.get(reverse("orders:order_update", args=[order.pk])).content.decode()
    assert f'<option value="{customer.pk}" selected>作り物の得意先</option>' in edit
    line = order.lines.get()
    data = {
        "customer": customer.pk,
        "order_date": "2026-11-04",
        "customer_order_no": "",
        "note": "",
        "lines-TOTAL_FORMS": "1",
        "lines-INITIAL_FORMS": "1",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
        "lines-0-order_line": line.pk,
        "lines-0-item": item.pk,
        "lines-0-qty": "12",
        "lines-0-unit": "H87",
        "lines-0-unit_price": "100",
    }
    response = logged_in.post(reverse("orders:order_update", args=[order.pk]), data)
    assert response.status_code == 302
    line.refresh_from_db()
    assert line.qty == Decimal("12")


@pytest.mark.django_db
def test_new_order_and_purchase_forms_offer_active_only(user, customers, items, subcontractors):
    # 受注の得意先の欄は、今までどおり取引先の種類で絞らない (得意先でない物は services が断る)。使用停止だけを外す。
    order_customers = list(OrderForm().fields["customer"].queryset)
    assert customers[0] in order_customers and customers[1] not in order_customers
    assert list(OrderLineForm().fields["item"].queryset) == [items[0]]
    assert list(PurchaseOrderForm().fields["partner"].queryset) == [subcontractors[0]]
    assert list(PurchaseOrderLineForm().fields["item"].queryset) == [items[0]]
    assert list(UploadForm().fields["customer"].queryset) == [customers[0]]


@pytest.mark.django_db
def test_csv_without_active_column_keeps_current_value(user, items):
    _used, stopped = items

    result = masters_services.import_items(
        [{"line": 2, "data": {"part_no": "T-900", "name": "作り物の古い軸 (直した名前)"}, "ext": None},
         {"line": 3, "data": {"part_no": "T-300", "name": "作り物の新しい軸"}, "ext": None}],
        user,
    )

    assert result == {"入れた": 1, "更新した": 1}
    stopped.refresh_from_db()
    assert (stopped.name, stopped.active) == ("作り物の古い軸 (直した名前)", False)
    assert Item.objects.get(part_no="T-300").active is True
    assert Partner._meta.get_field("active").editable is False
