"""受入の検収の列と、検収を送る側の services (purchasing)。EDI の 4 段目。

1 段目 = services と受入の列。2 段目 = 発注の詳細の「検収」の節・「送り直す」の口・受入の詳細 (Django の test client)。
HTTP は no_http で止め、つながりの無い協力企業で発注を作る (送る発注の試験だけ偽の requests.post を使う)。
鍵はテストのたびに作り、環境変数に置く。取引先と品目は作り物。日は固定の値を渡す。
"""

import json
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import requests
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.connect.models import Outbox
from seizou.core import access
from seizou.core.views import hidden_detail_fields
from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder, Receipt
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

ORDER_DATE = date(2026, 10, 28)
DAY = date(2026, 11, 2)
ENV_NAME = "SEIZOU_PARTNER_TESTSUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def no_http(monkeypatch):
    """HTTP を呼んだら落とす。送った注文の行が無い発注は検収を送らないことを見る。"""

    def fail(*args, **kwargs):
        raise AssertionError("送った注文の行が無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


def _order(partner, item, user, *, send=True):
    return services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("25.5")}],
        user,
        order_date=ORDER_DATE,
        send=send,
    )


@pytest.fixture
def receipt(user, subcontractor, item, no_http):
    po = _order(subcontractor, item, user)
    return services.receive(po.lines.get(), Decimal("4"), DAY, user)


@pytest.mark.django_db
def test_assign_acceptance_number_is_stable(user, receipt):
    first = services.assign_acceptance_number(receipt, user)
    second = services.assign_acceptance_number(receipt, user)

    assert isinstance(first, uuid.UUID)
    assert first == second
    assert Receipt.objects.get(pk=receipt.pk).acceptance_message_id == first


@pytest.mark.django_db
def test_record_acceptance_response_truncates_and_marks_sent(user, receipt):
    saved = services.record_acceptance_response(receipt, "あ" * 600, user)

    assert isinstance(saved, Receipt)
    assert saved is not receipt and saved.pk == receipt.pk
    assert len(saved.acceptance_response) == 500
    assert saved.acceptance_sent_at is None

    sent = services.record_acceptance_response(receipt, "受け取った", user, sent=True)

    assert sent.acceptance_response == "受け取った"
    assert sent.acceptance_sent_at is not None

    # sent が偽の 2 回目は、送った日時を消さない
    again = services.record_acceptance_response(receipt, "返事が読めません (HTTP 500)", user)

    assert again.acceptance_sent_at == sent.acceptance_sent_at
    assert Receipt.objects.get(pk=receipt.pk).acceptance_response == "返事が読めません (HTTP 500)"


def test_acceptance_state_text():
    assert Receipt().acceptance_state() == ""
    assert Receipt(acceptance_response="届きません: 相手につながりません。").acceptance_state() == (
        "届きません: 相手につながりません。"
    )
    sent = Receipt(
        acceptance_sent_at=datetime(2026, 11, 5, 1, 30, tzinfo=UTC), acceptance_response="受け取った"
    )
    # 日時は会社の時刻 (Asia/Tokyo) で出す
    assert sent.acceptance_state() == "送った 2026-11-05 10:30"


@pytest.mark.django_db
def test_receive_without_outbox_calls_no_http(monkeypatch, user, subcontractor, item, no_http):
    other = masters_services.create_partner(
        {"code": "S002", "name": "作り物の送る協力企業", "role": "subcontractor"}, user
    )
    monkeypatch.setenv(ENV_NAME, _new_secret())
    connect_services.register_connection(other, PARTNER_URL, ENV_NAME, "send", user)
    # つながりの無い協力企業の発注と、つながりはあるが送らずに入れた発注 (種の形)
    orders = [_order(subcontractor, item, user), _order(other, item, user, send=False)]

    for po in orders:
        receipt = services.receive(po.lines.get(), Decimal("4"), DAY, user)

        assert services._is_sent_order(po) is False
        assert receipt.acceptance_message_id is None
        assert receipt.acceptance_response == ""


@pytest.mark.django_db
def test_receive_returns_receipt_with_acceptance_columns(monkeypatch, user, subcontractor, item):
    monkeypatch.setenv(ENV_NAME, _new_secret())
    connection = connect_services.register_connection(subcontractor, PARTNER_URL, ENV_NAME, "send", user)
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    po = _order(subcontractor, item, user, send=False)
    Outbox.objects.create(
        message_id=uuid.uuid4(), connection=connection, purchase_order=po, response="受け取った"
    )
    assert services._is_sent_order(po) is True

    receipt = services.receive(po.lines.get(), Decimal("10"), DAY, user)

    (call,) = fake.calls
    assert call["url"] == PARTNER_URL
    assert isinstance(receipt, Receipt)
    assert receipt.acceptance_sent_at is not None
    assert receipt.acceptance_response == "受け取った"
    assert str(receipt.acceptance_message_id) == json.loads(call["data"].decode("utf-8"))["id"]
    # 全部を受け入れたので「納品された」(今までどおり)
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_DELIVERED


