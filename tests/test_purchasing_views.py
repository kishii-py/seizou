"""発注の画面 (core の土台の上)。相手の受け口への HTTP は requests.post を偽の物に差し替えて見る。

鍵はテストのたびに作り、環境変数に置く。取引先と品目は作り物。
"""

import pytest
import requests
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.connect.models import Outbox
from seizou.masters import services as masters_services
from seizou.purchasing.models import PurchaseOrder
from tests.test_connect_signing import _new_secret

ENV_NAME = "SEIZOU_PARTNER_TESTSUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"


class FakeResponse:
    def __init__(self, status_code, data=None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data


class FakePost:
    def __init__(self, reply=None, raises=None):
        self.reply = reply if reply is not None else FakeResponse(200, {"status": "received"})
        self.raises = raises
        self.calls = 0

    def __call__(self, url, data=None, headers=None, timeout=None):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
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
def items(user):
    return [
        masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user),
        masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user),
    ]


@pytest.fixture
def send_connection(monkeypatch, subcontractor, user):
    monkeypatch.setenv(ENV_NAME, _new_secret())
    return connect_services.register_connection(subcontractor, PARTNER_URL, ENV_NAME, "send", user)


def _post_data(partner, items, total=2):
    data = {
        "partner": partner.pk,
        "order_date": "2026-11-02",
        "note": "作り物の発注",
        "lines-TOTAL_FORMS": str(total),
        "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
        "lines-0-item": items[0].pk,
        "lines-0-qty": "10",
        "lines-0-unit_price": "25.5",
        "lines-0-due_date": "2026-11-20",
        "lines-1-item": items[1].pk,
        "lines-1-qty": "3",
        "lines-1-unit": "kg",
        "lines-1-unit_price": "100",
    }
    return data


@pytest.mark.django_db
def test_list_requires_login(client):
    response = client.get(reverse("purchasing:purchaseorder_list"))
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


@pytest.mark.django_db
def test_create_posts_and_sends(monkeypatch, client_logged_in, subcontractor, items, send_connection):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    response = client_logged_in.post(
        reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items)
    )

    po = PurchaseOrder.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("purchasing:purchaseorder_detail", args=[po.pk])
    assert fake.calls == 1
    assert Outbox.objects.count() == 1
    assert po.status == PurchaseOrder.STATUS_SENT
    assert [line.line_no for line in po.lines.all()] == [1, 2]
    assert po.lines.get(line_no=2).unit == "kg"

    detail = client_logged_in.get(response["Location"]).content.decode()
    assert "受け取った" in detail
    assert "送った" in detail
    assert "T-100" in detail and "作り物の板" in detail
    # 「送った」の発注には送り直すと取り消すのボタンを出さない
    assert reverse("purchasing:purchaseorder_send", args=[po.pk]) not in detail
    assert reverse("purchasing:purchaseorder_cancel", args=[po.pk]) not in detail


@pytest.mark.django_db
def test_create_with_unreachable_partner_stays_draft(monkeypatch, client_logged_in, subcontractor, items, send_connection):
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("作り物の失敗")))

    response = client_logged_in.post(
        reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items)
    )

    po = PurchaseOrder.objects.get()
    assert response.status_code == 302
    assert po.status == PurchaseOrder.STATUS_DRAFT
    detail = client_logged_in.get(response["Location"]).content.decode()
    assert "届きません" in detail
    assert "送り直す" in detail
    assert reverse("purchasing:purchaseorder_send", args=[po.pk]) in detail


@pytest.mark.django_db
def test_resend_button_sends_again(monkeypatch, client_logged_in, subcontractor, items, send_connection):
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("作り物の失敗")))
    client_logged_in.post(reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items))
    po = PurchaseOrder.objects.get()
    assert po.status == PurchaseOrder.STATUS_DRAFT

    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    response = client_logged_in.post(reverse("purchasing:purchaseorder_send", args=[po.pk]))

    assert response.status_code == 302
    assert fake.calls == 1
    assert Outbox.objects.filter(purchase_order=po).count() == 2
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_SENT


