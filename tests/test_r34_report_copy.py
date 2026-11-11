"""R34: 請求書と納品書を発行したら、発注を作るか変えたら (版ごと)、その時の帳票の写しを 1 つ残す。写しは後から直さず消さない。

- (a) 請求書: 発行で写しが 1 行。作り直しでは取り消した請求書の写しが残り、新しい請求書に 1 行。取消では増えない。
- (b) 納品書: 受注の単価と部品マスターの品名を後で直すと、印刷用の画面は今の字になり、写しは発行の時の字のまま。取り消して作り直すと写しは 2 行。
- (c) 発注書: 第 1 版の写し。変更で版を上げると第 2 版の写しが増え、第 1 版の写しは前の数の字のまま。変えた所が無い変更と取消では増えない。
- (d) 写しを描けなくても発行は止まらない (写しだけが戻り、黄色の知らせと logging の WARNING)。
- (d2) 発行が戻れば写しも戻る。
- (e) 発行の処理では PDF を作らない。
- (f) 写しは直さず消さない (元の記録とマスターと会社の情報を直しても字は同じ。写しを書く公開の関数は keep_report_copy だけ)。
今日は 2026-11-11 に固定する (締めの一括の締める日を見る所だけが今日を読む)。取引先・部品・会社の値は作り物だけ。
相手への HTTP は偽の物に差し替える (外へは出ない)。
"""

import inspect
import logging
import re
from datetime import date
from decimal import Decimal

import pytest
from django.db import transaction
from django.template import TemplateSyntaxError
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.billing.models import Invoice
from seizou.connect import services as connect_services
from seizou.core import services as core_services
from seizou.core.models import ChangeLog, ReportCopy
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import PurchaseOrder
from seizou.shipping import services as shipping_services
from seizou.shipping.models import DeliveryNote
from tests.rules_helpers import rules_md_text
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

TODAY = date(2026, 11, 11)
ORDER_DATE = date(2026, 10, 1)
SHIPPED_AT = date(2026, 10, 10)
ISSUED_ON = date(2026, 10, 31)
ENV_NAME = "SEIZOU_PARTNER_TESTR34_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"


def test_rules_md_has_r34():
    assert "その時の帳票の写しを 1 つ残す" in rules_md_text("R34")


# ---------------------------------------------------------------- 道具


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-340", "name": "作り物の軸", "unit": "個"}, user)


def _customer(user, code="C001", **extra):
    return masters_services.create_partner({"code": code, "name": f"作り物の得意先 {code}", "role": "customer", **extra}, user)


def _line(user, customer, item, qty="50", unit_price="1650"):
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal(qty), "unit_price": Decimal(unit_price)}], user, order_date=ORDER_DATE
    )
    return order.lines.get()


def _ship(user, line, qty, shipped_at=SHIPPED_AT):
    shipment = shipping_services.ship(line, Decimal(qty), shipped_at, user, send=False)
    shipment.refresh_from_db()
    return shipment


def _copies(obj):
    return list(core_services.report_copies(obj))


def _yen(value):
    return f"¥{int(value):,}"


def _break_render(monkeypatch):
    """帳票を描く所 (core.reports.render_html) を、会社の帳票のテンプレートの誤りの代わりに例外を出す物に差し替える。"""

    def fail(template, context):
        raise TemplateSyntaxError("作り物のテンプレートの誤り")

    monkeypatch.setattr("seizou.core.reports.render_html", fail)


def _counted(make):
    """make() を呼び、(返した物, 変更の履歴の増えた行の数) を返す。"""
    before = ChangeLog.objects.count()
    result = make()
    return result, ChangeLog.objects.count() - before


def _copy_warnings(caplog):
    return [r for r in caplog.records if r.name == "seizou.reports" and r.levelno == logging.WARNING]


@pytest.fixture
def fake_post(monkeypatch):
    post = FakePost()
    monkeypatch.setattr("requests.post", post)
    return post


@pytest.fixture
def no_http(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


def _subcontractor(user, code, *, connected=False, monkeypatch=None):
    partner = masters_services.create_partner({"code": code, "name": f"作り物の協力企業 {code}", "role": "subcontractor"}, user)
    if connected:
        monkeypatch.setenv(ENV_NAME, _new_secret())
        connect_services.register_connection(partner, PARTNER_URL, ENV_NAME, "send", user)
    return partner


def _purchase_order(user, partner, item, qty="37"):
    return purchasing_services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("250"), "due_date": date(2026, 11, 20)}],
        user,
        order_date=ORDER_DATE,
    )


