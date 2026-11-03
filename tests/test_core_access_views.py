"""権限の判定と画面の土台 (02 の 7 節)。Django の test client で HTML と表の状態を見る。

判定を有効にする試験は settings の fixture で SEIZOU を替える。fixture は tests/test_orders_views.py の形を写した。
"""

import csv
import io
import logging
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.billing import services as billing_services
from seizou.core import access
from seizou.core.search import SearchColumn, SearchRow, SearchSection, display_section
from seizou.core.views import BaseListView
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.orders.models import OrderLine
from seizou.shipping import services as shipping_services
from seizou.shipping.models import Shipment
from seizou.workorders import services as workorders_services

User = get_user_model()

PASSWORD = "kumo-tests-2611"
ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
ALL_ON = {"department": "on", "write": "on", "money": "on"}


@pytest.fixture
def on(settings):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}


@pytest.fixture
def user(db):
    """Access の行の無い普通の利用者 (既にある試験の利用者と同じ形)。"""
    return User.objects.create_user("tester", password=PASSWORD)


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def shaft(user):
    return masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個", "standard_price": Decimal("1234")}, user
    )


@pytest.fixture
def order(user, customer, shaft):
    return orders_services.create_order(
        customer,
        [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("1500")}],
        user,
        order_date=ORDER_DATE,
    )


@pytest.fixture
def line(order):
    return order.lines.get()


@pytest.fixture
def invoice(user, customer, line):
    shipment = shipping_services.ship(line, Decimal("4"), SHIPPED_AT, user)
    shipping_services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    return billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)


def account(login_id, *, departments=(), permissions=(), **flags):
    accounts_services.issue_account(login_id, PASSWORD, list(departments), list(permissions), flags, None)
    return User.objects.get(username=login_id)


def access_records(caplog):
    return [r for r in caplog.records if r.name == "seizou.access"]


def _ship_post(line, qty):
    return {"order_line": line.pk, "qty": str(qty), "shipped_at": SHIPPED_AT.isoformat(), "note": ""}


def _cell(response, column_name):
    names = [spec.name for spec in response.context["columns"]]
    index = names.index(column_name)
    return response.context["rows"][0]["cells"][index]["value"]


def _field(response, label):
    # 見出しは単位つき (例「標準単価 (円)」) なので頭の字で引く。
    return next(item["value"] for item in response.context["fields"] if str(item["label"]).startswith(label))


def _has_column(header, label):
    return any(name.startswith(label) for name in header)


def _csv_header(response):
    text = response.content.decode("utf-8-sig")
    return next(csv.reader(io.StringIO(text)))


class _AmountList(BaseListView):
    """合計を選んだ金額の列を持つ一覧 (今の部品の一覧に無いので、試験の中だけで作る)。"""

    model = OrderLine
    columns = ({"name": "amount", "total": True},)


def _amount_totals(user):
    view = _AmountList()
    view.request = RequestFactory().get("/")
    view.request.user = user
    return view.totals_for(OrderLine.objects.all(), view.column_specs())


# ---------------------------------------------------------------- 既定 (記録だけ)


@pytest.mark.django_db
def test_default_keeps_current_behavior(caplog, logged_in, line, shaft):
    caplog.set_level(logging.WARNING, logger="seizou.access")

    assert logged_in.get(reverse("orders:order_list")).status_code == 200

    response = logged_in.post(reverse("shipping:shipment_create"), _ship_post(line, 3))
    assert response.status_code == 302
    assert Shipment.objects.filter(order_line=line).count() == 1

    item_list = logged_in.get(reverse("masters:item_list"))
    assert _cell(item_list, "standard_price") != access.MASK
    detail = logged_in.get(reverse("masters:item_detail", args=[shaft.pk]))
    assert _field(detail, "標準単価") is not None

    messages = [r.getMessage() for r in access_records(caplog)]
    assert any(m.startswith("記録だけ (編集の権限): tester が shipping.ship") for m in messages)
    assert any(m.startswith("記録だけ (金額の閲覧): tester が ") for m in messages)


# ---------------------------------------------------------------- 編集の権限 (有効)


@pytest.mark.django_db
def test_form_refused_without_permission(on, logged_in, line):
    # 残 (10) を超える数。検査の層 (R01) より先に権限で断る。
    response = logged_in.post(reverse("shipping:shipment_create"), _ship_post(line, 11))

    assert response.status_code == 200
    html = response.content.decode()
    assert "alert-error" in html
    assert "権限" in html
    assert "出荷と納品書の編集の権限がありません。" in html
    assert "R01" not in html
    assert Shipment.objects.filter(order_line=line).count() == 0


