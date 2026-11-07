"""請求書の写し (依頼書 N357。本人の決め 11-07 23:4x・11-08 00:0x)。

請求書は発行した時の会社の登録番号・住所・電話・振込先と、得意先の名前と、明細の品番・品名・単位 (受注の明細の単位)・得意先の注文番号と、
税率を写して持ち、刷り直しは写しだけを読む。マスターや会社の設定を後で直しても、出した請求書の刷り直しの字は変わらない。
作り物の得意先・部品・会社の情報だけを使う (登録番号・住所・電話・振込先は実在しない字)。請求書は services を通して作る。
"""

import importlib
from datetime import date
from decimal import Decimal

import pytest
from django.apps import apps
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.billing.models import Invoice, InvoiceLine
from seizou.core import services as core_services
from seizou.masters import services as masters_services
from seizou.masters.services import PRICE_BASIS_LUMP
from seizou.orders import services as orders_services
from tests.billing_helpers import SHIPPED_AT, shipped

ORDER_DATE = date(2026, 11, 2)
ISSUED_ON = date(2026, 11, 30)
#: 写しの列の見出し (詳細と小窓の項目の一覧に出さない)
SNAPSHOT_LABELS = ("発行の時の登録番号", "発行の時の住所", "発行の時の電話", "発行の時の振込先", "得意先の名前", "税率 (%)")
MIGRATION = "seizou.billing.migrations.0007_n357_invoice_snapshot"
#: 小窓は htmx の GET でだけ開く (htmx でない GET は詳細へ移る)
HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "SN-100", "name": "作り物の軸", "unit": "個"}, user)


def _customer(user, code="C001", name="作り物の得意先"):
    return masters_services.create_partner({"code": code, "name": name, "role": "customer"}, user)


def _order_line(user, customer, item, *, unit=None, customer_order_no=""):
    data = {"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}
    if unit is not None:
        data["unit"] = unit
    order = orders_services.create_order(customer, [data], user, order_date=ORDER_DATE, customer_order_no=customer_order_no)
    return order.lines.get()


def _invoice(user, customer, item, **line_values):
    """受注の明細 1 つ (10 個 × 1,500 円) を出し切った出荷 1 つの請求書。"""
    line = _order_line(user, customer, item, **line_values)
    return billing_services.create_invoice(customer, [shipped(user, customer, line, "10")], user, issued_on=ISSUED_ON)


# ---------------------------------------------------------------- 1 表と migration


@pytest.mark.django_db
def test_rows_can_be_created_without_snapshot_values(user, item):
    """(a) 写しの列を渡さずに請求書と明細を作れる (既定がある。services を通さずに作る試験のため)。税率の既定は 10.00。"""
    customer = _customer(user)
    line = _order_line(user, customer, item)
    invoice = Invoice.objects.create(
        invoice_no="IV-SNAP-1",
        customer=customer,
        issued_on=ISSUED_ON,
        period_from=date(2026, 11, 1),
        period_to=ISSUED_ON,
        subtotal=Decimal("15000"),
        tax=Decimal("1500"),
        total=Decimal("16500"),
    )
    row = InvoiceLine.objects.create(
        invoice=invoice,
        order_line=line,
        qty=Decimal("10"),
        unit_price=Decimal("15000"),
        price_basis=PRICE_BASIS_LUMP,
        amount=Decimal("15000"),
        last_shipped_at=SHIPPED_AT,
    )
    invoice.refresh_from_db()
    row.refresh_from_db()
    assert invoice.tax_rate == Decimal("10.00")
    assert (invoice.issuer_registration_no, invoice.issuer_address, invoice.issuer_phone, invoice.issuer_bank_account) == ("", "", "", "")
    assert invoice.customer_name == ""
    assert (row.part_no, row.item_name, row.unit, row.customer_order_no) == ("", "", "", "")


@pytest.mark.django_db
def test_migration_fills_existing_rows_with_current_values(user, item):
    """(b) migration の RunPython は、写しの空の今ある行を migration の時の今の値で埋める (今ある行には今の値しか入れられない)。

    migration の executor で前後を回す形は、試験の DB が transaction の中にあり組みにくいので、関数を django.apps.apps で直に呼ぶ。
    """
    customer = _customer(user)
    invoice = _invoice(user, customer, item, unit="本", customer_order_no="PO-SN-1")
    Invoice.objects.filter(pk=invoice.pk).update(
        issuer_registration_no="", issuer_address="", issuer_phone="", issuer_bank_account="", customer_name="", tax_rate=Decimal("0")
    )
    InvoiceLine.objects.filter(invoice=invoice).update(part_no="", item_name="", unit="", customer_order_no="")
    core_services.update_company(
        core_services.company(),
        {"registration_no": "T0000000000011", "address": "作り物県見本市1-1", "phone": "000-0000-0011", "bank_account": "作り物銀行 普通 0000011"},
        user,
    )

    importlib.import_module(MIGRATION).fill_snapshots(apps, None)

    invoice.refresh_from_db()
    assert invoice.issuer_registration_no == "T0000000000011"
    assert invoice.issuer_address == "作り物県見本市1-1"
    assert invoice.issuer_phone == "000-0000-0011"
    assert invoice.issuer_bank_account == "作り物銀行 普通 0000011"
    assert invoice.customer_name == "作り物の得意先"
    assert invoice.tax_rate == Decimal("10.00")
    row = invoice.lines.get()
    assert (row.part_no, row.item_name, row.unit, row.customer_order_no) == ("SN-100", "作り物の軸", "本", "PO-SN-1")


@pytest.mark.django_db
def test_detail_and_modal_do_not_list_snapshot_columns(logged_in, user, item):
    """(c) 請求書の詳細と小窓の項目の一覧に、写しの列 (「税率 (%)」「得意先の名前」など) を出さない (detail_hidden_fields)。得意先の項目は今のまま出る。"""
    invoice = _invoice(user, _customer(user), item)
    for name in ("billing:invoice_detail", "billing:invoice_modal"):
        response = logged_in.get(reverse(name, args=[invoice.pk]), **HTMX)
        assert response.status_code == 200, name
        labels = [str(field["label"]) for field in response.context["fields"]]
        assert "得意先" in labels, name
        for label in SNAPSHOT_LABELS:
            assert label not in labels, (name, label)