def _change_qty(user, order, qty):
    line = order.lines.get()
    return purchasing_services.change_purchase_order(order, {}, [{"line": line, "qty": Decimal(qty)}], user)


# ---------------------------------------------------------------- (a) 請求書


@pytest.mark.django_db
def test_a_invoice_issue_reissue_and_cancel(user, item):
    customer = _customer(user)
    line = _line(user, customer, item, qty="10", unit_price="1500")
    shipment = _ship(user, line, "10")

    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)

    [copy] = _copies(invoice)
    assert (copy.title, copy.object_text, copy.model, copy.object_pk, copy.revision) == (
        "請求書",
        invoice.invoice_no,
        "billing.invoice",
        invoice.pk,
        1,
    )
    assert copy.template == "billing/invoice.html"
    assert invoice.invoice_no in copy.html
    assert "¥1,500 / 個" in copy.html
    assert _yen(invoice.total) in copy.html
    first_html = copy.html

    remade = billing_services.reissue_invoice(invoice, user, issued_on=ISSUED_ON)

    invoice.refresh_from_db()
    assert invoice.status == Invoice.STATUS_CANCELLED
    assert [row.html for row in _copies(invoice)] == [first_html]  # 取り消した請求書の写しはそのまま
    [new_copy] = _copies(remade)
    assert new_copy.object_text == remade.invoice_no
    assert remade.invoice_no in new_copy.html

    count = ReportCopy.objects.count()
    billing_services.cancel_invoice(remade, user)
    assert ReportCopy.objects.count() == count


@pytest.mark.django_db
def test_a_closing_bulk_keeps_a_copy_per_invoice(user, item, monkeypatch):
    monkeypatch.setattr("seizou.billing.services.timezone.localdate", lambda *args, **kwargs: TODAY)
    first = _customer(user, "C001", closing_day=31, payment_month="next", payment_day=31)
    second = _customer(user, "C002", closing_day=31, payment_month="next", payment_day=31)
    _ship(user, _line(user, first, item), "4")
    _ship(user, _line(user, second, item), "6")

    created = billing_services.create_closing_invoices(ISSUED_ON, user, issued_on=ISSUED_ON)
    # 写しが残った時は、写しの知らせを足さない (作らなかった得意先が無ければ緑のまま)
    assert created.copy_failed == [] and "写しを残せなかった" not in created.saved_note and created.saved_level == ""

    assert len(created) == 2
    for invoice in created:
        [copy] = _copies(invoice)
        assert copy.object_text == invoice.invoice_no


# ---------------------------------------------------------------- (b) 納品書


@pytest.mark.django_db
def test_b_delivery_note_copy_keeps_the_price_and_name_of_the_issue(client, user, item):
    customer = _customer(user)
    line = _line(user, customer, item)
    shipment = _ship(user, line, "50")
    note = shipping_services.create_delivery_note(customer, [shipment], user, issued_on=SHIPPED_AT)

    [copy] = _copies(note)
    assert (copy.title, copy.object_text, copy.revision) == ("納品書", note.note_no, 1)
    assert copy.template == "shipping/delivery_note.html"
    first_html = copy.html
    assert "¥1,650 / 個" in first_html

    # 請求書は作らずに受注の単価を直す (納品書は請求書の写しを読まないので、刷り直しは今の単価になる)
    orders_services.update_order(line.order, {}, [{"id": line.pk, "unit_price": Decimal("1700")}], user)
    client.force_login(user)
    body = client.get(reverse("shipping:deliverynote_print", args=[note.pk])).content.decode()
    assert "¥1,700" in body
    [copy] = _copies(note)
    assert "¥1,650" in copy.html
    assert "¥1,700" not in copy.html

    masters_services.update_item(item, {"name": "作り物の新しい軸"}, user)
    body = client.get(reverse("shipping:deliverynote_print", args=[note.pk])).content.decode()
    assert "作り物の新しい軸" in body
    [copy] = _copies(note)
    assert "作り物の新しい軸" not in copy.html
    assert "作り物の軸" in copy.html
    assert copy.html == first_html

    shipping_services.cancel_delivery_note(note, user)
    remade = shipping_services.create_delivery_note(customer, [shipment], user, issued_on=SHIPPED_AT)

    assert [row.html for row in _copies(note)] == [first_html]
    [new_copy] = _copies(remade)
    assert new_copy.object_text == remade.note_no
    assert "¥1,700" in new_copy.html
    assert "作り物の新しい軸" in new_copy.html


