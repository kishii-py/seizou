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
from seizou.billing.reports import invoice_context
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


# ---------------------------------------------------------------- 2 発行の時に写す

#: 発行の時の会社の情報 (作り物)
COMPANY_AT_ISSUE = {
    "registration_no": "T0000000000021",
    "address": "作り物県見本市2-1",
    "phone": "000-0000-0021",
    "bank_account": "作り物銀行 見本支店 普通 0000021",
}
#: 後で直した会社の情報 (作り物)
COMPANY_LATER = {
    "registration_no": "T0000000000022",
    "address": "作り物県別見本市2-2",
    "phone": "000-0000-0022",
    "bank_account": "作り物信用金庫 別支店 当座 0000022",
}


def _snapshot(invoice):
    invoice.refresh_from_db()
    row = invoice.lines.get()
    return (
        invoice.issuer_registration_no,
        invoice.issuer_address,
        invoice.issuer_phone,
        invoice.issuer_bank_account,
        invoice.customer_name,
        invoice.tax_rate,
        row.part_no,
        row.item_name,
        row.unit,
        row.customer_order_no,
    )


@pytest.fixture
def changed_later(user, item):
    """会社の情報を入れてから請求書を作り、その後に会社の情報・得意先の名前・部品の品番と品名と単位・得意先の注文番号を直した請求書。"""
    core_services.update_company(core_services.company(), COMPANY_AT_ISSUE, user)
    customer = _customer(user, name="作り物の得意先 発行の時")
    invoice = _invoice(user, customer, item, customer_order_no="PO-SN-21")
    order = invoice.lines.get().order_line.order

    core_services.update_company(core_services.company(), COMPANY_LATER, user)
    masters_services.update_partner(customer, {"name": "作り物の得意先 直した後"}, user)
    masters_services.update_item(item, {"part_no": "SN-200", "name": "作り物の直した軸", "unit": "本"}, user)
    orders_services.update_order(order, {"customer_order_no": "PO-SN-22"}, [{"id": order.lines.get().pk}], user)
    return invoice


@pytest.mark.django_db
def test_snapshot_keeps_values_at_issue_after_masters_change(changed_later):
    """(d) 請求書を作った後に会社の情報・得意先の名前・部品・得意先の注文番号を直しても、請求書と明細の写しは作った時の値のまま。"""
    # 直した値が今のマスターと会社の設定に入っている (直しが空振りでない)
    order_line = changed_later.lines.get().order_line
    customer = changed_later.customer
    customer.refresh_from_db()
    assert core_services.company().registration_no == "T0000000000022"
    assert customer.name == "作り物の得意先 直した後"
    assert (order_line.item.part_no, order_line.item.name, order_line.item.unit) == ("SN-200", "作り物の直した軸", "本")
    assert order_line.order.customer_order_no == "PO-SN-22"
    assert _snapshot(changed_later) == (
        "T0000000000021",
        "作り物県見本市2-1",
        "000-0000-0021",
        "作り物銀行 見本支店 普通 0000021",
        "作り物の得意先 発行の時",
        Decimal("10.00"),
        "SN-100",
        "作り物の軸",
        "個",
        "PO-SN-21",
    )


@pytest.mark.django_db
def test_unit_is_copied_from_order_line(user, item):
    """(e) 明細の単位の写しは、部品の単位でなく受注の明細の単位 (注文で合意した字)。"""
    invoice = _invoice(user, _customer(user), item, unit="箱")
    row = invoice.lines.get()
    assert item.unit == "個"
    assert row.unit == "箱"
    assert row.price_unit_text == "箱"


@pytest.mark.django_db
def test_tax_rate_is_copied_from_consumption_tax_rate(monkeypatch, user, item):
    """(f) 税率の写しは consumption_tax_rate() (TAX_RATE の 1 か所) から。率を変えた後に作った請求書だけが新しい率で、前の請求書は 10.00 のまま。"""
    before = _invoice(user, _customer(user, "C001", "作り物の得意先 一"), item)
    monkeypatch.setattr(billing_services, "TAX_RATE", Decimal("0.12"))
    after = _invoice(user, _customer(user, "C002", "作り物の得意先 二"), item)

    before.refresh_from_db()
    after.refresh_from_db()
    assert after.tax_rate == Decimal("12.00")
    assert after.subtotal == Decimal("15000") and after.tax == Decimal("1800")
    assert before.tax_rate == Decimal("10.00")
    assert before.tax == Decimal("1500")


