"""請求書に入った出荷を消す (依頼書 N403c。本人の決め 11-11 12:5x〜13:0x = 請求済みの出荷の直しと削除は止めない)。

請求書の明細は、発行した時の出荷の出荷番号と出荷日を写して持つ (billing.models.InvoiceLine の shipment_no・shipped_at)。
出荷を消すと明細の出荷の外部キーは空になり (SET_NULL)、明細は写しを持って残る (出荷を消した出荷の行)。
一式の行は最後の出荷日がある行 (InvoiceLine.is_lump) で見分け、出荷を消した出荷の行を一式の行と数えない。
請求書の外で受け取った入金が指す出荷は、今のまま消せない (本人の決め 11-11 20:1x)。
作り物の得意先・部品・受注だけを使い、全部 services で作る。相手への HTTP は偽物 (呼ばれたら落とす) にする。
"""

import importlib
from datetime import date
from decimal import Decimal

import pytest
from django.apps import apps
from django.db import IntegrityError, transaction
from django.urls import reverse

from seizou.billing import services
from seizou.billing.models import Invoice, InvoiceLine
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.timeline import reached_stages
from seizou.shipping import services as shipping_services
from seizou.shipping.models import Shipment

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
LATER_SHIPPED_AT = date(2026, 11, 6)
ISSUED_ON = date(2026, 11, 30)
MIGRATION = "seizou.billing.migrations.0008_n403c_invoiceline_shipment_copy"


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("出荷を消すときに相手への HTTP を呼んだ")

    monkeypatch.setattr("requests.post", refuse)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "DS-100", "name": "作り物の軸", "unit": "個"}, user)


def _line(user, customer, item, qty="10", price_basis="unit", unit_price="100"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal(unit_price), "price_basis": price_basis}],
        user,
        order_date=ORDER_DATE,
    )
    return order.lines.get()


def _ship(line, qty, user, shipped_at=SHIPPED_AT):
    return shipping_services.ship(line, Decimal(qty), shipped_at, user)


# ---------------------------------------------------------------- (a) 請求書に入った出荷を消せる


@pytest.mark.django_db
def test_invoiced_partial_shipment_can_be_deleted_and_line_stays(user, logged_in, customer, item):
    """(a) 請求書に入った出荷 (分納の、明細を出し終えていない出荷) を delete_shipment で消せる。請求書の明細は残り、出荷番号と出荷日の写し・
    数量・単価・金額は消す前と同じ。請求書の合計は変わらない。印刷用の画面と請求書の詳細に、消した出荷の出荷番号と出荷日が出る。"""
    line = _line(user, customer, item)
    shipment = _ship(line, "4", user)
    assert not shipment.finishes_line
    invoice = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)
    row = invoice.lines.get()
    before = (row.shipment_no, row.shipped_at, row.qty, row.unit_price, row.amount)
    totals = (invoice.subtotal, invoice.tax, invoice.total)
    number = shipment.shipment_no
    assert shipping_services.delete_refusal(shipment) == ""

    shipping_services.delete_shipment(shipment, user)

    assert not Shipment.objects.filter(pk=shipment.pk).exists()
    row.refresh_from_db()
    assert row.shipment_id is None
    assert row.is_shipment_deleted and not row.is_lump
    assert (row.shipment_no, row.shipped_at, row.qty, row.unit_price, row.amount) == before
    assert before[:2] == (number, SHIPPED_AT)
    assert (row.ref_no, row.shipped_on) == (number, SHIPPED_AT)
    invoice.refresh_from_db()
    assert (invoice.subtotal, invoice.tax, invoice.total) == totals
    assert str(row) == f"{invoice.invoice_no} {number}"

    printed = logged_in.get(reverse("billing:invoice_print", args=[invoice.pk]))
    assert printed.status_code == 200
    assert number in printed.content.decode()
    detail = logged_in.get(reverse("billing:invoice_detail", args=[invoice.pk]))
    assert detail.status_code == 200
    assert number in detail.content.decode() and "2026-11-05" in detail.content.decode()