# ---------------------------------------------------------------- (c) 発注書


@pytest.mark.django_db
def test_c_purchase_order_without_connection_keeps_revision_1(user, item, no_http):
    partner = _subcontractor(user, "S001")

    order = _purchase_order(user, partner, item)

    assert order.status == PurchaseOrder.STATUS_DRAFT
    [copy] = _copies(order)
    assert (copy.title, copy.object_text, copy.revision, copy.model) == ("発注書", order.po_no, 1, "purchasing.purchaseorder")
    assert copy.template == "purchasing/purchaseorder.html"
    assert order.po_no in copy.html
    assert _yen(Decimal("37") * 250) in copy.html


@pytest.mark.django_db
def test_c_changed_order_keeps_one_copy_per_revision(user, item, fake_post, monkeypatch):
    partner = _subcontractor(user, "S002", connected=True, monkeypatch=monkeypatch)
    order = _purchase_order(user, partner, item, qty="37")
    assert order.status == PurchaseOrder.STATUS_SENT
    [first] = _copies(order)
    first_html = first.html

    changed = _change_qty(user, order, "41")

    assert changed.revision_no == 2
    old, new = _copies(changed)
    assert (old.revision, new.revision) == (1, 2)
    assert old.html == first_html
    assert _yen(Decimal("37") * 250) in old.html
    assert _yen(Decimal("41") * 250) not in old.html
    assert _yen(Decimal("41") * 250) in new.html
    assert str(new).endswith(f"{changed.po_no} 発注書 第 2 版")

    unchanged = purchasing_services.change_purchase_order(changed, {}, [{"line": changed.lines.get()}], user)
    assert unchanged.revision_no == 2
    assert len(_copies(changed)) == 2

    cancelled = purchasing_services.cancel_purchase_order(changed, user)
    assert cancelled.status == PurchaseOrder.STATUS_CANCELLED
    assert cancelled.revision_no == 3
    assert len(_copies(changed)) == 2


# ---------------------------------------------------------------- (d) 写しを描けなくても発行は止まらない


@pytest.mark.django_db
def test_d_invoice_is_issued_and_warns_when_the_copy_fails(user, item, monkeypatch, caplog):
    def setup(code):
        customer = _customer(user, code)
        line = _line(user, customer, item)
        # 同じ期間に 2 つ出荷し、1 つだけを請求書に入れる (請求書の期間の知らせの字が saved_note に付く)
        return customer, _ship(user, line, "10"), _ship(user, line, "20")

    ok_customer, ok_shipment, ok_rest = setup("C001")
    ok, ok_logs = _counted(lambda: billing_services.create_invoice(ok_customer, [ok_shipment], user, issued_on=ISSUED_ON))
    bad_customer, bad_shipment, bad_rest = setup("C002")
    _break_render(monkeypatch)
    caplog.set_level(logging.WARNING, logger="seizou.reports")

    bad, bad_logs = _counted(lambda: billing_services.create_invoice(bad_customer, [bad_shipment], user, issued_on=ISSUED_ON))

    assert Invoice.objects.filter(pk=bad.pk, status=Invoice.STATUS_ISSUED).exists()
    assert bad.lines.count() == ok.lines.count() == 1
    assert bad_logs == ok_logs
    assert _copies(bad) == []
    assert bad.saved_level == "warning"
    assert f"まだ請求書に入っていない物が 1 件あります: {bad_rest.shipment_no}。" in bad.saved_note  # 前の字も残る
    assert bad.saved_note.endswith(core_services.COPY_FAILED_NOTE)
    [warning] = _copy_warnings(caplog)
    assert warning.exc_info is not None
    assert bad.invoice_no in warning.getMessage()

    # 作り直し (描けた時の請求書を取り消し、期間の残りの出荷も入れて作る) も止まらない
    remade, remade_logs = _counted(lambda: billing_services.reissue_invoice(ok, user, issued_on=ISSUED_ON))

    assert remade.lines.count() == 2
    assert _copies(remade) == []
    assert len(_copies(ok)) == 1  # 描けた時の写しは残る
    assert remade.saved_level == "warning"
    assert remade.saved_message.startswith(f"{ok.invoice_no} を取り消し、")
    assert remade.saved_note.endswith(core_services.COPY_FAILED_NOTE)
    assert len(_copy_warnings(caplog)) == 2
    assert remade_logs == 1 + 1 + 2  # 取り消した請求書・新しい請求書・明細 2 行 (写しは履歴に書かない)