# ---------------------------------------------------------------- 3 刷り直しは写しだけを読む


def _print(client, invoice):
    response = client.get(reverse("billing:invoice_print", args=[invoice.pk]))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_reprint_shows_values_at_issue(logged_in, changed_later):
    """(g) (d) の後の刷り直し (帳票の context と印刷用の HTML) は、作った時の会社の情報・得意先の名前・品番・品名・単位・注文番号・税率の字のまま。
    直した後の新しい字は出ない。"""
    context = invoice_context(changed_later)
    assert {key: context["company"][key] for key in ("registration_no", "address", "phone", "bank_account")} == COMPANY_AT_ISSUE
    assert context["customer"] == {"name": "作り物の得意先 発行の時"}
    line = context["lines"][0]
    assert (line["item_code"], line["item_name"], line["unit"], line["price_unit"], line["customer_order_no"]) == (
        "SN-100",
        "作り物の軸",
        "個",
        "個",
        "PO-SN-21",
    )
    assert context["tax_rate_percent"] == "10"

    body = _print(logged_in, changed_later)
    for text in (
        "登録番号 T0000000000021",
        "作り物県見本市2-1",
        "電話 000-0000-0021",
        "作り物の得意先 発行の時 御中",
        "SN-100",
        "作り物の軸",
        "<td>個</td>",
        "注文番号 PO-SN-21",
        "消費税 (10%)",
    ):
        assert text in body, text
    for word in COMPANY_AT_ISSUE["bank_account"].split():
        assert f"<span>{word}</span>" in body, word
    for text in (
        "T0000000000022",
        "作り物県別見本市2-2",
        "000-0000-0022",
        "作り物信用金庫",
        "作り物の得意先 直した後",
        "SN-200",
        "作り物の直した軸",
        "<td>本</td>",
        "PO-SN-22",
    ):
        assert text not in body, text


@pytest.mark.django_db
def test_registration_no_added_later_is_not_printed_on_earlier_invoice(logged_in, user, item):
    """(h) 登録番号を入れる前に作った請求書は、後で登録番号を入れても、刷り直しに登録番号の行が出ない (写しが空でも今の値で補わない)。"""
    invoice = _invoice(user, _customer(user), item)
    core_services.update_company(core_services.company(), {"registration_no": "T0000000000031"}, user)

    assert invoice_context(invoice)["company"]["registration_no"] == ""
    body = _print(logged_in, invoice)
    assert "<p>登録番号" not in body
    assert "T0000000000031" not in body


@pytest.mark.django_db
def test_detail_price_unit_text_reads_snapshot(logged_in, user, item):
    """(i) 請求書の詳細の画面の明細の単価の後ろの単位の字 (price_unit_text) も写しの字 (受注の明細から写した単位)。後で部品の単位を直しても変わらない。"""
    invoice = _invoice(user, _customer(user), item, unit="箱")
    masters_services.update_item(item, {"unit": "本"}, user)

    response = logged_in.get(reverse("billing:invoice_detail", args=[invoice.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert " / 箱</span>" in body
    assert " / 本</span>" not in body
    assert " / 個</span>" not in body


# ---------------------------------------------------------------- 4 作り直し (任意)


@pytest.mark.django_db
def test_reissue_copies_values_at_reissue_and_keeps_cancelled_snapshot(user, item):
    """(4) 作り直し (reissue_invoice) で作った新しい請求書は作り直した時の値を写し、取り消した前の請求書の写しは変わらない。"""
    core_services.update_company(core_services.company(), COMPANY_AT_ISSUE, user)
    customer = _customer(user, name="作り物の得意先 発行の時")
    first = _invoice(user, customer, item, customer_order_no="PO-SN-41")
    before = _snapshot(first)

    core_services.update_company(core_services.company(), COMPANY_LATER, user)
    masters_services.update_partner(customer, {"name": "作り物の得意先 直した後"}, user)
    masters_services.update_item(item, {"name": "作り物の直した軸"}, user)
    second = billing_services.reissue_invoice(first, user, issued_on=ISSUED_ON)

    first.refresh_from_db()
    assert first.status == Invoice.STATUS_CANCELLED
    assert _snapshot(first) == before
    assert _snapshot(second) == (
        "T0000000000022",
        "作り物県別見本市2-2",
        "000-0000-0022",
        "作り物信用金庫 別支店 当座 0000022",
        "作り物の得意先 直した後",
        Decimal("10.00"),
        "SN-100",
        "作り物の直した軸",
        "個",
        "PO-SN-41",
    )
