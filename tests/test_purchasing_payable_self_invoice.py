"""支払の見込みを、相手が仕入明細で承諾した明細は仕入明細の金額で数え、確かめ中の金額を別の行に出すテスト。初見テスト R5 の候補 C111 その 7 (決め C111-1)。

- (a) 承諾 (AP)・直した内容の承諾 (AC。金額 95)・確認できない (RE) の明細と、仕入明細に入っていない受入: 見込みは 105 + 95 + 105 + 105 = 410、確かめ中は 105。
  ダッシュボードの「今月の支払の見込み」も 410
- (b) 仕入明細を取消にすると、見込みは受入の金額だけ (420)・確かめ中は 0
- (c) 画面: 受入の一覧の上の表に「仕入明細で確かめ中の金額」の行。確かめ中が 0 なら行が無い。金額の閲覧の無い人には表ごと出ない
明細の金額と答えは表に直に書く (提案を受け入れて直す画面は別の その)。HTTP は no_http で止め、送った注文の行 (connect.Outbox) は直に作る。取引先・品目は作り物。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.core import dashboard
from seizou.purchasing import services
from seizou.purchasing.models import SelfInvoice, SelfInvoiceLine
from tests.test_purchasing_self_invoice import _partner, _receipts, _sent_order, no_http  # noqa: F401

TODAY = date(2026, 11, 20)


@pytest.fixture
def item(user):
    from seizou.masters import services as masters_services

    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def setup(user, item, no_http):  # noqa: F811
    """同じ月の受入 4 件 (105 ずつ)。3 件で仕入明細を作り、明細 1 = AP・明細 2 = AC (金額 95)・明細 3 = RE にする。"""
    partner = _partner(user)
    po = _sent_order(partner, item, user)
    receipts = _receipts(po, user, [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)])
    si = services.create_self_invoice(partner, receipts[:3], user, issued_on=date(2026, 11, 30))
    lines = {line.line_no: line for line in si.lines.all()}
    SelfInvoiceLine.objects.filter(pk=lines[1].pk).update(reply_type="AP")
    SelfInvoiceLine.objects.filter(pk=lines[2].pk).update(reply_type="AC", amount=Decimal("95"))
    SelfInvoiceLine.objects.filter(pk=lines[3].pk).update(reply_type="RE", reply_reason_code="103")
    return partner, si


@pytest.mark.django_db
def test_payable_counts_confirmed_lines(setup):
    """(a) 見込みは 410、確かめ中は 105。ダッシュボードのカードも 410。"""
    _partner_row, _si = setup
    result = services.payable_by_partner(today=TODAY)
    assert result["totals"]["this_month"] == Decimal("410")
    assert result["checking"] == {"last_month": Decimal("0"), "this_month": Decimal("105")}
    assert dashboard.payable_this_month(TODAY) == Decimal("410")
    assert dashboard.payable_month_compare(TODAY)["this"] == Decimal("410")


@pytest.mark.django_db
def test_cancelled_self_invoice_is_ignored(setup):
    """(b) 取消の仕入明細の明細は見ない: 見込みは受入の金額だけ (420)、確かめ中は 0。"""
    _partner_row, si = setup
    SelfInvoice.objects.filter(pk=si.pk).update(status=SelfInvoice.STATUS_CANCELLED)
    result = services.payable_by_partner(today=TODAY)
    assert result["totals"]["this_month"] == Decimal("420")
    assert result["checking"]["this_month"] == Decimal("0")
    assert dashboard.payable_this_month(TODAY) == Decimal("420")


@pytest.mark.django_db
def test_payable_table_rows(client_logged_in, settings, client, setup, monkeypatch):
    """(c) 受入の一覧の上の表に「仕入明細で確かめ中の金額」の行と ¥105。確かめ中が無ければ行が無い。金額の閲覧の無い人には表ごと出ない。"""
    from django.contrib.auth import get_user_model

    from seizou.accounts import services as accounts_services

    original = services.payable_by_partner
    monkeypatch.setattr(services, "payable_by_partner", lambda today=None: original(today=TODAY))
    body = client_logged_in.get(reverse("purchasing:receipt_list")).content.decode()
    assert "仕入明細で確かめ中の金額" in body and "¥105" in body
    assert "相手が仕入明細で承諾した明細は、仕入明細の金額" in body

    _partner_row, si = setup
    SelfInvoiceLine.objects.filter(self_invoice=si).update(reply_type="AP")
    body = client_logged_in.get(reverse("purchasing:receipt_list")).content.decode()
    assert "data-payable-checking" not in body

    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "on"}}
    accounts_services.issue_account("genba", "kumo-tests-2611", [], [], {}, None)
    client.force_login(get_user_model().objects.get(username="genba"))
    body = client.get(reverse("purchasing:receipt_list")).content.decode()
    assert 'id="payable-table"' not in body
