"""送り合った記録 (connect.models.Exchange・services._deliver と receive・exchange_amount。決め HIST-1 の edi・C106-1) の試験。

送った物と受けた物の中身が送ったとおり・受けたとおりの字で 1 通 1 行に残ること、金額の数え方、一覧と詳細の画面、
金額の閲覧の無い人に金額の列と絞りと中身の金額の鍵を出さないこと、受信した注文の変更の履歴を見る。
1 つの DB に、注文を送る会社と受ける会社の両方の役を置く (test_connect_reply.py の形)。相手への HTTP は requests.post を偽の物に
差し替え、受けた本文を services.receive に渡す (外へは出ない)。鍵はテストのたびに作り、環境変数に置く。取引先・品目・会社は作り物。
"""

import csv
import io
import json
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

import seizou
from seizou.accounts import services as accounts_services
from seizou.connect import services, signing
from seizou.connect.models import Exchange, Inbox, Outbox
from seizou.core.models import ChangeLog
from seizou.masters import services as masters_services
from seizou.purchasing import services as purchasing_services
from seizou.shipping import services as shipping_services
from tests.test_connect_reply import ORDER_ENV, REPLY_ENV, _connect, _into_receive
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

MONEY_ON = {"department": "off", "write": "off", "money": "on"}
PASSWORD = "kumo-tests-2611"
DAY = date(2026, 11, 5)


def _account(login_id):
    accounts_services.issue_account(login_id, PASSWORD, [], [], {}, None)
    return get_user_model().objects.get(username=login_id)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only", is_superuser=True)


@pytest.fixture
def order_secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(ORDER_ENV, value)
    return value


@pytest.fixture
def reply_secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(REPLY_ENV, value)
    return value


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の発注元", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def own_item(user):
    return masters_services.create_item({"part_no": "B-200", "name": "作り物の受ける側の部品", "unit": "個"}, user)


@pytest.fixture
def connections(user, subcontractor, customer, order_secret, reply_secret):
    _connect(user, subcontractor, customer)


def _send(subcontractor, item, user, lines=None):
    return purchasing_services.create_purchase_order(
        subcontractor,
        lines
        or [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("25.5"), "due_date": date(2026, 11, 20)}],
        user,
        order_date=date(2026, 11, 2),
    )


def _signed(secret, data, msg_id=None):
    """手で作る本文と署名の見出し。返す物は (番号, 本文の bytes, 見出し)。"""
    msg_id = msg_id or str(uuid.uuid4())
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return msg_id, raw, signing.headers_for(secret, msg_id, raw)


def _order_data(**changes):
    data = {
        "kind": "order",
        "order_no": "PO-作り物-1",
        "order_date": "2026-11-02",
        "buyer": {"id": "a", "name": "作り物の発注元"},
        "lines": [{"line_no": 1, "part_no": "T-100", "part_name": "作り物の軸", "qty": "10", "unit": "個", "unit_price": "25.5", "due_date": "2026-11-20", "note": ""}],
    }
    data.update(changes)
    return data


# ---------------------------------------------------------------- (a)(b) 送った物


@pytest.mark.django_db
def test_send_order_keeps_body_as_sent(monkeypatch, user, order_secret, subcontractor, customer, item, connections):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    po = _send(subcontractor, item, user)

    row = Exchange.objects.get()
    outbox = Outbox.objects.get()
    assert row.direction == "sent" and row.kind == "order" and row.partner == subcontractor
    assert row.message_id == outbox.message_id
    assert row.body == fake.calls[0]["data"].decode("utf-8")
    assert row.result == "受け取った"

    services.send_order(po, user)  # 送り直し = 同じ番号で 2 行目
    rows = list(Exchange.objects.order_by("pk"))
    assert len(rows) == 2 and rows[1].message_id == rows[0].message_id


@pytest.mark.django_db
def test_no_secret_makes_no_row(monkeypatch, user, subcontractor, customer, item, connections):
    monkeypatch.delenv(ORDER_ENV)
    monkeypatch.setattr("requests.post", FakePost())

    _send(subcontractor, item, user)

    assert Exchange.objects.count() == 0
    assert Outbox.objects.get().response.startswith("鍵の置き場")