@pytest.mark.django_db
def test_two_deleted_shipments_of_one_order_line_stay_on_one_invoice(user, customer, item):
    """(a) 同じ受注の明細の出荷 2 つを 1 枚の請求書に入れて、両方を消しても、明細は 2 行とも残る
    (1 枚 1 行の一式の行の制約は、最後の出荷日がある行だけを数える。出荷を消した出荷の行を一式の行と数えない)。"""
    line = _line(user, customer, item)
    first = _ship(line, "2", user)
    second = _ship(line, "3", user)
    invoice = services.create_invoice(customer, [first, second], user, issued_on=ISSUED_ON)

    shipping_services.delete_shipment(first, user)
    shipping_services.delete_shipment(second, user)

    rows = list(invoice.lines.order_by("pk"))
    assert [(row.shipment_id, row.shipment_no, row.qty) for row in rows] == [
        (None, first.shipment_no, Decimal("2")),
        (None, second.shipment_no, Decimal("3")),
    ]


@pytest.mark.django_db
def test_constraint_refuses_a_shipment_row_without_shipment_or_copy(user, customer, item):
    """(a) 出荷を指さず、出荷番号の写しも最後の出荷日も無い行は、表の制約で作れない (出荷の行でも一式の行でもない)。
    出荷を指さず出荷番号の写しがあり、最後の出荷日もある行も作れない (出荷を消した出荷の行は最後の出荷日が空)。"""
    line = _line(user, customer, item)
    shipment = _ship(line, "4", user)
    invoice = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)
    base = {
        "invoice": invoice,
        "shipment": None,
        "order_line": line,
        "qty": Decimal("1"),
        "unit_price": Decimal("100"),
        "price_basis": "lump",
        "amount": Decimal("100"),
    }
    for extra in ({"shipment_no": ""}, {"shipment_no": "S-X", "last_shipped_at": SHIPPED_AT}):
        with pytest.raises(IntegrityError), transaction.atomic():
            InvoiceLine.objects.create(**base, **extra)


# ---------------------------------------------------------------- (b) 写しは請求書を作った時に入る


@pytest.mark.django_db
def test_copy_is_written_when_the_invoice_is_issued(user, customer, item):
    """(b) 請求書を作った時に、出荷の行には出荷番号と出荷日の写しが入り、一式の行は空のまま。"""
    plain = _line(user, customer, item)
    shipment = _ship(plain, "4", user, LATER_SHIPPED_AT)
    lump = _line(user, customer, item, qty="300", price_basis="lump")
    _ship(lump, "60", user)
    orders_services.mark_completed(lump, user)
    lump.refresh_from_db()

    invoice = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON, lumps=[(lump, "1000")])

    shipment_row = invoice.lines.get(shipment=shipment)
    assert (shipment_row.shipment_no, shipment_row.shipped_at) == (shipment.shipment_no, LATER_SHIPPED_AT)
    assert not shipment_row.is_lump and not shipment_row.is_shipment_deleted
    lump_row = invoice.lines.get(order_line=lump)
    assert (lump_row.shipment_no, lump_row.shipped_at) == ("", None)
    assert lump_row.is_lump and not lump_row.is_shipment_deleted
    assert lump_row.ref_no == f"受注 {lump.order.order_no}-{lump.line_no} の一式"


@pytest.mark.django_db
def test_print_reads_the_copy_after_the_shipment_is_edited(user, logged_in, customer, item):
    """(b) 請求書を作った後に出荷の出荷日を直しても、明細の出荷日の欄は写し (渡した時の字) のまま。"""
    line = _line(user, customer, item)
    shipment = _ship(line, "4", user)
    invoice = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)

    shipping_services.update_shipment(shipment, Decimal("4"), LATER_SHIPPED_AT, user)

    row = invoice.lines.get()
    assert row.shipped_on == SHIPPED_AT
    assert row.shipment.shipped_at == LATER_SHIPPED_AT


# ---------------------------------------------------------------- (c) 作り直し


