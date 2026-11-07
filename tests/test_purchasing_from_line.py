"""発注の新規登録の from_line (注残の盤の「発注する」が GET で渡す受注の明細の pk)。

明細の部品・残の数・単位・納期を最初の明細の行の初期値にする。単価と取引先は利用者が選ぶ。
相手の受け口への HTTP は requests.post を偽の物に差し替えて見る。取引先と品目は作り物。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing.models import PurchaseOrder
from seizou.shipping import services as shipping_services
from tests.test_connect_signing import _new_secret

ENV_NAME = "SEIZOU_PARTNER_TESTSUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"
ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
DUE_DATE = date(2026, 11, 20)


class FakeResponse:
    def __init__(self, status_code, data=None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data

    def iter_content(self, chunk_size=1):
        import json

        body = json.dumps(self._data, ensure_ascii=False).encode("utf-8")
        for start in range(0, len(body), chunk_size):
            yield body[start : start + chunk_size]

    def close(self):
        pass


class FakePost:
    def __init__(self):
        self.reply = FakeResponse(200, {"status": "received"})
        self.calls = 0

    def __call__(self, url, data=None, headers=None, timeout=None, **kwargs):
        self.calls += 1
        return self.reply


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user
    )


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
def line(user, customer, shaft):
    """数量 10 のうち 4 を出荷した明細 (納期あり)。残は 6。"""
    order = orders_services.create_order(
        customer,
        [{"item": shaft, "qty": Decimal("10"), "unit": "個", "unit_price": Decimal("100"), "due_date": DUE_DATE}],
        user,
        order_date=ORDER_DATE,
    )
    order_line = order.lines.get()
    shipping_services.ship(order_line, Decimal("4"), SHIPPED_AT, user)
    return order_line


@pytest.fixture
def send_connection(monkeypatch, subcontractor, user):
    monkeypatch.setenv(ENV_NAME, _new_secret())
    return connect_services.register_connection(subcontractor, PARTNER_URL, ENV_NAME, "send", user)


def _create(client, **params):
    response = client.get(reverse("purchasing:purchaseorder_create"), params)
    assert response.status_code == 200
    return response


def _post_data(partner, line, **extra):
    """初期値の行 (明細を写した行) に単価と取引先を足した POST の値。2 行目は空の行のまま。"""
    data = {
        "partner": partner.pk,
        "order_date": "2026-11-03",
        "note": "",
        "lines-TOTAL_FORMS": "2",
        "lines-INITIAL_FORMS": "1",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
        "lines-0-item": line.item_id,
        "lines-0-qty": "6",
        "lines-0-unit": line.unit_code,
        "lines-0-unit_price": "30",
        "lines-0-due_date": line.due_date.isoformat(),
        "lines-0-note": "",
        "lines-1-item": "",
        "lines-1-qty": "",
        "lines-1-unit": "",
        "lines-1-unit_price": "",
        "lines-1-due_date": "",
        "lines-1-note": "",
    }
    data.update(extra)
    return data


@pytest.mark.django_db
def test_from_line_prefills_first_row(client_logged_in, subcontractor, line):
    response = _create(client_logged_in, from_line=line.pk)

    form = response.context["form"]
    assert form.from_line == line
    first = form.lines.forms[0].initial
    assert first["item"] == line.item_id
    assert first["qty"] == Decimal("6")
    assert first["unit"] == "H87"
    assert first["due_date"] == DUE_DATE
    assert "unit_price" not in first
    # 初期値の行と、空の行 1 つ。
    assert len(form.lines.forms) == 2
    assert form.lines.forms[1].initial == {}
    assert form["partner"].value() is None

    body = response.content.decode()
    assert f'<option value="{line.item_id}" selected>' in body
    assert 'value="2026-11-20"' in body
    assert 'name="lines-0-unit_price"' in body
    assert 'name="lines-0-unit_price" value=' not in body


@pytest.mark.django_db
def test_from_line_bad_or_missing_is_ignored(client_logged_in, subcontractor, line):
    for params in ({"from_line": "abc"}, {"from_line": str(line.pk + 1000)}, {"from_line": "-1"}, {}):
        response = _create(client_logged_in, **params)
        form = response.context["form"]
        assert form.from_line is None, params
        assert form.lines.forms[0].initial == {}, params
        assert len(form.lines.forms) == 1, params


@pytest.mark.django_db
def test_save_from_prefilled_line_creates_order(monkeypatch, client_logged_in, subcontractor, line, send_connection):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    response = client_logged_in.post(reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, line))

    po = PurchaseOrder.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("purchasing:purchaseorder_detail", args=[po.pk])
    assert po.partner == subcontractor
    po_line = po.lines.get()
    assert po_line.item_id == line.item_id
    assert po_line.qty == Decimal("6")
    assert po_line.due_date == DUE_DATE
    assert fake.calls == 1


@pytest.mark.django_db
def test_post_ignores_from_line(monkeypatch, client_logged_in, subcontractor, line, plate):
    monkeypatch.setattr("requests.post", FakePost())
    data = _post_data(
        subcontractor,
        line,
        **{"from_line": str(line.pk), "lines-0-item": plate.pk, "lines-0-qty": "3", "lines-0-due_date": ""},
    )
    data["lines-INITIAL_FORMS"] = "0"

    response = client_logged_in.post(reverse("purchasing:purchaseorder_create") + f"?from_line={line.pk}", data)

    assert response.status_code == 302
    po_line = PurchaseOrder.objects.get().lines.get()
    assert po_line.item == plate
    assert po_line.qty == Decimal("3")
    assert po_line.due_date is None
