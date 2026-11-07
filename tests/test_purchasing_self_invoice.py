"""仕入明細 (purchasing.SelfInvoice) を作る・一覧・詳細のテスト。初見テスト R5 の候補 C111 その 2。

発注元が、seizou どうしで送った発注の受入をまとめて仕入明細を作る (中小企業共通 EDI の仕入明細の項目を元にした)。まだ送らない。
- (a) 明細の並び・金額の写し・消費税 (合計に 1 回)・税率・番号・状態・目的・締め期間・支払期日・相手の登録番号
- (a2) 支払期日が空の取引先・締日 20 の取引先の締め期間
- (b) 断る形 (同じ受入の 2 回目・ほかの取引先の受入・送った注文の行の無い発注の受入)
- (c) 候補の受入 (self_invoice_receipts)
- (d) 画面 (受入の一覧の link と列・作る画面・詳細・金額の閲覧の無い人)
- (d2) 支払期日が受領日から 60 日を超える黄の 1 行
- (e) EDI のコードの表の値
HTTP は no_http で止める。送った注文の行 (connect.Outbox) は直に作る。取引先・品目・番号は作り物。
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.connect.models import Connection, Outbox
from seizou.core import edi_codes
from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.models import SelfInvoice, SelfInvoiceLine

ORDER_DATE = date(2026, 10, 28)
DUE_NOTE = (
    "支払期日 (2027-01-31) が受領日 (2026-11-01) から 60 日を超えます。"
    "取適法の対象の取引では、支払期日の決まりに反するおそれがあります。取引先の支払の条件を確かめてください。"
)


@pytest.fixture
def no_http(monkeypatch):
    """HTTP を呼んだら落とす (仕入明細はまだ送らない。受入の検収も送らない)。"""

    def fail(*args, **kwargs):
        raise AssertionError("HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


def _partner(user, code="S001", **changes):
    data = {
        "code": code,
        "name": f"作り物の協力企業 {code}",
        "role": "subcontractor",
        "rounding": "round",
        "tax_mode": "total",
        "tax_rounding": "round",
        "closing_day": 31,
        "payment_month": "next",
        "payment_day": 31,
        "registration_no": "T0000000000001",
    }
    data.update(changes)
    return masters_services.create_partner(data, user)


@pytest.fixture
def partner(user):
    return _partner(user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _sent_order(partner, item, user, *, unit_price=Decimal("10.5"), qty=Decimal("100"), sent=True):
    """発注を作り (送らない)、送った注文の行を直に足す (seizou どうしで送った発注の形)。"""
    po = services.create_purchase_order(
        partner,
        [{"item": item, "qty": qty, "unit_price": unit_price}],
        user,
        order_date=ORDER_DATE,
        send=False,
    )
    if sent:
        # つながりは受ける向き (送る向きが無いので、受入の後の検収は HTTP を送らずに返事の欄に書く)
        connection = Connection.objects.filter(partner=partner, direction=Connection.DIRECTION_RECEIVE).first()
        if connection is None:
            connection = Connection.objects.create(
                partner=partner, url="", secret_env=f"SEIZOU_TEST_{partner.code}_SECRET", direction=Connection.DIRECTION_RECEIVE
            )
        Outbox.objects.create(purchase_order=po, connection=connection, message_id=uuid.uuid4(), response="受け取った")
    return po


def _receipts(po, user, days, qty=Decimal("10")):
    line = po.lines.get()
    return [services.receive(line, qty, day, user) for day in days]


@pytest.mark.django_db
def test_create_self_invoice_copies_receipts(user, partner, item, no_http):
    """(a) 明細 3 行が受入日の順・金額は受入の写し・消費税は合計に 1 回 (31.5 → 32)・締め期間と支払期日は billing の計算。"""
    from seizou.billing.services import closing_period, payment_due_date

    po = _sent_order(partner, item, user)
    receipts = _receipts(po, user, [date(2026, 11, 20), date(2026, 11, 2), date(2026, 11, 10)])
    assert [r.amount for r in receipts] == [Decimal("105.00")] * 3

    si = services.create_self_invoice(partner, receipts, user, issued_on=date(2026, 11, 30))

    si.refresh_from_db()
    lines = list(si.lines.order_by("line_no"))
    assert [line.received_on for line in lines] == [date(2026, 11, 2), date(2026, 11, 10), date(2026, 11, 20)]
    assert [line.line_no for line in lines] == [1, 2, 3]
    assert [line.amount for line in lines] == [Decimal("105.00")] * 3
    assert all(line.unit == "個" and line.unit_price == Decimal("10.5") and line.qty == Decimal("10") for line in lines)
    assert si.subtotal == Decimal("315")
    assert si.tax == Decimal("32")
    assert si.total == Decimal("347")
    assert si.tax_rate == Decimal("10")
    assert si.self_invoice_no == "SI-000001"
    assert si.status == SelfInvoice.STATUS_DRAFT
    assert si.purpose_code == "9"
    assert si.revision_no == 1
    assert (si.period_from, si.period_to) == (date(2026, 11, 1), date(2026, 11, 30))
    assert (si.period_from, si.period_to) == closing_period(partner, date(2026, 11, 20))
    assert si.due_on == date(2026, 12, 31) == payment_due_date(partner, si.period_to)
    assert si.supplier_registration_no == "T0000000000001"


@pytest.mark.django_db
def test_due_on_empty_and_closing_day_20(user, item, no_http):
    """(a2) 支払の月が空なら支払期日は空。締日 20 で受入日 11-10 と 11-25 なら締め期間は 11-21〜12-20。"""
    no_terms = _partner(user, "S002", payment_month="", payment_day=None)
    po = _sent_order(no_terms, item, user)
    si = services.create_self_invoice(no_terms, _receipts(po, user, [date(2026, 11, 2)]), user, issued_on=date(2026, 11, 30))
    assert si.due_on is None

    twenty = _partner(user, "S003", closing_day=20)
    po = _sent_order(twenty, item, user)
    si = services.create_self_invoice(
        twenty, _receipts(po, user, [date(2026, 11, 10), date(2026, 11, 25)]), user, issued_on=date(2026, 12, 20)
    )
    assert (si.period_from, si.period_to) == (date(2026, 11, 21), date(2026, 12, 20))
    assert si.lines.count() == 2


@pytest.mark.django_db
def test_create_self_invoice_refuses(user, partner, item, no_http):
    """(b) 同じ受入の 2 回目・ほかの取引先の受入・送った注文の行の無い発注の受入は「発行できません。」で、仕入明細が増えない。"""
    po = _sent_order(partner, item, user)
    receipts = _receipts(po, user, [date(2026, 11, 2)])
    first = services.create_self_invoice(partner, receipts, user, issued_on=date(2026, 11, 30))

    with pytest.raises(ValueError) as caught:
        services.create_self_invoice(partner, receipts, user, issued_on=date(2026, 11, 30))
    assert str(caught.value).startswith("発行できません。")
    assert first.self_invoice_no in str(caught.value)

    other = _partner(user, "S009")
    other_po = _sent_order(other, item, user)
    with pytest.raises(ValueError) as caught:
        services.create_self_invoice(partner, _receipts(other_po, user, [date(2026, 11, 3)]), user, issued_on=date(2026, 11, 30))
    assert str(caught.value).startswith("発行できません。")

    unsent = _sent_order(partner, item, user, sent=False)
    with pytest.raises(ValueError) as caught:
        services.create_self_invoice(partner, _receipts(unsent, user, [date(2026, 11, 4)]), user, issued_on=date(2026, 11, 30))
    assert str(caught.value).startswith("発行できません。")

    with pytest.raises(ValueError) as caught:
        services.create_self_invoice(partner, [], user, issued_on=date(2026, 11, 30))
    assert str(caught.value).startswith("発行できません。")
    assert SelfInvoice.objects.count() == 1


@pytest.mark.django_db
def test_self_invoice_receipts(user, partner, item, no_http):
    """(c) 取消でない仕入明細に入った受入と、送っていない発注の受入は出ない。取消の仕入明細だけに入っていた受入は出る。"""
    po = _sent_order(partner, item, user)
    used, free = _receipts(po, user, [date(2026, 11, 2), date(2026, 11, 3)])
    unsent = _sent_order(partner, item, user, sent=False)
    hidden = _receipts(unsent, user, [date(2026, 11, 4)])[0]
    si = services.create_self_invoice(partner, [used], user, issued_on=date(2026, 11, 30))

    pks = set(services.self_invoice_receipts().values_list("pk", flat=True))
    assert free.pk in pks
    assert used.pk not in pks and hidden.pk not in pks
    assert set(services.self_invoice_receipts(partner).values_list("pk", flat=True)) == {free.pk}

    SelfInvoice.objects.filter(pk=si.pk).update(status=SelfInvoice.STATUS_CANCELLED)
    assert used.pk in set(services.self_invoice_receipts().values_list("pk", flat=True))


@pytest.mark.django_db
def test_screens(client_logged_in, user, partner, item, no_http):
    """(d) 受入の一覧の link と列・作る画面の行と保存のボタン・POST で詳細へ・詳細の表と合計の枠と締め期間と支払期日。"""
    po = _sent_order(partner, item, user)
    receipts = _receipts(po, user, [date(2026, 11, 2), date(2026, 11, 10)])

    body = client_logged_in.get(reverse("purchasing:receipt_list")).content.decode()
    assert reverse("purchasing:selfinvoice_list") in body
    assert "<th" in body and "仕入明細" in body

    body = client_logged_in.get(reverse("purchasing:selfinvoice_create")).content.decode()
    assert f'data-partner="{partner.pk}"' in body
    assert "data-label-pick-amount=" in body
    assert "の仕入明細を作る" in body and "data-submit-label=" in body

    response = client_logged_in.post(
        reverse("purchasing:selfinvoice_create"),
        {"partner": partner.pk, "issued_on": "2026-11-30", "receipts": [r.pk for r in receipts]},
    )
    si = SelfInvoice.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("purchasing:selfinvoice_detail", args=[si.pk])

    body = client_logged_in.get(response["Location"]).content.decode()
    assert "受入の金額の合計" in body and "差はありません" in body
    assert "2026-11-30" in body and "2026-12-31" in body
    assert f"{po.po_no}-1" in body
    assert "取引先の登録番号が空です。" not in body

    rows = client_logged_in.get(reverse("purchasing:receipt_list")).content.decode()
    assert si.self_invoice_no in rows

    listing = client_logged_in.get(reverse("purchasing:selfinvoice_list")).content.decode()
    assert si.self_invoice_no in listing and "答え待ち" in listing and "確かめ中" in listing


@pytest.mark.django_db
def test_detail_registration_alert_and_hidden_money(settings, client, client_logged_in, user, item, no_http):
    """(d) 取引先の登録番号が空なら黄色の 1 行。金額の閲覧の無い人 (access_control の money = on) には作る画面と詳細に金額の列が出ない。"""
    from django.contrib.auth import get_user_model

    from seizou.accounts import services as accounts_services

    partner = _partner(user, "S004", registration_no="")
    po = _sent_order(partner, item, user)
    si = services.create_self_invoice(partner, _receipts(po, user, [date(2026, 11, 2)]), user, issued_on=date(2026, 11, 30))
    body = client_logged_in.get(reverse("purchasing:selfinvoice_detail", args=[si.pk])).content.decode()
    assert "取引先の登録番号が空です。" in body
    assert "受入の金額の合計" in body

    other = _sent_order(partner, item, user)
    _receipts(other, user, [date(2026, 11, 3)])
    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "on"}}
    accounts_services.issue_account("genba", "kumo-tests-2611", [], [], {}, None)
    client.force_login(get_user_model().objects.get(username="genba"))
    body = client.get(reverse("purchasing:selfinvoice_create")).content.decode()
    assert "金額 (税抜)" not in body and "data-label-pick-amount=" not in body
    body = client.get(reverse("purchasing:selfinvoice_detail", args=[si.pk])).content.decode()
    assert "受入の金額の合計" not in body and "提案の単価" not in body


@pytest.mark.django_db
def test_due_date_note(client_logged_in, user, item, no_http):
    """(d2) 支払の月「翌々月」で受入日 11-01 なら支払期日 2027-01-31 で黄の 1 行。「翌月」(60 日ちょうど) と支払期日の空には出ない。"""
    late = _partner(user, "S005", payment_month="after_next")
    po = _sent_order(late, item, user)
    si = services.create_self_invoice(late, _receipts(po, user, [date(2026, 11, 1)]), user, issued_on=date(2026, 11, 30))
    assert si.due_on == date(2027, 1, 31)
    assert services.due_date_note(si) == DUE_NOTE
    body = client_logged_in.get(reverse("purchasing:selfinvoice_detail", args=[si.pk])).content.decode()
    assert DUE_NOTE in body

    ok = _partner(user, "S006")
    po = _sent_order(ok, item, user)
    si = services.create_self_invoice(ok, _receipts(po, user, [date(2026, 11, 1)]), user, issued_on=date(2026, 11, 30))
    assert si.due_on == date(2026, 12, 31)
    assert services.due_date_note(si) == ""
    assert "60 日を超えます" not in client_logged_in.get(reverse("purchasing:selfinvoice_detail", args=[si.pk])).content.decode()

    empty = _partner(user, "S007", payment_month="", payment_day=None)
    po = _sent_order(empty, item, user)
    si = services.create_self_invoice(empty, _receipts(po, user, [date(2026, 11, 1)]), user, issued_on=date(2026, 11, 30))
    assert services.due_date_note(si) == ""


def test_edi_codes():
    """(e) EDI のコードの表の値 (全部が字)。"""
    assert {code for code, _ in edi_codes.SI_REPLY_LINE_TYPES} == {"AP", "UV", "RE", "AC"}
    assert {code for code, _ in edi_codes.SI_REPLY_TYPES} == {"AP", "CA", "RE", "AC"}
    assert {code for code, _ in edi_codes.SI_REASON_CHOICES} == {"101", "102", "103", "104", "105", "191", "199"}
    assert {code for code, _ in edi_codes.SI_DOC_CHOICES} == {"1", "1C", "1R", "1CR"}
    assert edi_codes.TAX_CATEGORY_STANDARD == "S"
    assert {code for code, _ in edi_codes.PURPOSE_CHOICES} == {"9", "4", "1"}
    for table in (edi_codes.SI_REPLY_LINE_TYPES, edi_codes.SI_REPLY_TYPES, edi_codes.SI_REASON_CHOICES, edi_codes.SI_DOC_CHOICES):
        assert all(isinstance(code, str) for code, _ in table)


def test_receipt_is_foreign_key():
    """受入は外部キー (取り消した仕入明細の受入をまた入れられる = 決め C111-3)。"""
    from django.db import models

    field = SelfInvoiceLine._meta.get_field("receipt")
    assert isinstance(field, models.ForeignKey) and not isinstance(field, models.OneToOneField)
