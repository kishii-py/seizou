"""入金の残る請求書の取消を止める (決め 11-04 06:4x。N56 の迷った事)。

取消でない入金が 1 件でも残る請求書は billing.services.cancel_invoice が ValueError (赤の知らせ) で止め、
取消の確かめの画面は「取り消す」を出さずに同じ文を黄で出す。入金を全部取り消した後は今までどおり取り消せる。
人は accounts の services で作る。名前は作り物。
fixture は tests/test_billing_payment_views.py と同じ形を写す (import した fixture は ruff の F401・F811 に当たる)。
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.billing import services
from seizou.billing.models import Invoice, Payment
from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from tests.test_billing_payment_views import ALL_ON, PASSWORD, RECEIVED_ON, _customer, _invoice, _order_line, account

TWO_LEFT = "この請求書には入金の記録が 2 件あります (合計 ¥7,500)。先に入金を取り消してください。"
ONE_LEFT = "この請求書には入金の記録が 1 件あります (合計 ¥6,500)。先に入金を取り消してください。"
ONE_LEFT_COUNT = "この請求書には入金の記録が 1 件あります。先に入金を取り消してください。"


@pytest.fixture
def on(settings):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password=PASSWORD)


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def customer(user):
    return _customer(user, closing_day=20, payment_month="next", payment_day=25)


@pytest.fixture
def line(user, customer, item):
    return _order_line(user, customer, item)


@pytest.fixture
def invoice(user, customer, line):
    return _invoice(user, customer, line)


def _refusal_of_cancel(invoice, user):
    """cancel_invoice が止めた文 (ValueError の字)。"""
    with pytest.raises(ValueError) as caught:
        services.cancel_invoice(invoice, user)
    return str(caught.value)


def _status(invoice):
    return Invoice.objects.get(pk=invoice.pk).status


def test_cancel_refused_while_payment_left(user, invoice):
    assert services.cancel_refusal(invoice) == ""

    services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), user)
    services.record_payment(invoice, RECEIVED_ON, Decimal("1000"), user)

    assert _refusal_of_cancel(invoice, user) == "取り消せません。" + TWO_LEFT
    assert _status(invoice) == Invoice.STATUS_ISSUED
    assert Payment.objects.filter(invoice=invoice, status=Payment.STATUS_RECORDED).count() == 2
    assert services.cancel_refusal(invoice) == "取り消せません。" + TWO_LEFT


def test_refusal_counts_without_money(on, client, user, invoice):
    services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), user)

    # 伝票編集 (請求書を書ける)・金額の閲覧なし = 件数だけ (「¥」が無い)
    jimu = account("jimu", permissions=["slip_edit"])
    refusal = _refusal_of_cancel(invoice, jimu)
    assert refusal == "取り消せません。" + ONE_LEFT_COUNT
    assert "¥" not in refusal
    assert _status(invoice) == Invoice.STATUS_ISSUED

    # 取消の確かめの画面の黄の文も件数だけ
    client.force_login(jimu)
    response = client.get(reverse("billing:invoice_cancel", args=[invoice.pk]))
    assert response.status_code == 200
    html = response.content.decode()
    assert ONE_LEFT_COUNT in html
    assert ONE_LEFT not in html
    assert "取り消す</button>" not in html

    # 金額の閲覧あり = 件数と合計
    keiri = account("keiri", permissions=["billing"], see_money=True)
    assert _refusal_of_cancel(invoice, keiri) == "取り消せません。" + ONE_LEFT
    assert _status(invoice) == Invoice.STATUS_ISSUED

    assert services.cancel_refusal(invoice, hide_money=True) == "取り消せません。" + ONE_LEFT_COUNT


def test_cancel_passes_after_payments_cancelled(user, invoice):
    big = services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), user)
    small = services.record_payment(invoice, RECEIVED_ON, Decimal("1000"), user)

    services.cancel_payment(small, user)
    assert _refusal_of_cancel(invoice, user) == "取り消せません。" + ONE_LEFT
    assert _status(invoice) == Invoice.STATUS_ISSUED

    services.cancel_payment(big, user)
    assert services.cancel_refusal(invoice) == ""
    cancelled = services.cancel_invoice(invoice, user)
    assert cancelled.status == Invoice.STATUS_CANCELLED
    assert _status(invoice) == Invoice.STATUS_CANCELLED

    # 取り消した入金の行は 2 件とも残る (履歴)
    rows = Payment.objects.filter(invoice=invoice)
    assert rows.count() == 2
    assert set(rows.values_list("status", flat=True)) == {Payment.STATUS_CANCELLED}

    # 取消の請求書への新しい入金は R21 (今の形のまま)
    with pytest.raises(RuleViolation) as caught:
        services.record_payment(invoice, RECEIVED_ON, Decimal("500"), user)
    assert caught.value.code == "R21"


def test_cancel_screen_hides_button_while_payment_left(client_logged_in, user, invoice):
    payment = services.record_payment(invoice, RECEIVED_ON, Decimal("6500"), user)
    url = reverse("billing:invoice_cancel", args=[invoice.pk])
    back = reverse("billing:invoice_detail", args=[invoice.pk]) + "#payments"

    # 確かめの画面: 「取り消す」と form の文を出さず、黄の知らせと詳細の入金の枠へ戻る link だけ
    # (ログアウトの form がどの画面にもあるので、<form の字でなくボタンの字で見る)
    response = client_logged_in.get(url)
    assert response.status_code == 200
    html = response.content.decode()
    assert "取り消す</button>" not in html
    assert "保存</button>" not in html
    assert "この請求書を取り消します。" not in html
    assert "alert-warning" in html
    assert ONE_LEFT in html
    assert f'href="{back}"' in html

    # URL を直に打った POST は services が赤の知らせで止める (請求書は発行のまま)
    response = client_logged_in.post(url, {})
    assert response.status_code == 200
    html = response.content.decode()
    assert 'data-notice="stop"' in html
    assert ONE_LEFT in html
    assert _status(invoice) == Invoice.STATUS_ISSUED

    # 入金を取り消した後は、今までどおり「取り消す」が出る
    services.cancel_payment(payment, user)
    html = client_logged_in.get(url).content.decode()
    assert "取り消す</button>" in html
    assert "この請求書を取り消します。入っていた出荷は、また請求できます。請求書は消えずに状態が「取消」で残り、元には戻せません。" in html
    assert ONE_LEFT not in html  # 断りの黄が消えた (確かめの文も黄の帯なので、class の字でなく断りの字で見る。N158b)