@pytest.mark.django_db
def test_reissue_leaves_out_deleted_shipment_lines_with_a_yellow_notice(user, customer, item):
    """(c) 出荷を消した請求書を作り直すと、消した出荷の明細は新しい請求書に入らず (出荷の記録が無い = R09)、
    知らせが黄で「出荷を消したので入れない明細」を並べる。"""
    line = _line(user, customer, item)
    kept = _ship(line, "4", user)
    gone = _ship(line, "3", user, LATER_SHIPPED_AT)
    invoice = services.create_invoice(customer, [kept, gone], user, issued_on=ISSUED_ON)
    shipping_services.delete_shipment(gone, user)

    plan = services.reissue_plan(invoice)
    assert [row.shipment_no for row in plan["deleted"]] == [gone.shipment_no]
    assert plan["shipments"] == [kept]
    assert plan["lumps"] == []

    created = services.reissue_invoice(invoice, user, issued_on=ISSUED_ON)

    assert [row.shipment_id for row in created.lines.all()] == [kept.pk]
    assert created.saved_level == "warning"
    assert created.saved_message == (
        f"{invoice.invoice_no} を取り消し、この期間の出荷 1 件で {created.invoice_no} を作りました。"
        f"出荷を消したので入れない明細: {gone.shipment_no}。"
    )
    invoice.refresh_from_db()
    assert invoice.status == Invoice.STATUS_CANCELLED
    # 取り消した請求書の明細は、写しを持ったまま残る。
    assert invoice.lines.get(shipment__isnull=True).shipment_no == gone.shipment_no


@pytest.mark.django_db
def test_reissue_without_deleted_lines_keeps_no_deleted_notice(user, customer, item):
    """(c) 出荷を消していない請求書の作り直しは、今のまま (deleted は空で、知らせに「出荷を消した」の字が無い)。"""
    line = _line(user, customer, item)
    shipment = _ship(line, "4", user)
    invoice = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)

    assert services.reissue_plan(invoice)["deleted"] == []
    created = services.reissue_invoice(invoice, user, issued_on=ISSUED_ON)

    assert "出荷を消した" not in created.saved_message
    assert getattr(created, "saved_level", "") != "warning"


# ---------------------------------------------------------------- (d) 一式の行の見分け


@pytest.mark.django_db
def test_deleted_shipment_line_is_not_read_as_a_lump_line(user, customer, item):
    """(d) 出荷の行で請求した一式の受注の明細の出荷を消しても、その明細を一式の行で請求した明細と読まない
    (_has_issued_lump_line・billed_by・lump_billed_shipments・unbilled_lumps・lumps_shipped_after・受注の段の「請求」)。"""
    lump = _line(user, customer, item, qty="300", price_basis="lump")
    first = _ship(lump, "60", user)
    second = _ship(lump, "40", user)
    plain = _line(user, customer, item)
    unit_shipment = _ship(plain, "4", user)
    invoice = services.create_invoice(customer, [first, second, unit_shipment], user, issued_on=ISSUED_ON)
    shipping_services.delete_shipment(first, user)
    shipping_services.delete_shipment(unit_shipment, user)
    third = _ship(lump, "30", user, LATER_SHIPPED_AT)
    later_plain = _ship(plain, "3", user, LATER_SHIPPED_AT)
    deleted = InvoiceLine.objects.filter(invoice=invoice, shipment__isnull=True)
    assert deleted.count() == 2 and not any(row.is_lump for row in deleted)

    lump.refresh_from_db()
    assert shipping_services._has_issued_lump_line(lump) is False
    assert shipping_services.billed_by(third) is None
    note = shipping_services.create_delivery_note(customer, [third], user)
    assert shipping_services.lump_billed_shipments(note) == []
    assert services.lumps_shipped_after(customer) == []
    # 新しい出荷 (third) にも明細を出し終えた出荷の印は付かない (一式の行がまだ無い。出荷の合計 70 は数量 300 に届かない)。
    third.refresh_from_db()
    assert third.finishes_line is False

    orders_services.mark_completed(lump, user)
    assert lump in services.unbilled_lumps(customer)

    plain.refresh_from_db()
    assert 7 not in reached_stages(plain)
    assert shipping_services.billed_by(later_plain) is None


@pytest.mark.django_db
def test_lump_line_is_still_read_as_lump(user, customer, item):
    """(d) 一式の行 (最後の出荷日がある行) は、今のまま一式の行と読む (同じ明細の出荷は billed_by が "lump")。"""
    lump = _line(user, customer, item, qty="300", price_basis="lump")
    shipment = _ship(lump, "60", user)
    orders_services.mark_completed(lump, user)
    lump.refresh_from_db()
    services.create_invoice(customer, [], user, issued_on=ISSUED_ON, lumps=[(lump, "1000")])

    assert shipping_services._has_issued_lump_line(lump) is True
    assert shipping_services.billed_by(shipment)[0] == "lump"
    assert lump not in services.unbilled_lumps(customer)