@pytest.mark.django_db
def test_d_closing_bulk_is_not_stopped_when_the_copy_fails(user, item, monkeypatch, caplog):
    monkeypatch.setattr("seizou.billing.services.timezone.localdate", lambda *args, **kwargs: TODAY)
    customer = _customer(user, "C001", closing_day=31, payment_month="next", payment_day=31)
    _ship(user, _line(user, customer, item), "4")
    _break_render(monkeypatch)
    caplog.set_level(logging.WARNING, logger="seizou.reports")

    created = billing_services.create_closing_invoices(ISSUED_ON, user, issued_on=ISSUED_ON)

    [invoice] = created
    assert Invoice.objects.filter(pk=invoice.pk).exists()
    assert _copies(invoice) == []
    assert len(_copy_warnings(caplog)) == 1
    # 一括の結果の知らせも黄になり、写しを残せなかった請求書の番号を言う (描けなければ黄色の知らせで伝える)
    assert created.saved_level == "warning"
    assert f"発行の時の写しを残せなかった請求書 1 枚: {invoice.invoice_no}。会社の帳票のテンプレートを確かめてください。" in created.saved_note


@pytest.mark.django_db
def test_d_delivery_note_is_issued_and_warns_when_the_copy_fails(user, item, monkeypatch, caplog):
    def setup(code):
        customer = _customer(user, code)
        return customer, _ship(user, _line(user, customer, item), "50")

    ok_customer, ok_shipment = setup("C001")
    ok, ok_logs = _counted(
        lambda: shipping_services.create_delivery_note(ok_customer, [ok_shipment], user, issued_on=SHIPPED_AT)
    )
    bad_customer, bad_shipment = setup("C002")
    _break_render(monkeypatch)
    caplog.set_level(logging.WARNING, logger="seizou.reports")

    bad, bad_logs = _counted(
        lambda: shipping_services.create_delivery_note(bad_customer, [bad_shipment], user, issued_on=SHIPPED_AT)
    )

    assert DeliveryNote.objects.filter(pk=bad.pk, status=DeliveryNote.STATUS_ISSUED).exists()
    bad_shipment.refresh_from_db()
    assert bad_shipment.delivery_note_id == bad.pk
    assert bad_logs == ok_logs
    assert len(_copies(ok)) == 1
    assert _copies(bad) == []
    assert bad.saved_level == "warning"
    assert bad.saved_note == core_services.COPY_FAILED_NOTE
    [warning] = _copy_warnings(caplog)
    assert warning.exc_info is not None
    assert bad.note_no in warning.getMessage()


@pytest.mark.django_db
def test_d_purchase_order_is_saved_and_warns_when_the_copy_fails(user, item, monkeypatch, caplog, fake_post):
    plain_ok, plain_bad = _subcontractor(user, "S001"), _subcontractor(user, "S002")
    connected = _subcontractor(user, "S003", connected=True, monkeypatch=monkeypatch)
    ok, ok_logs = _counted(lambda: _purchase_order(user, plain_ok, item))
    sent_ok = _purchase_order(user, connected, item)
    sent_bad = _purchase_order(user, connected, item)
    changed_ok, changed_ok_logs = _counted(lambda: _change_qty(user, sent_ok, "41"))
    _break_render(monkeypatch)
    caplog.set_level(logging.WARNING, logger="seizou.reports")

    # 作成 (送らない道)
    bad, bad_logs = _counted(lambda: _purchase_order(user, plain_bad, item))

    assert PurchaseOrder.objects.filter(pk=bad.pk).exists()
    assert bad.lines.count() == ok.lines.count() == 1
    assert bad_logs == ok_logs
    assert _copies(bad) == []
    assert bad.saved_level == "warning"
    assert bad.saved_note == core_services.COPY_FAILED_NOTE

    # 作成 (送る道)
    sent = _purchase_order(user, connected, item)

    assert sent.status == PurchaseOrder.STATUS_SENT
    assert _copies(sent) == []
    assert sent.saved_level == "warning"
    assert sent.saved_note == core_services.COPY_FAILED_NOTE

    # 変更 (第 2 版)
    changed_bad, changed_bad_logs = _counted(lambda: _change_qty(user, sent_bad, "41"))

    assert changed_bad.revision_no == 2
    assert changed_bad.lines.get().qty == Decimal("41")
    assert changed_bad_logs == changed_ok_logs
    assert [row.revision for row in _copies(changed_bad)] == [1]
    assert changed_bad.saved_level == "warning"
    assert changed_bad.saved_message == "相手に第 2 版の変更を送りました。"
    assert changed_bad.saved_note == core_services.COPY_FAILED_NOTE
    assert len(_copy_warnings(caplog)) == 3