# ---------------------------------------------------------------- (c) 注文回答・検収・出荷の知らせ


@pytest.mark.django_db
def test_reply_acceptance_and_notice_are_kept(monkeypatch, user, order_secret, reply_secret, subcontractor, customer, item, own_item, connections):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    po = _send(subcontractor, item, user)
    inbox = Inbox.objects.get()
    order = services.accept(inbox, {"T-100": own_item}, user)
    purchasing_services.receive(po.lines.get(), Decimal("4"), DAY, user)
    shipping_services.ship(order.lines.get(), Decimal("4"), DAY, user)

    pairs = sorted((row.direction, row.kind) for row in Exchange.objects.all())
    for kind in ("order", "order_reply", "acceptance", "shipping_notice"):
        assert ("sent", kind) in pairs and ("received", kind) in pairs, kind
    reply = Exchange.objects.get(direction="sent", kind="order_reply")
    assert '"reply_type":"AP"' in reply.body
    assert reply.kind_label() == "注文回答"
    assert all(row.result in ("受け取った", "受けた") for row in Exchange.objects.all())


def test_kind_label_for_order_versions():
    assert Exchange(kind="order", body=json.dumps({"purpose_code": "4"})).kind_label() == "注文の変更"
    assert Exchange(kind="order", body=json.dumps({"purpose_code": "1"})).kind_label() == "注文の取消"
    assert Exchange(kind="order", body=json.dumps({"purpose_code": "9"})).kind_label() == "注文"
    assert Exchange(kind="mystery", body="{}").kind_label() == "mystery"


# ---------------------------------------------------------------- (d)(e) 受けた物


@pytest.mark.django_db
def test_receive_keeps_received_and_refused(user, order_secret, subcontractor, customer, connections):
    msg_id, raw, headers = _signed(order_secret, _order_data())
    result = services.receive(raw, headers)
    assert isinstance(result, Inbox)
    row = Exchange.objects.get()
    assert row.direction == "received" and row.partner == customer
    assert row.body == raw.decode("utf-8")
    assert row.message_id == uuid.UUID(msg_id)
    assert row.result == "受けた"

    _id, raw, headers = _signed(order_secret, _order_data(lines=[]))
    refused = services.receive(raw, headers)
    assert isinstance(refused, str)
    assert Exchange.objects.order_by("-pk").first().result == f"断った: {refused}"

    wrong = _new_secret()
    _id, raw, headers = _signed(wrong, _order_data())
    count = Exchange.objects.count()
    services.receive(raw, headers)
    assert Exchange.objects.count() == count  # 署名の合わない物は残さない

    _id, raw, headers = _signed(order_secret, {"kind": "chat_message", "order_id": str(uuid.uuid4()), "body": "作り物の言葉"})
    services.receive(raw, headers)
    assert Exchange.objects.filter(kind="chat_message").count() == 1
    _id, raw, headers = _signed(order_secret, {"kind": "chat_file", "file_id": str(uuid.uuid4())})
    services.receive(raw, headers)
    assert not Exchange.objects.filter(kind="chat_file").exists()


@pytest.mark.django_db
def test_secrets_are_not_kept(monkeypatch, user, order_secret, reply_secret, subcontractor, customer, item, own_item, connections):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    _send(subcontractor, item, user)
    services.accept(Inbox.objects.get(), {"T-100": own_item}, user)

    for row in Exchange.objects.all():
        for secret in (order_secret, reply_secret):
            assert secret not in row.body and secret not in row.result


# ---------------------------------------------------------------- (f)(g)(h) 画面と消す道


def _exchange(partner, *, day, amount=None, kind="order", body=None, direction="sent"):
    created = timezone.make_aware(datetime(day.year, day.month, day.day, 10, 0))
    return Exchange.objects.create(
        direction=direction,
        kind=kind,
        message_id=uuid.uuid4(),
        partner=partner,
        created_at=created,
        body=body or json.dumps({"kind": kind, "order_no": "PO-作り物-9", "lines": [{"qty": "2", "unit_price": "1500"}]}, ensure_ascii=False),
        amount=amount,
        result="受け取った",
    )


