"""一覧の FK の列の select_related (BaseListView.get_queryset。N174)。

列の宣言のうち前向きの FK (ForeignKey と OneToOneField。annotate の列は除く) の相手を、土台が行と一緒に 1 回の SELECT で引く。
行の数を変えても SELECT の数が同じことを、試験の中で作った画面 (RequestFactory) と本物の画面 (test client) で見る。
FK の列の無い一覧と列の無い一覧は select_related を足さない。金額の閲覧の記録 (log) は 1 要求で 1 行のまま。
問い合わせの数は CaptureQueriesContext で見る (最初の要求は設定の控えを読むので、数える前に 1 回開く)。名前は作り物だけ。
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.core import access
from seizou.core.views import BaseListView
from seizou.masters import services as masters_services
from seizou.masters.models import Item
from seizou.orders import services as orders_services
from seizou.orders.models import Order
from seizou.shipping import services as shipping_services
from seizou.stock.models import StockMove

MONEY_LOG = {"access_control": {"department": "off", "write": "off", "money": "log"}}


class _OrderList(BaseListView):
    """部品の get_queryset を持たない、FK の列 (得意先) のある一覧。"""

    model = Order
    screen_name = "作り物の受注の一覧"
    columns = ("order_no", "customer", "order_date")
    ordering = ("order_date", "pk")
    template_name = "core/list.html"


class _ItemList(BaseListView):
    """FK の列の無い一覧。"""

    model = Item
    screen_name = "作り物の部品の一覧"
    columns = ("part_no", "name")
    ordering = ("part_no",)
    template_name = "core/list.html"


class _BareOrderList(BaseListView):
    """列の無い一覧。"""

    model = Order
    screen_name = "作り物の列の無い一覧"
    ordering = ("pk",)
    template_name = "core/list.html"


class _PriceList(BaseListView):
    """金額の列 (標準単価) のある、FK の列の無い一覧。"""

    model = Item
    screen_name = "作り物の単価の一覧"
    columns = ("part_no", "standard_price")
    ordering = ("part_no",)
    template_name = "core/list.html"


class _MoveList(BaseListView):
    """前向きの FK (部品) と、GenericForeignKey (元の記録。select_related に渡せない) の列のある一覧。"""

    model = StockMove
    screen_name = "作り物の入出庫の一覧"
    columns = ("item", "source")
    ordering = ("pk",)
    template_name = "core/list.html"


def selects(queries) -> int:
    return sum(1 for query in queries if query["sql"].lstrip().upper().startswith("SELECT"))


def view_for(view_class, user):
    request = RequestFactory().get("/")
    request.user = user
    view = view_class()
    view.setup(request)
    return view


def render_selects(view_class, user) -> int:
    """試験の中の画面を RequestFactory で開いて描いた 1 回の SELECT の数 (前に 1 回描いて、設定の控えを読み終えておく)。"""

    def render():
        request = RequestFactory().get("/")
        request.user = user
        response = view_class.as_view()(request)
        response.render()
        return response

    render()
    with CaptureQueriesContext(connection) as ctx:
        response = render()
    assert response.status_code == 200
    return selects(ctx.captured_queries)


def client_selects(client, url) -> int:
    assert client.get(url).status_code == 200
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200
    return selects(ctx.captured_queries)


def make_orders(user, count: int) -> list:
    """得意先の違う受注を count 件。"""
    item = Item.objects.filter(part_no="N174-1").first() or masters_services.create_item(
        {"part_no": "N174-1", "name": "作り物の軸", "unit": "個"}, user
    )
    made = []
    start = Order.objects.count()
    for n in range(start, start + count):
        customer = masters_services.create_partner({"code": f"C{n:03}", "name": f"作り物の得意先{n}", "role": "customer"}, user)
        made.append(
            orders_services.create_order(
                customer, [{"item": item, "qty": Decimal("1"), "unit_price": Decimal("100")}], user, order_date=date(2026, 11, 2)
            )
        )
    return made


def access_records(caplog):
    return [r for r in caplog.records if r.name == "seizou.access"]


# ---------------------------------------------------------------- 何を select_related に渡すか


@pytest.mark.django_db
def test_fk_columns_are_selected_with_rows(user):
    assert view_for(_OrderList, user).get_queryset().query.select_related == {"customer": {}}


@pytest.mark.django_db
def test_lists_without_fk_columns_add_nothing(user):
    assert view_for(_ItemList, user).get_queryset().query.select_related is False
    assert view_for(_BareOrderList, user).get_queryset().query.select_related is False


@pytest.mark.django_db
def test_generic_foreign_key_column_is_left_out(user):
    queryset = view_for(_MoveList, user).get_queryset()

    assert queryset.query.select_related == {"item": {}}
    assert list(queryset) == []  # 渡した名前で SQL が組める (GenericForeignKey を渡すと FieldError になる)


# ---------------------------------------------------------------- 行の数で SELECT が増えない


@pytest.mark.django_db
def test_fk_column_selects_do_not_grow_with_rows(user):
    make_orders(user, 1)
    one = render_selects(_OrderList, user)
    make_orders(user, 4)
    five = render_selects(_OrderList, user)

    assert Order.objects.count() == 5
    assert five == one


@pytest.mark.django_db
def test_real_list_without_own_select_related_does_not_grow(logged_in, user):
    """休日の一覧 (登録した人の FK の列。部品の get_queryset は select_related を足さない)。"""
    url = reverse("masters:holiday_list")
    first = date(2026, 12, 1)
    masters_services.create_holiday({"date": first, "name": "作り物の休み0", "kind": "company"}, user)
    one = client_selects(logged_in, url)
    for n in range(1, 5):
        masters_services.create_holiday({"date": first + timedelta(days=n), "name": f"作り物の休み{n}", "kind": "company"}, user)
    five = client_selects(logged_in, url)

    assert five == one
    assert logged_in.get(url).content.decode().count(user.get_username()) >= 5


@pytest.mark.django_db
def test_list_without_fk_columns_keeps_select_count(logged_in, user):
    """取引先の一覧 (FK の列が無い) は、行の数で SELECT が増えない (今までどおり)。"""
    url = reverse("masters:partner_list")
    masters_services.create_partner({"code": "C900", "name": "作り物の得意先900", "role": "customer"}, user)
    one = client_selects(logged_in, url)
    for n in range(901, 905):
        masters_services.create_partner({"code": f"C{n}", "name": f"作り物の得意先{n}", "role": "customer"}, user)
    five = client_selects(logged_in, url)

    assert five == one


# ---------------------------------------------------------------- 金額の閲覧の記録は 1 要求で 1 行のまま


@pytest.mark.django_db
def test_money_record_is_one_line_per_request(settings, caplog, logged_in, user):
    settings.SEIZOU = MONEY_LOG
    caplog.set_level(logging.WARNING, logger="seizou.access")
    customer = masters_services.create_partner({"code": "C174", "name": "作り物の得意先", "role": "customer", "closing_day": 20}, user)
    item = masters_services.create_item({"part_no": "N174-2", "name": "作り物の板", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("5"), "unit_price": Decimal("1500")}], user, order_date=date(2026, 11, 2)
    )
    shipment = shipping_services.ship(order.lines.get(), Decimal("2"), date(2026, 11, 5), user)
    shipping_services.create_delivery_note(customer, [shipment], user)
    billing_services.create_invoice(customer, [shipment], user, issued_on=date(2026, 11, 30))
    caplog.clear()

    response = logged_in.get(reverse("billing:invoice_list"))

    assert response.status_code == 200
    records = [r.getMessage() for r in access_records(caplog)]
    assert len(records) == 1
    assert records[0].startswith("記録だけ (金額の閲覧): tester が ")

    assert logged_in.get(reverse("billing:invoice_list"), {"q": "作り物"}).status_code == 200
    assert len(access_records(caplog)) == 2  # 次の要求でまた 1 行


@pytest.mark.django_db
@pytest.mark.parametrize(("state", "shown"), [("log", ["part_no", "standard_price"]), ("on", ["part_no"])])
def test_money_check_runs_once_per_view(settings, monkeypatch, user, state, shown):
    """列を何度読んでも、金額の閲覧の判定 (記録つき) は画面 (1 要求) で 1 回だけ。log では見せ、on では列ごと外す。"""
    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": state}}
    calls = []
    real = access.can_see_money

    def counting(who, **kwargs):
        calls.append(kwargs.get("record", True))
        return real(who, **kwargs)

    monkeypatch.setattr(access, "can_see_money", counting)
    view = view_for(_PriceList, user)
    for _ in range(3):
        assert [spec.name for spec in view.column_specs()] == shown
    view.sort_param()
    view.get_queryset()

    assert calls == [True]
