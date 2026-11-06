"""請求書の期間の 2 つの字 (初見テスト R5 の V97。決め C46-1 = a)。

期間は得意先の締日で決まるので、表示名を「締め期間の初め」「締め期間の終わり」にした。一覧の列・詳細の基本情報・一覧の CSV の
見出しは、どれも同じ表示名から出る。新しい字は古い字「期間の初め」を中に含むので、本文の字でなく、字の並びの一致で見る。
期間の値は tests/test_billing_invoice.py が見る。会社名と部品は作り物。
"""

from datetime import date

import pytest
from django.urls import reverse

from seizou.billing import services
from seizou.masters import services as masters_services
from tests.billing_helpers import shipped
from tests.test_billing_views import _customer, _order_line

NEW_LABELS = {"締め期間の初め", "締め期間の終わり"}
OLD_LABELS = {"期間の初め", "期間の終わり"}


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def invoice(user, item):
    customer = _customer(user)
    shipment = shipped(user, customer, _order_line(user, customer, item), "4")
    return services.create_invoice(customer, [shipment], user, issued_on=date(2026, 11, 30))


def _assert_new_labels(labels):
    assert NEW_LABELS <= set(labels)
    assert not OLD_LABELS & set(labels)


@pytest.mark.django_db
def test_list_columns_say_closing_period(client_logged_in, invoice):
    response = client_logged_in.get(reverse("billing:invoice_list"))

    assert response.status_code == 200
    _assert_new_labels([spec.label for spec in response.context["columns"]])


@pytest.mark.django_db
def test_detail_fields_say_closing_period(client_logged_in, invoice):
    response = client_logged_in.get(reverse("billing:invoice_detail", args=[invoice.pk]))

    assert response.status_code == 200
    _assert_new_labels([field["label"] for field in response.context["fields"]])


@pytest.mark.django_db
def test_csv_header_says_closing_period(client_logged_in, invoice):
    response = client_logged_in.get(reverse("billing:invoice_list") + "?format=csv")

    assert response.status_code == 200
    _assert_new_labels(response.content.decode("utf-8-sig").splitlines()[0].split(","))