@pytest.mark.django_db
def test_list_filters_csv_and_no_uuid(client, user, subcontractor, customer):
    client.force_login(user)
    hit = _exchange(subcontractor, day=date(2026, 11, 5), amount=Decimal("3000"))
    _exchange(customer, day=date(2026, 11, 5), amount=Decimal("3000"))
    _exchange(subcontractor, day=date(2026, 10, 1), amount=Decimal("3000"))

    url = reverse("connect:exchange_list")
    response = client.get(url, {"f_created_from": "2026-11-01", "f_created_to": "2026-11-30", "f_partner": subcontractor.pk})
    assert list(response.context["object_list"]) == [hit]
    full = client.get(url).content.decode()
    assert "注文回答" in full and ">注文<" in full
    assert str(hit.message_id) not in full

    rows = list(csv.reader(io.StringIO(client.get(url, {"format": "csv"}).content.decode("utf-8-sig"))))
    assert len(rows) == 4 and "金額" in rows[0]

    detail = client.get(reverse("connect:exchange_detail", args=[hit.pk])).content.decode()
    basic = detail[detail.index('aria-label="基本情報"') :]  # 中身の枠 (block main) は基本情報のタブより前に描く
    assert str(hit.message_id) not in basic
    assert "unit_price" in detail and "送ったとおりの字" in detail


@pytest.mark.django_db
def test_detail_hides_money_keys_for_people_without_money(settings, client, subcontractor):
    row = _exchange(subcontractor, day=date(2026, 11, 5), amount=Decimal("3000"))
    settings.SEIZOU = {"access_control": dict(MONEY_ON)}
    client.force_login(_account("genba"))

    detail = client.get(reverse("connect:exchange_detail", args=[row.pk])).content.decode()

    assert "unit_price" not in detail
    assert "1500" not in detail
    assert "送ったとおりの字" not in detail
    assert "&quot;qty&quot;: &quot;2&quot;" in detail  # 金額でない鍵は出る (テンプレートが引用符を字の参照にする)


@pytest.mark.django_db
def test_settings_has_exchange_link(client, user):
    client.force_login(user)
    body = client.get(reverse("core:settings")).content.decode()
    assert "送り合った記録" in body
    assert reverse("connect:exchange_list") in body


def test_no_delete_path_for_exchange():
    root = Path(seizou.__file__).parent
    hits = [
        f"{path}:{number}"
        for path in root.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if "Exchange" in line and ".delete(" in line
    ]
    assert hits == []


# ---------------------------------------------------------------- (i) 受信した注文の履歴


@pytest.mark.django_db
def test_inbox_history_by_partner_and_accept(monkeypatch, client, user, order_secret, reply_secret, subcontractor, customer, item, own_item, connections):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    _send(subcontractor, item, user)
    inbox = Inbox.objects.get()
    created = ChangeLog.objects.get(model="connect.inbox", action="created")
    assert created.actor is None and created.actor_label == "作り物の発注元 の seizou"

    services.accept(inbox, {"T-100": own_item}, user)
    accepted = ChangeLog.objects.filter(model="connect.inbox", action="updated").first()
    status = next(change for change in accepted.changes if change["field"] == "status")
    assert (status["before"], status["after"]) == ("未処理", "受け入れた")
    assert all(change["field"] != "payload" for change in created.changes)

    client.force_login(user)
    body = client.get(reverse("connect:inbox_detail", args=[inbox.pk])).content.decode()
    assert 'id="history-heading"' in body
    assert "状態: 未処理 → 受け入れた" in body
    assert re.search(r"最後に直した <span class=\"font-mono tabular-nums\">[0-9: -]+</span> ・ tester", body)


# ---------------------------------------------------------------- (j) 金額


