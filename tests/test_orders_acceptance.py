"""受注の詳細の明細の「検収」の列 (orders)。EDI の 4 段目。

検収の行は connect.services.record_acceptance で作る (元の届いた注文を 1 行置いてから)。
orders は connect を import しない (逆の関係 acceptances を読むだけ)。試験は 2 つの部品をまとめて置く。
受注は services で作る。作り物のデータだけを使う。
"""

import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.connect.models import Inbox
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.models import OrderLine
from tests.test_orders_board import ORDER_DATE, _line

_TH = re.compile(r"<th(?:\s[^>]*)?>(.*?)</th>", re.S)
_TD = re.compile(r"<td(?:\s[^>]*)?>(.*?)</td>", re.S)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _accept(line, qty, accepted_on):
    """受注の明細に検収を 1 行付ける。元の届いた注文 (受け入れた物) は受注ごとに 1 行置く。"""
    inbox = Inbox.objects.filter(order=line.order).first()
    if inbox is None:
        inbox = Inbox.objects.create(
            message_id=uuid.uuid4(),
            partner=line.order.customer,
            kind="order",
            payload={},
            status="accepted",
            order=line.order,
        )
    return connect_services.record_acceptance(
        inbox,
        line,
        Decimal(qty),
        accepted_on,
        None,
        message_id=uuid.uuid4(),
        buyer_order_no="PO-0001",
        buyer_line_no=line.line_no,
    )


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


@pytest.mark.django_db
def test_acceptance_summary_and_line_property(user, customer, item):
    line = _line(user, customer, item)

    assert orders_services.acceptance_summary(line) == {"count": 0, "total": Decimal("0"), "last_on": None}

    _accept(line, "4", date(2026, 11, 5))
    _accept(line, "6", date(2026, 11, 7))
    summary = orders_services.acceptance_summary(line)

    assert summary == {"count": 2, "total": Decimal("10"), "last_on": date(2026, 11, 7)}
    assert isinstance(summary["total"], Decimal)
    assert OrderLine.objects.get(pk=line.pk).acceptance == summary


@pytest.mark.django_db
def test_order_detail_has_acceptance_column_before_remaining(user, logged_in, customer, item):
    order = orders_services.create_order(
        customer,
        [
            {"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")},
            {"item": item, "qty": Decimal("5"), "unit_price": Decimal("100")},
        ],
        user,
        order_date=ORDER_DATE,
    )
    first, _second = order.lines.order_by("line_no")
    _accept(first, "4", date(2026, 11, 5))
    _accept(first, "6", date(2026, 11, 7))

    body = logged_in.get(reverse("orders:order_detail", args=[order.pk])).content.decode()

    start = body.index('id="order-lines-title"')
    table = body[start : body.index("</table>", start)]
    heads = [_text(th) for th in _TH.findall(table)]
    assert heads == ["操作", "行番号", "部品", "数量", "単位", "単価", "金額", "納期", "出荷済み", "検収", "残"]
    rows = [[_text(td) for td in _TD.findall(row)] for row in re.findall(r'<tr class="h-10"[^>]*>(.*?)</tr>', table, re.S)]
    assert len(rows) == 2
    assert all(len(cells) == len(heads) for cells in rows)
    accepted, plain = rows
    # 集計の数の桁は DB で違う (SQLite は桁なし、PostgreSQL は小数 2 桁) ので、.00 を外して比べる
    assert accepted[heads.index("検収")].replace(".00", "") == "10 (2026-11-07)"
    assert plain[heads.index("検収")] == "—"
    # 最後の欄は「残」のまま (E2E が行の最後の td で残を読む)
    assert accepted[-1].replace(".00", "") == "10" and plain[-1].replace(".00", "") == "5"