# ---------------------------------------------------------------- (e) 請求した時の数量が違う明細


@pytest.mark.django_db
def test_changed_lines_lists_deleted_shipment_and_detail_shows_it(user, logged_in, customer, item):
    """(e) 請求した時の数量が違う明細の関数に、消した出荷の行が今の数量 0 で並ぶ (明細の is_shipment_deleted が真)。
    請求書の詳細の黄色の 1 行にも「S-… (請求 4・出荷を消した)」の形で出る。"""
    line = _line(user, customer, item)
    shipment = _ship(line, "4", user)
    invoice = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)
    number = shipment.shipment_no
    shipping_services.delete_shipment(shipment, user)

    found = services.changed_lines(invoice)
    assert [(row.is_shipment_deleted, row.shipment_no, billed, now) for row, billed, now in found] == [
        (True, number, Decimal("4"), Decimal("0"))
    ]

    body = logged_in.get(reverse("billing:invoice_detail", args=[invoice.pk])).content.decode()
    assert "data-changed-lines" in body
    assert f"{number} (請求 4・出荷を消した)" in body


# ---------------------------------------------------------------- (f) 請求書の外の入金が指す出荷


@pytest.mark.django_db
def test_outside_payment_shipment_is_still_refused(user, customer, item):
    """(f) 請求書の外で受け取った入金が指す出荷は、今のまま delete_refusal で断る (入金を取り消しても断る)。"""
    line = _line(user, customer, item)
    shipment = _ship(line, "4", user)
    payment = services.record_outside_payment(shipment, SHIPPED_AT, Decimal("440"), user)
    text = (
        f"削除できません。出荷 {shipment.shipment_no} は請求書の外で受け取った入金の記録が指しています"
        " (入金を取り消しても、記録は出荷を指したまま残ります)。数や日付の誤りは「編集」で直してください。"
    )

    def assert_refused():
        assert shipping_services.delete_refusal(shipment) == text
        with pytest.raises(ValueError) as caught:
            shipping_services.delete_shipment(shipment, user)
        assert str(caught.value) == text
        assert Shipment.objects.filter(pk=shipment.pk).exists()

    assert_refused()
    services.cancel_payment(payment, user)
    assert_refused()


# ---------------------------------------------------------------- migration の写しの埋め方


@pytest.mark.django_db
def test_migration_fills_existing_shipment_rows_with_current_values(user, customer, item):
    """migration (0008) の RunPython は、今ある出荷の行の出荷番号と出荷日の写しを、migration の時の今の出荷の値で埋める。一式の行は空のまま。

    migration の executor で前後を回す形は、試験の DB が transaction の中にあり組みにくいので、関数を django.apps.apps で直に呼ぶ
    (0007 の写しの試験 tests/test_billing_invoice_snapshot.py と同じ形)。
    """
    plain = _line(user, customer, item)
    shipment = _ship(plain, "4", user)
    lump = _line(user, customer, item, qty="300", price_basis="lump")
    _ship(lump, "60", user)
    orders_services.mark_completed(lump, user)
    lump.refresh_from_db()
    invoice = services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON, lumps=[(lump, "1000")])
    InvoiceLine.objects.filter(invoice=invoice, shipment__isnull=False).update(shipment_no="", shipped_at=None)
    # 写しの空の行は、出荷を指すので制約に合う (migration の前からある行の形)。出荷日を直した後の今の値で埋まる。
    shipping_services.update_shipment(shipment, Decimal("4"), LATER_SHIPPED_AT, user)

    importlib.import_module(MIGRATION).fill_shipment_copies(apps, None)

    shipment_row = invoice.lines.get(shipment=shipment)
    assert (shipment_row.shipment_no, shipment_row.shipped_at) == (shipment.shipment_no, LATER_SHIPPED_AT)
    lump_row = invoice.lines.get(order_line=lump)
    assert (lump_row.shipment_no, lump_row.shipped_at) == ("", None)