@pytest.mark.django_db
def test_amount_same_on_both_sides(monkeypatch, user, order_secret, reply_secret, subcontractor, customer, item, own_item, connections):
    monkeypatch.setattr("requests.post", FakePost(inside=_into_receive))
    _send(
        subcontractor,
        item,
        user,
        lines=[
            {"item": item, "qty": Decimal("1.25"), "unit_price": Decimal("10.01"), "due_date": date(2026, 11, 20)},
            {"item": item, "qty": Decimal("3"), "unit_price": Decimal("250.50"), "due_date": date(2026, 11, 20)},
        ],
    )
    services.accept(Inbox.objects.get(), {"T-100": own_item}, user)

    sent = Exchange.objects.get(direction="sent", kind="order")
    received = Exchange.objects.get(direction="received", kind="order")
    assert sent.amount == Decimal("764.0125")
    assert received.amount == sent.amount
    assert Exchange.objects.get(direction="sent", kind="order_reply").amount is None


@pytest.mark.django_db
def test_unreadable_qty_keeps_row_without_amount(user, order_secret, subcontractor, customer, connections):
    data = _order_data()
    data["lines"][0]["qty"] = "作り物の字"
    _id, raw, headers = _signed(order_secret, data)
    services.receive(raw, headers)

    row = Exchange.objects.get()
    assert row.amount is None
    assert row.body == raw.decode("utf-8")


def test_exchange_amount_rules():
    assert services.exchange_amount({"lines": [{"qty": "1", "unit_price": "10.00001"}]}) is None  # 小数 5 桁は丸めない
    assert services.exchange_amount({"lines": [{"qty": "3", "unit_price": "5000", "price_basis": "lump"}]}) == Decimal("5000")
    assert services.exchange_amount(
        {"lines": [{"qty": "2", "unit_price": "100"}, {"qty": "5", "unit_price": "100", "line_status_code": "2"}]}
    ) == Decimal("200")
    assert services.exchange_amount(
        {"purpose_code": "1", "lines": [{"qty": "2", "unit_price": "100", "line_status_code": "2"}]}
    ) == Decimal("0")
    assert services.exchange_amount({"kind": "order_reply", "lines": [{"qty": "2", "unit_price": "100"}]}) is None
    assert services.exchange_amount({"lines": "作り物"}) is None
    assert services.exchange_amount({"lines": [{"qty": "2"}]}) is None


# ---------------------------------------------------------------- (k) 金額の一覧


@pytest.mark.django_db
def test_amount_column_and_filter_only_for_money(settings, client, user, subcontractor):
    low = _exchange(subcontractor, day=date(2026, 11, 5), amount=Decimal("1000"))
    edge = _exchange(subcontractor, day=date(2026, 11, 5), amount=Decimal("3000"))
    _exchange(subcontractor, day=date(2026, 11, 5), amount=Decimal("5000.5"))
    _exchange(subcontractor, day=date(2026, 10, 1), amount=Decimal("3000"))
    settings.SEIZOU = {"access_control": dict(MONEY_ON)}
    url = reverse("connect:exchange_list")

    client.force_login(user)
    response = client.get(url, {"f_amount_from": "1000", "f_amount_to": "3000"})
    body = response.content.decode()
    assert 'name="f_amount_from"' in body and 'name="f_amount_to"' in body
    assert ">金額<" in body or "金額</" in body
    assert set(response.context["object_list"]) >= {low, edge}
    assert len(response.context["object_list"]) == 3
    both = client.get(url, {"f_amount_from": "1000", "f_amount_to": "3000", "f_created_from": "2026-11-01"})
    assert set(both.context["object_list"]) == {low, edge}

    client.force_login(_account("genba"))
    response = client.get(url, {"f_amount_from": "1000", "f_amount_to": "3000"})
    body = response.content.decode()
    assert 'name="f_amount_from"' not in body
    assert len(response.context["object_list"]) == 4
    rows = list(csv.reader(io.StringIO(client.get(url, {"format": "csv"}).content.decode("utf-8-sig"))))
    assert "金額" not in rows[0]
    detail = client.get(reverse("connect:exchange_detail", args=[edge.pk])).content.decode()
    basic = detail[detail.index('aria-label="基本情報"') :]
    assert ">金額<" not in basic