@pytest.mark.django_db
def test_resend_without_connection_is_red_notice(monkeypatch, client_logged_in, subcontractor, items):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    client_logged_in.post(reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items))
    po = PurchaseOrder.objects.get()

    response = client_logged_in.post(reverse("purchasing:purchaseorder_send", args=[po.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "alert-error" in body and "つながりがありません" in body
    assert fake.calls == 0
    assert Outbox.objects.count() == 0


@pytest.mark.django_db
def test_create_without_lines_is_red_notice(client_logged_in, subcontractor, items):
    data = _post_data(subcontractor, items, total=1)
    for key in ("lines-0-item", "lines-0-qty", "lines-0-unit_price", "lines-0-due_date"):
        data.pop(key)

    response = client_logged_in.post(reverse("purchasing:purchaseorder_create"), data)

    assert response.status_code == 200
    body = response.content.decode()
    assert "alert-error" in body and "明細がありません" in body
    assert PurchaseOrder.objects.count() == 0


@pytest.mark.django_db
def test_create_keeps_line_errors_on_screen(client_logged_in, subcontractor, items):
    data = _post_data(subcontractor, items)
    data["lines-1-qty"] = ""

    response = client_logged_in.post(reverse("purchasing:purchaseorder_create"), data)

    assert response.status_code == 200
    body = response.content.decode()
    assert "作り物の発注" in body  # 入れた値は消えない
    assert PurchaseOrder.objects.count() == 0


@pytest.mark.django_db
def test_customer_only_partner_is_not_a_choice(client_logged_in, user, subcontractor):
    masters_services.create_partner({"code": "C001", "name": "作り物の得意先だけ", "role": "customer"}, user)
    masters_services.create_partner({"code": "B001", "name": "作り物の両方の取引先", "role": "both"}, user)

    response = client_logged_in.get(reverse("purchasing:purchaseorder_create"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "作り物の協力企業" in body and "作り物の両方の取引先" in body
    assert "作り物の得意先だけ" not in body
    assert 'id="po-line-template"' in body and "行を足す" in body


@pytest.mark.django_db
def test_cancel_view_cancels_draft_with_reason(monkeypatch, client_logged_in, subcontractor, items):
    monkeypatch.setattr("requests.post", FakePost())
    client_logged_in.post(reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items))
    po = PurchaseOrder.objects.get()

    response = client_logged_in.post(
        reverse("purchasing:purchaseorder_cancel", args=[po.pk]), {"reason": "数を間違えた"}
    )

    assert response.status_code == 302
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_CANCELLED
    assert po.note.endswith("取消の理由: 数を間違えた")


@pytest.mark.django_db
def test_list_shows_orders_and_searches(monkeypatch, client_logged_in, user, subcontractor, items):
    monkeypatch.setattr("requests.post", FakePost())
    client_logged_in.post(reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items))
    other = masters_services.create_partner(
        {"code": "S002", "name": "作り物の別の協力企業", "role": "subcontractor"}, user
    )
    client_logged_in.post(reverse("purchasing:purchaseorder_create"), _post_data(other, items))

    body = client_logged_in.get(reverse("purchasing:purchaseorder_list")).content.decode()
    assert "PO-000001" in body and "PO-000002" in body
    assert "下書き" in body
    assert reverse("masters:partner_modal", args=[subcontractor.pk]) in body

    rows = client_logged_in.get(
        reverse("purchasing:purchaseorder_list"), {"q": "S002"}, HTTP_HX_REQUEST="true"
    ).content.decode()
    assert "PO-000002" in rows and "PO-000001" not in rows


@pytest.mark.django_db
def test_menu_has_purchase_entry(client_logged_in):
    body = client_logged_in.get(reverse("core:home")).content.decode()
    assert reverse("purchasing:purchaseorder_list") in body
    assert "発注" in body