def test_receipt_hides_number_in_detail_fields():
    # 発注の明細の無い受入は、送った発注でない側 (番号と検収の 2 列を隠す)。DB に問い合わせない。
    # 送った発注の受入で 2 列が出ることは、test_receipt_detail_shows_state_columns_without_number が見る。
    hidden = hidden_detail_fields(Receipt())
    assert "acceptance_message_id" in hidden
    assert "acceptance_sent_at" in hidden
    assert "acceptance_response" in hidden


# --- 2 段目: 発注の詳細の「検収」の節と「送り直す」の口・受入の詳細 ---


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def send_connection(monkeypatch, subcontractor, user):
    monkeypatch.setenv(ENV_NAME, _new_secret())
    return connect_services.register_connection(subcontractor, PARTNER_URL, ENV_NAME, "send", user)


def _sent_order(monkeypatch, subcontractor, item, user):
    """相手に送った発注 (相手の受け口は 200)。送った注文の行が 1 つある。"""
    monkeypatch.setattr("requests.post", FakePost())
    po = _order(subcontractor, item, user)
    assert po.status == PurchaseOrder.STATUS_SENT
    return po


def _unreachable(monkeypatch):
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("作り物の失敗")))


def _messages(response):
    return [str(message) for message in response.context["messages"]]


@pytest.mark.django_db
def test_order_detail_shows_acceptance_section_for_sent_orders(
    monkeypatch, client_logged_in, user, subcontractor, item, send_connection
):
    po = _sent_order(monkeypatch, subcontractor, item, user)
    line = po.lines.get()
    monkeypatch.setattr("requests.post", FakePost())
    sent = services.receive(line, Decimal("4"), DAY, user)
    _unreachable(monkeypatch)
    lost = services.receive(line, Decimal("3"), date(2026, 11, 3), user)

    body = client_logged_in.get(reverse("purchasing:purchaseorder_detail", args=[po.pk])).content.decode()

    assert 'id="po-acceptance-title"' in body
    start = body.index('id="po-acceptance-title"')
    section = body[start : body.index("</section>", start)]
    assert "検収" in section
    # 見出しと説明の 1 文は、相手を取引先の名前で言う (初見テスト R5 の V92・V93。候補 C45 の 1)
    assert "検収 (作り物の協力企業 へ送った受入の数)" in section
    assert "受け入れた数を 作り物の協力企業 の受注に" in section
    assert "相手の seizou" not in body
    tbody = section[section.index("<tbody>") : section.index("</tbody>")]
    rows = {re.search(r"\d{4}-\d{2}-\d{2}", row).group(0): row for row in re.findall(r"<tr>(.*?)</tr>", tbody, re.S)}
    assert set(rows) == {"2026-11-02", "2026-11-03"}
    assert "送った " in rows["2026-11-02"] and ">4</td>" in rows["2026-11-02"]
    assert "text-error" not in rows["2026-11-02"]
    assert "text-error" in rows["2026-11-03"] and "届きません" in rows["2026-11-03"]
    assert reverse("purchasing:receipt_acceptance_resend", args=[lost.pk]) in rows["2026-11-03"]
    assert reverse("purchasing:receipt_acceptance_resend", args=[sent.pk]) not in body
    # 「送った」の発注なので、作成のときの「送り直す」(主色) も出ない
    assert body.count("btn-primary") == 0


@pytest.mark.django_db
def test_order_detail_has_no_section_for_hand_orders(client_logged_in, user, item, no_http):
    other = masters_services.create_partner(
        {"code": "S002", "name": "作り物のつながりの無い協力企業", "role": "subcontractor"}, user
    )
    po = _order(other, item, user)
    services.receive(po.lines.get(), Decimal("4"), DAY, user)

    body = client_logged_in.get(reverse("purchasing:purchaseorder_detail", args=[po.pk])).content.decode()

    assert "po-acceptance-title" not in body
    assert "受入を登録" in body