@pytest.mark.django_db
def test_plan_place_refused_without_permission(on, logged_in, user, line):
    cut = Process.objects.create(code="CUT", name="切断", sort_order=1, use_in_plan=True)
    workorder = workorders_services.issue_workorder(
        line, Decimal("4"), [{"process": cut, "planned_qty": Decimal("4")}], user
    )
    step = workorder.steps.get()

    response = logged_in.post(
        reverse("workorders:plan_place"),
        {"step": step.pk, "start": "2026-11-06", "process": cut.pk, "board_start": "2026-11-02", "weeks": "1"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    html = response.content.decode()
    assert "alert-error" in html
    assert "指示書の編集の権限がありません。" in html
    step.refresh_from_db()
    assert (step.planned_start, step.planned_end) == (None, None)


# ---------------------------------------------------------------- 部署 (有効)


@pytest.mark.django_db
def test_department_403_and_menu(on, client, order, shaft):
    seizou = accounts_services.create_department("製造", ["workorders", "progress", "masters"], ["workorders"], None)
    genba = account("genba", departments=[seizou])
    client.force_login(genba)

    denied = client.get(reverse("orders:order_list"))
    assert denied.status_code == 403
    html = denied.content.decode()
    assert "この画面は開けません" in html
    assert "「受注」の画面です" in html
    assert "あなたの部署: 製造" in html
    assert "no-store" in denied["Cache-Control"]

    assert client.get(reverse("orders:order_detail", args=[order.pk])).status_code == 200
    assert client.get(reverse("core:search") + "?q=T-100").status_code == 200

    board = client.get(reverse("workorders:workorder_list"))
    assert board.status_code == 200
    labels = [item["label"] for item in board.context["seizou"]["menu"]]
    assert "指示書" in labels
    assert "受注" not in labels

    # 小窓と候補の口は部署を見ない (部署の外の部品でも開く)。
    sales = accounts_services.create_department("受注と出荷", ["orders", "shipping"], ["orders"], None)
    jimu = account("jimu", departments=[sales])
    client.force_login(jimu)
    assert client.get(reverse("masters:item_list")).status_code == 403
    assert client.get(reverse("masters:item_modal", args=[shaft.pk]), HTTP_HX_REQUEST="true").status_code == 200
    assert client.get(reverse("masters:item_select") + "?q=T").status_code == 200


@pytest.mark.django_db
def test_anonymous_goes_to_login(on, client):
    response = client.get(reverse("orders:order_list"))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("core:login"))


# ---------------------------------------------------------------- 金額 (有効)


@pytest.mark.django_db
def test_money_masked_everywhere(on, logged_in, user, shaft, invoice):
    item_list = logged_in.get(reverse("masters:item_list"))
    assert _cell(item_list, "standard_price") == access.MASK

    invoice_list = logged_in.get(reverse("billing:invoice_list"))
    assert _cell(invoice_list, "total") == access.MASK

    exported = logged_in.get(reverse("masters:item_list") + "?format=csv")
    assert exported.status_code == 200
    header = _csv_header(exported)
    assert "自社品番" in header
    assert not _has_column(header, "標準単価")

    detail = logged_in.get(reverse("masters:item_detail", args=[shaft.pk]))
    assert _field(detail, "標準単価") is None
    modal = logged_in.get(reverse("masters:item_modal", args=[shaft.pk]), HTTP_HX_REQUEST="true")
    assert _field(modal, "標準単価") in (None, "")

    home = logged_in.get(reverse("core:home"))
    assert home.status_code == 200
    assert [row["total"] for row in home.context["recent_orders"]] == [None]
    assert home.context["shipping"]["amount_chart"] is None
    assert 'id="shipping-amount-chart"' not in home.content.decode()

    assert _amount_totals(User.objects.get(pk=user.pk))[0]["value"] == access.MASK


@pytest.mark.django_db
def test_money_visible_with_permission(on, client, shaft, invoice):
    keiri = account("keiri", see_money=True)
    client.force_login(keiri)

    assert _cell(client.get(reverse("masters:item_list")), "standard_price") not in (access.MASK, None, "")
    assert _cell(client.get(reverse("billing:invoice_list")), "total") not in (access.MASK, None, "")
    assert _has_column(_csv_header(client.get(reverse("masters:item_list") + "?format=csv")), "標準単価")
    assert _field(client.get(reverse("masters:item_detail", args=[shaft.pk])), "標準単価") is not None

    home = client.get(reverse("core:home"))
    assert [row["total"] for row in home.context["recent_orders"]] == [Decimal("15000")]
    assert home.context["shipping"]["amount_chart"] is not None
    assert "出荷 (直近 6 か月)" in home.content.decode()

    assert _amount_totals(keiri)[0]["value"] == Decimal("15000")


def test_search_money_column():
    section = SearchSection(
        kind="tests",
        label="試験",
        columns=(SearchColumn("金額", numeric=True, money=True), SearchColumn("品番", code=True)),
        rows=(SearchRow(cells=(Decimal("1200"), "T-100")),),
        total=1,
    )

    hidden = display_section(section, hide_money=True)["rows"][0]["cells"]
    assert hidden[0]["value"] == access.MASK
    assert hidden[1]["value"] == "T-100"

    shown = display_section(section, hide_money=False)["rows"][0]["cells"]
    assert shown[0]["value"] == Decimal("1200")


# ---------------------------------------------------------------- 使用停止


@pytest.mark.django_db
def test_disabled_user_goes_to_login(client, db):
    target = account("genba")
    client.force_login(target)
    url = reverse("orders:order_list")
    assert client.get(url).status_code == 200

    row = target.seizou_access
    accounts_services.disable_account(row, None)
    response = client.get(url)
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("core:login"))

    accounts_services.enable_account(row, None)
    assert client.get(url).status_code == 200
