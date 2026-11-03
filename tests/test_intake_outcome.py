"""読み取りの一致率 (N83): 読み取った明細が、そのまま受注に残ったか (intake.outcome.match_counts と core の dashboard.reading_match)。

見る所: 4 つの欄 (品番・数量・単価・納期) が同じなら「そのまま」、全角と空白はそろえて比べる、人が消した行は「直した」、
人が足した行は数えない、問い合わせは枚数で増えない、読み取りの詳細と「受注にする」の画面の 1 行。
本物の API は呼ばない (読み取りの行は _ask_claude を作り物の返事に差し替えて extract_order で作る)。名前は作り物だけ。
返事 (tests/fixtures/intake/order_reply.json) は明細 2 行 = X-100 ×10 単価 120 納期 2026-11-20 / X-200 ×5 単価 null 納期 2026-11-27。
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.core import dashboard
from seizou.intake import services
from seizou.masters.services import create_item, create_partner
from seizou.orders import services as orders_services
from tests.test_intake_extract import FAKE_KEY, refuse_api, reply_data, upload


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(services, "_ask_claude", refuse_api)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def customer(user):
    return create_partner({"code": "IO01", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def mapping(user):
    shaft = create_item(
        {"part_no": "S-100", "name": "作り物のシャフト", "unit": "個", "standard_price": Decimal("150")}, user
    )
    bracket = create_item(
        {"part_no": "B-200", "name": "作り物のブラケット", "unit": "個", "standard_price": Decimal("240")}, user
    )
    return {"X-100": shaft, "X-200": bracket}


@pytest.fixture
def read_order(monkeypatch, customer, user):
    """作り物の返事で読み取りの記録を 1 つ作って返す (for_customer を渡せば、その得意先で)。"""

    def _read(for_customer=None):
        text = json.dumps(reply_data(), ensure_ascii=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
        monkeypatch.setattr(services, "_ask_claude", lambda model, block: text)
        try:
            return services.extract_order(upload(), for_customer or customer, user)
        finally:
            monkeypatch.setattr(services, "_ask_claude", refuse_api)

    return _read


@pytest.fixture
def ordered(read_order, mapping, user):
    """読み取って受注にした (accept_extracted) 読み取りの行と、その受注の 2 行の明細。"""

    def _ordered():
        extraction = read_order()
        order = services.accept_extracted(extraction, mapping, user)
        return extraction, order, order.lines.get(line_no=1), order.lines.get(line_no=2)

    return _ordered


def test_match_counts_all_same(ordered, read_order):
    extraction, _order, _first, _second = ordered()

    assert dashboard.reading_match(extraction=extraction) == {"extractions": 1, "lines": 2, "same": 2, "fixed": 0}
    assert dashboard.reading_match(extraction=read_order()) is None  # 受注にしていない読み取りは数えない
    assert dashboard.reading_match() is None


def test_match_counts_one_field_fixed(ordered, mapping, user):
    extraction, order, first, second = ordered()
    orders_services.update_order(order, {}, [{"id": first.pk, "qty": Decimal("12")}, {"id": second.pk}], user)
    match = dashboard.reading_match(extraction=extraction)
    assert (match["lines"], match["same"], match["fixed"]) == (2, 1, 1)

    extraction, order, first, second = ordered()
    orders_services.update_order(order, {}, [{"id": first.pk}, {"id": second.pk, "due_date": date(2026, 11, 30)}], user)
    match = dashboard.reading_match(extraction=extraction)
    assert (match["lines"], match["same"], match["fixed"]) == (2, 1, 1)

    extraction, order, first, _second = ordered()
    orders_services.update_order(order, {}, [{"id": first.pk}], user)  # 2 行目を消す
    match = dashboard.reading_match(extraction=extraction)
    assert (match["lines"], match["same"], match["fixed"]) == (2, 1, 1)

    extraction, order, first, second = ordered()
    added = {"item": mapping["X-100"], "qty": Decimal("3"), "unit_price": Decimal("150")}
    orders_services.update_order(order, {}, [{"id": first.pk}, {"id": second.pk}, added], user)  # 人が足した行
    match = dashboard.reading_match(extraction=extraction)
    assert (match["lines"], match["same"], match["fixed"]) == (2, 2, 0)


def test_match_ignores_width_and_spaces(ordered, user):
    extraction, order, first, second = ordered()
    orders_services.update_order(
        order, {}, [{"id": first.pk, "customer_part_no": "Ｘ－１００ "}, {"id": second.pk}], user
    )
    assert dashboard.reading_match(extraction=extraction)["same"] == 2

    orders_services.update_order(order, {}, [{"id": first.pk, "customer_part_no": "X-101"}, {"id": second.pk}], user)
    match = dashboard.reading_match(extraction=extraction)
    assert (match["same"], match["fixed"]) == (1, 1)


def test_match_counts_two_queries(ordered, customer):
    for _ in range(3):
        ordered()

    with CaptureQueriesContext(connection) as captured:
        match = dashboard.reading_match(customer=customer)
    assert len(captured.captured_queries) <= 2
    assert match == {"extractions": 3, "lines": 6, "same": 6, "fixed": 0}


def test_match_screens_show_counts(client, user, ordered, read_order):
    client.force_login(user)
    extraction, _order, _first, _second = ordered()

    body = client.get(reverse("intake:extraction_detail", args=[extraction.pk])).content.decode()
    assert "この読み取り: 明細 2 行中 2 行そのまま (人が直したのは 0 行)" in body
    assert "この得意先の読み取り (これまで)" in body

    waiting = read_order()
    for url in (
        reverse("intake:extraction_detail", args=[waiting.pk]),
        reverse("intake:extraction_accept", args=[waiting.pk]),
    ):
        body = client.get(url).content.decode()
        assert "この得意先の読み取り (これまで): 明細 2 行中 2 行そのまま" in body, url
        assert "この読み取り:" not in body, url

    other = create_partner({"code": "IO02", "name": "作り物の別の得意先", "role": "customer"}, user)
    body = client.get(reverse("intake:extraction_detail", args=[read_order(other).pk])).content.decode()
    assert "この読み取り:" not in body
    assert "この得意先の読み取り (これまで)" not in body