# ---------------------------------------------------------------- (d2) 発行が戻れば写しも戻る


@pytest.mark.django_db
def test_d2_copy_is_rolled_back_with_the_issue(user, item):
    customer = _customer(user)
    shipment = _ship(user, _line(user, customer, item), "10")

    with pytest.raises(RuntimeError, match="作り物の後の誤り"):
        with transaction.atomic():
            invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)
            assert len(_copies(invoice)) == 1
            raise RuntimeError("作り物の後の誤り")

    assert not Invoice.objects.exists()
    assert not ReportCopy.objects.exists()


# ---------------------------------------------------------------- (e) 発行の処理では PDF を作らない


@pytest.mark.django_db
def test_e_issuing_does_not_make_a_pdf(user, item, monkeypatch, no_http):
    def fail(*args, **kwargs):
        raise AssertionError("発行の処理で PDF を作った")

    monkeypatch.setattr("seizou.core.reports.render_pdf", fail)
    customer = _customer(user)
    line = _line(user, customer, item)
    note = shipping_services.create_delivery_note(customer, [_ship(user, line, "10")], user, issued_on=SHIPPED_AT)
    invoice = billing_services.create_invoice(customer, [_ship(user, line, "5")], user, issued_on=ISSUED_ON)
    order = _purchase_order(user, _subcontractor(user, "S001"), item)

    assert [len(_copies(obj)) for obj in (invoice, note, order)] == [1, 1, 1]


# ---------------------------------------------------------------- (f) 写しは直さず消さない


@pytest.mark.django_db
def test_f_copies_do_not_follow_later_changes(user, item, monkeypatch, fake_post):
    customer = _customer(user)
    line = _line(user, customer, item)
    note = shipping_services.create_delivery_note(customer, [_ship(user, line, "10")], user, issued_on=SHIPPED_AT)
    invoice = billing_services.create_invoice(customer, [_ship(user, line, "5")], user, issued_on=ISSUED_ON)
    order = _purchase_order(user, _subcontractor(user, "S001", connected=True, monkeypatch=monkeypatch), item)
    order = _change_qty(user, order, "41")
    before = {row.pk: row.html for row in ReportCopy.objects.all()}
    assert len(before) == 4

    core_services.update_company(core_services.company(), {"address": "作り物の県 作り物の市 1-2-3"}, user)
    masters_services.update_partner(customer, {"name": "作り物の新しい得意先"}, user)
    masters_services.update_item(item, {"name": "作り物の新しい軸"}, user)
    orders_services.update_order(line.order, {}, [{"id": line.pk, "unit_price": Decimal("1700")}], user)

    assert {row.pk: row.html for row in ReportCopy.objects.all()} == before
    assert all("作り物の新しい" not in html for html in before.values())
    assert {row.object_pk for row in _copies(invoice) + _copies(note) + _copies(order)} == {invoice.pk, note.pk, order.pk}


def test_f_core_services_has_no_way_to_change_or_remove_copies():
    names = {
        name
        for name, function in inspect.getmembers(core_services, inspect.isfunction)
        if function.__module__ == core_services.__name__ and not name.startswith("_")
    }
    touching = {name for name in names if "ReportCopy" in inspect.getsource(getattr(core_services, name))}
    writes = re.compile(r"\.(create|update|update_or_create|get_or_create|delete|save|bulk_create|bulk_update)\(")
    writers = {name for name in touching if writes.search(inspect.getsource(getattr(core_services, name)))}
    assert touching == {"keep_report_copy", "report_copies"}
    assert writers == {"keep_report_copy"}
