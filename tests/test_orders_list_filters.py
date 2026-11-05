"""受注の一覧の絞り (N118。状態・得意先・受注日・納期。本人の決め 11-04 15:0x)。名前は作り物だけ。

状態を選ぶと、既定 (出荷の残りがある受注だけ) を外す。得意先の候補は、取引先の種類が得意先か両方の物だけ。
納期は明細の項目なので、期間に入る明細が 2 行あっても受注は 1 回だけ出す。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.shipping import services as shipping_services
from tests.test_orders_views import customer, logged_in, shaft, user  # noqa: F401 - fixture を借りる

ORDER_DATE = date(2026, 11, 2)


def _order(partner, item, user, *dues, qty="5"):  # noqa: F811
    """明細の行ごとの納期 (dues) を持つ受注。dues が無ければ納期の無い明細 1 行。"""
    lines = [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100"), "due_date": due} for due in dues or (None,)]
    return services.create_order(partner, lines, user, order_date=ORDER_DATE)


def _listed(response):
    return [row["obj"] for row in response.context["rows"]]


@pytest.mark.django_db
def test_status_filter_drops_the_default_and_shows_shipped(user, logged_in, customer, shaft):  # noqa: F811
    open_order = _order(customer, shaft, user)
    done = _order(customer, shaft, user, qty="2")
    shipping_services.ship(done.lines.get(), Decimal("2"), date(2026, 11, 5), user)
    cancelled = _order(customer, shaft, user, qty="1")
    services.cancel_order(cancelled, user)
    url = reverse("orders:order_list")

    assert _listed(logged_in.get(url)) == [open_order]
    response = logged_in.get(url, {"f_status": "shipped"})
    assert _listed(response) == [done]
    assert '<option value="shipped" selected>出荷済</option>' in response.content.decode()
    assert _listed(logged_in.get(url, {"f_status": "cancelled"})) == [cancelled]
    assert _listed(logged_in.get(url, {"f_status": "shipped"}, HTTP_HX_REQUEST="true")) == [done]
    body = logged_in.get(url, {"f_status": "shipped", "format": "csv"}).content.decode("utf-8-sig")
    assert done.order_no in body and open_order.order_no not in body

    # 読めない値は空と同じ (既定のまま)。印 (f_open) を外すと全部、外して状態を選ぶとその状態だけ。受注日の期間は取消も出す (N126)。
    assert _listed(logged_in.get(url, {"f_status": "zzz"})) == [open_order]
    assert set(_listed(logged_in.get(url, {"f_open": "0"}))) == {open_order, done, cancelled}
    assert _listed(logged_in.get(url, {"f_open": "0", "f_status": "open"})) == [open_order]
    month = {"f_open": "0", "f_ordered_from": "2026-11-01", "f_ordered_to": "2026-11-30"}
    assert set(_listed(logged_in.get(url, month))) == {open_order, done, cancelled}


@pytest.mark.django_db
def test_customer_filter_lists_only_customers_and_both(user, logged_in, customer, shaft):  # noqa: F811
    both = masters_services.create_partner({"code": "C002", "name": "作り物の両方の取引先", "role": "both"}, user)
    supplier = masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)
    mine = _order(customer, shaft, user)
    theirs = _order(both, shaft, user)
    url = reverse("orders:order_list")

    response = logged_in.get(url)
    body = response.content.decode()
    for name in ("f_status", "f_customer", "f_ordered_from", "f_ordered_to", "f_due_from", "f_due_to"):
        assert f'name="{name}"' in body
    for label in ("状態", "得意先", "受注日", "納期"):
        assert f">{label}</label>" in body
    (options,) = [f["options"] for f in response.context["filters"] if f["name"] == "customer"]
    assert options == [(str(customer.pk), str(customer)), (str(both.pk), str(both))]
    assert str(supplier) not in body

    response = logged_in.get(url, {"f_customer": str(both.pk)})
    assert _listed(response) == [theirs]
    assert f'<option value="{both.pk}" selected>{both}</option>' in response.content.decode()
    # 協力企業だけの取引先は候補に無いので、読めない値と同じ (絞らない)
    response = logged_in.get(url, {"f_customer": str(supplier.pk)})
    assert set(_listed(response)) == {mine, theirs}
    assert not response.context["filter_active"]


@pytest.mark.django_db
def test_due_range_lists_an_order_once_even_if_two_lines_match(user, logged_in, customer, shaft):  # noqa: F811
    twice = _order(customer, shaft, user, date(2026, 11, 20), date(2026, 11, 25))
    later = _order(customer, shaft, user, date(2026, 12, 10))
    _order(customer, shaft, user)
    url = reverse("orders:order_list")
    params = {"f_due_from": "2026-11-20", "f_due_to": "2026-11-30"}

    response = logged_in.get(url, params)
    assert _listed(response) == [twice]
    assert response.context["paginator"].count == 1
    body = logged_in.get(url, {**params, "format": "csv"}).content.decode("utf-8-sig")
    assert body.count(twice.order_no) == 1
    assert _listed(logged_in.get(url, {"f_due_from": "2026-11-21"})) == [later, twice]
