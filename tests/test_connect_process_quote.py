"""会社どうしの注文の明細の工程名と見積の番号 (C100 の その 4)。

- 送る: 発注の明細の工程の名前を process_name、単価表から写した見積の番号を quote_no に載せる (無ければ空の字)。
  中小企業共通 EDI の工程名 (UN01012678) と (明細行参照) 見積回答書ID (UN01005580) を元にした鍵。
- 受ける: 受注の明細の工程名と見積の番号に写す。鍵が無い・字でない値は空の字にし、断らない。
- 受信した注文の明細の表: 品名の下に「工程 …」と「見積の番号 …」を出す。
相手への HTTP は requests.post を偽の物に差し替えて見る (外へは出ない)。取引先・品目・会社は作り物。
"""

import json
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string

from seizou.connect import services
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.purchasing import services as purchasing_services
from tests.test_connect_send import ENV_NAME, PARTNER_URL, FakePost
from tests.test_connect_signing import _new_secret


@pytest.fixture
def secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(ENV_NAME, value)
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


def _order_data(**line_changes):
    """手で作る注文のデータ (send_order が作る形。明細 1 行)。line_changes の値が ... の鍵は外す。"""
    line = {
        "line_no": 1,
        "part_no": "T-100",
        "part_name": "作り物の軸",
        "qty": "10.00",
        "unit": "個",
        "unit_price": "300.00",
        "price_basis": "unit",
        "due_date": "2026-11-20",
        "note": "",
    }
    line.update(line_changes)
    return {
        "id": str(uuid.uuid4()),
        "kind": "order",
        "sent_at": "2026-11-02T10:00:00+09:00",
        "order_no": "PO-9001",
        "order_date": "2026-11-02",
        "buyer": {"id": "a-company", "name": "作り物の発注する会社"},
        "supplier": {"code": "S001", "name": "作り物の受ける会社"},
        "note": "",
        "lines": [{key: value for key, value in line.items() if value is not ...}],
    }


# ---------------------------------------------------------------- (a) 送る


@pytest.mark.django_db
def test_send_order_carries_process_name_and_quote_no(monkeypatch, user, secret, subcontractor, item):
    services.register_connection(subcontractor, PARTNER_URL, ENV_NAME, "send", user)
    outside = Process.objects.create(code="OUT1", name="作り物の外の工程", is_outsourced=True)
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    purchasing_services.create_purchase_order(
        subcontractor,
        [
            {"item": item, "qty": Decimal("5"), "unit_price": Decimal("300"), "process": outside, "quote_no": "Q-1"},
            {"item": item, "qty": Decimal("5"), "unit_price": Decimal("300")},
        ],
        user,
        order_date=date(2026, 11, 2),
    )
    sent = json.loads(fake.calls[0]["data"].decode("utf-8"))
    assert [(line["process_name"], line["quote_no"]) for line in sent["lines"]] == [("作り物の外の工程", "Q-1"), ("", "")]


# ---------------------------------------------------------------- (b) (c) 受ける


@pytest.mark.django_db
def test_accept_copies_process_name_and_quote_no(user, customer, own_item):
    inbox = services.record_inbox(customer, _order_data(process_name="作り物の外の工程", quote_no="Q-1"), user)
    order = services.accept(inbox, {"T-100": own_item}, user, send=False)
    line = order.lines.get()
    assert (line.process_name, line.quote_no) == ("作り物の外の工程", "Q-1")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "changes",
    [{}, {"process_name": 12, "quote_no": None}, {"process_name": None, "quote_no": ["Q-1"]}],
)
def test_accept_without_text_values_keeps_blank(user, customer, own_item, changes):
    inbox = services.record_inbox(customer, _order_data(**changes), user)
    order = services.accept(inbox, {"T-100": own_item}, user, send=False)
    line = order.lines.get()
    assert (line.process_name, line.quote_no) == ("", "")


@pytest.mark.django_db
def test_conditional_accept_also_copies_values(user, customer, own_item):
    inbox = services.record_inbox(customer, _order_data(process_name="作り物の外の工程", quote_no="Q-1"), user)
    order = services.accept_with_conditions(inbox, {"T-100": own_item}, {1: {"unit_price": Decimal("280")}}, user, send=False)
    line = order.lines.get()
    assert (line.unit_price, line.process_name, line.quote_no) == (Decimal("280"), "作り物の外の工程", "Q-1")


# ---------------------------------------------------------------- (d) 受信した注文の明細の表


def test_payload_lines_show_process_name_and_quote_no():
    shown = {"line_no": 1, "part_no": "T-100", "part_name": "作り物の軸", "qty": "10", "unit": "個", "unit_price": "300", "due_date": "2026-11-20",
             "process_name": "作り物の外の工程", "quote_no": "Q-1"}
    plain = {"line_no": 2, "part_no": "T-200", "part_name": "作り物の板", "qty": "3", "unit": "枚", "unit_price": "100", "due_date": "2026-11-20"}

    def render(lines):
        return render_to_string("connect/_payload_lines.html", {"object": SimpleNamespace(payload={"lines": lines}), "seizou": {"hide_money": False}})

    html = render([shown])
    assert "工程 作り物の外の工程" in html and "見積の番号" in html and "Q-1" in html
    html = render([plain])
    assert "工程 " not in html and "見積の番号" not in html