@pytest.mark.django_db
def test_resend_port_sends_and_redirects(monkeypatch, client_logged_in, user, subcontractor, item, send_connection):
    po = _sent_order(monkeypatch, subcontractor, item, user)
    _unreachable(monkeypatch)
    receipt = services.receive(po.lines.get(), Decimal("4"), DAY, user)
    assert receipt.acceptance_sent_at is None
    url = reverse("purchasing:receipt_acceptance_resend", args=[receipt.pk])
    detail = reverse("purchasing:purchaseorder_detail", args=[po.pk])

    # 相手がまだ届かない (ConnectionError) と、理由を赤の知らせに出して戻る
    response = client_logged_in.post(url, follow=True)

    assert response.redirect_chain == [(detail, 302)]
    assert any("届きません" in text for text in _messages(response))
    receipt.refresh_from_db()
    assert receipt.acceptance_sent_at is None

    # 相手が立つと送れる。番号は 1 回目の物を使い回す
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    response = client_logged_in.post(url, follow=True)

    assert response.redirect_chain == [(detail, 302)]
    assert "検収を送りました。" in _messages(response)
    (call,) = fake.calls
    assert json.loads(call["data"].decode("utf-8"))["id"] == str(receipt.acceptance_message_id)
    receipt.refresh_from_db()
    assert receipt.acceptance_sent_at is not None
    assert receipt.acceptance_response == "受け取った"


@pytest.mark.django_db
def test_resend_port_refuses_get_and_anonymous(
    monkeypatch, client, user, subcontractor, item, send_connection
):
    po = _sent_order(monkeypatch, subcontractor, item, user)
    _unreachable(monkeypatch)
    receipt = services.receive(po.lines.get(), Decimal("4"), DAY, user)
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    url = reverse("purchasing:receipt_acceptance_resend", args=[receipt.pk])

    anonymous = client.post(url)

    assert anonymous.status_code == 302
    assert reverse("core:login") in anonymous["Location"]

    client.force_login(user)

    assert client.get(url).status_code == 405
    assert client.post(reverse("purchasing:receipt_acceptance_resend", args=[receipt.pk + 100])).status_code == 404
    assert fake.calls == []


@pytest.mark.django_db
def test_resend_port_follows_write_permission(
    monkeypatch, settings, client_logged_in, user, subcontractor, item, send_connection
):
    settings.SEIZOU = {"access_control": {"department": "off", "write": "on", "money": "off"}}
    po = _sent_order(monkeypatch, subcontractor, item, user)
    _unreachable(monkeypatch)
    receipt = services.receive(po.lines.get(), Decimal("4"), DAY, user)
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    url = reverse("purchasing:receipt_acceptance_resend", args=[receipt.pk])

    # 権限の行の無い利用者は断られる (送らない)
    response = client_logged_in.post(url, follow=True)

    assert response.redirect_chain == [(reverse("purchasing:purchaseorder_detail", args=[po.pk]), 302)]
    assert fake.calls == []
    assert any("発注の編集の権限がありません" in text for text in _messages(response))
    assert access.write_message("purchasing") in _messages(response)

    # システム管理者は通る
    admin = get_user_model().objects.create_superuser("admin", password="pass-for-tests-only")
    client_logged_in.force_login(admin)
    response = client_logged_in.post(url, follow=True)

    assert len(fake.calls) == 1
    assert "検収を送りました。" in _messages(response)


@pytest.mark.django_db
def test_receipt_detail_shows_state_columns_without_number(
    monkeypatch, client_logged_in, user, subcontractor, item, send_connection
):
    po = _sent_order(monkeypatch, subcontractor, item, user)
    monkeypatch.setattr("requests.post", FakePost())
    receipt = services.receive(po.lines.get(), Decimal("4"), DAY, user)
    assert receipt.acceptance_message_id is not None

    body = client_logged_in.get(reverse("purchasing:receipt_detail", args=[receipt.pk])).content.decode()

    assert "検収を送った日時" in body
    assert "検収の相手の返事" in body
    assert "受け取った" in body
    assert str(receipt.acceptance_message_id) not in body
    assert "検収の番号" not in body


@pytest.mark.django_db
def test_unsent_receipt_detail_has_no_acceptance_rows(client_logged_in, receipt):
    # つながりの無い協力企業の発注 (送っていない発注) の受入は、検収を送る物でないので 2 行を出さない
    # (初見テスト R5 の候補 C18 の A3)
    assert not receipt.line.purchase_order.outbox_messages.exists()

    body = client_logged_in.get(reverse("purchasing:receipt_detail", args=[receipt.pk])).content.decode()

    assert re.search(r"<dt[^>]*>発注の明細</dt>", body)
    for label in ("検収を送った日時", "検収の相手の返事"):
        assert re.search(rf"<dt[^>]*>{label}</dt>", body) is None, label
