"""協力企業に工程を出した発注の受入は、完成品の在庫の入庫にしない (N378)。

精査 (事務の 1 人の 1 日) の番号 2。指示書の製造の完了で入庫にした部品に、協力企業の工程の発注の受入を入れると、
同じ現物が 2 回数えられていた。工程の無い発注の受入 (買った部品や完成品) は今までどおり入庫にする。
受注・指示書・実績・発注・受入は services で作る。日は固定の値を渡す。作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.progress import services as progress_services
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import PurchaseOrder, Receipt
from seizou.stock import services
from seizou.stock.models import KIND_FINISHED, KIND_RECEIVED, StockMove
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
DAY1 = date(2026, 11, 3)
DAY2 = date(2026, 11, 4)
DAY3 = date(2026, 11, 5)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor", "rounding": "round"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def processes(user):
    """社内の工程 2 つと、協力企業に出す工程 1 つ (再現の手順の標準の工程の並び)。"""
    return {
        "milling": masters_services.create_process({"code": "MILL", "name": "フライス加工", "sort_order": 1}, user),
        "surface": masters_services.create_process(
            {"code": "SURF", "name": "表面処理", "sort_order": 2, "is_outsourced": True}, user
        ),
        "inspect": masters_services.create_process({"code": "INSP", "name": "検査", "sort_order": 3}, user),
    }


@pytest.fixture
def no_http(monkeypatch):
    """HTTP を呼んだら落とす (つながりの無い協力企業の発注は送らない)。"""

    def fail(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


def _order(subcontractor, user, item, qty, unit_price, *, process=None, send=True):
    """明細 1 行の発注。process を渡せば明細に協力企業に出す工程を入れる。"""
    line = {"item": item, "qty": Decimal(qty), "unit_price": Decimal(unit_price)}
    if process is not None:
        line["process"] = process
    return purchasing_services.create_purchase_order(subcontractor, [line], user, order_date=ORDER_DATE, send=send)


@pytest.mark.django_db
def test_receipt_on_process_line_keeps_receipt_without_stock_move(user, subcontractor, item, processes, no_http):
    """(a) 工程のある明細の受入は残り (金額と支払の見込みも今までどおり)、在庫の動きは増えない。"""
    po = _order(subcontractor, user, item, "10", "25.5", process=processes["surface"])

    receipt = purchasing_services.receive(po.lines.get(), Decimal("4"), DAY2, user, note="表面処理の戻り")

    assert Receipt.objects.count() == 1
    assert receipt.amount == Decimal("102")  # 4 × 25.5 = 102 (四捨五入の取引先)
    assert receipt.received_on == DAY2 and receipt.qty == Decimal("4")
    forecast = purchasing_services.payable_by_partner(today=DAY2)
    assert [(row["partner"], row["this_month"]) for row in forecast["rows"]] == [(subcontractor, Decimal("102"))]
    assert StockMove.objects.count() == 0
    assert services.balance(item) is None  # 動きの無い部品は在庫を数えないまま


@pytest.mark.django_db
def test_record_received_returns_none_for_process_line(user, subcontractor, item, processes, no_http):
    """(a) record_received を直に呼んでも、工程のある受入は動きを作らずに None を返す。"""
    po = _order(subcontractor, user, item, "10", "25", process=processes["surface"])
    receipt = purchasing_services.receive(po.lines.get(), Decimal("4"), DAY2, user)

    assert services.record_received(receipt, user) is None
    assert StockMove.objects.count() == 0


@pytest.mark.django_db
def test_receipt_without_process_still_makes_stock_move(user, subcontractor, item, no_http):
    """(b) 工程の無い発注の受入は、今までどおり入庫の動きを 1 つ作る。"""
    po = _order(subcontractor, user, item, "10", "25")

    receipt = purchasing_services.receive(po.lines.get(), Decimal("4"), DAY2, user, note="買った部品")

    move = StockMove.objects.get()
    assert move.kind == KIND_RECEIVED
    assert move.qty == Decimal("4") and move.moved_on == DAY2
    assert move.source == receipt
    assert move.note == "買った部品"
    assert services.balance(item) == Decimal("4")


@pytest.mark.django_db
def test_finished_then_process_receipts_count_once(user, customer, subcontractor, item, processes, no_http):
    """(c) 再現の手順の形。製造の完了で 98 を入庫にした部品に、工程のある受入 60 と 50 を入れても在庫数は 98 のまま。"""
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("100"), "unit_price": Decimal("300")}], user, order_date=ORDER_DATE
    )
    steps = [processes["milling"], processes["surface"], processes["inspect"]]
    workorder = workorders_services.issue_workorder(
        order.lines.get(), Decimal("100"), [{"process": p, "planned_qty": Decimal("100")} for p in steps], user
    )
    for step in workorder.steps.order_by("seq"):
        progress_services.record_progress(step, Decimal("98"), timezone.now(), user, completes_step=True)
    services.record_finished(workorder, Decimal("98"), user, moved_on=DAY1)

    po = _order(subcontractor, user, item, "100", "25", process=processes["surface"])
    line = po.lines.get()
    first = purchasing_services.receive(line, Decimal("60"), DAY2, user)
    second = purchasing_services.receive(line, Decimal("50"), DAY3, user)

    assert (first.amount, second.amount) == (Decimal("1500"), Decimal("1250"))
    assert list(StockMove.objects.values_list("kind", "qty")) == [(KIND_FINISHED, Decimal("98"))]
    assert services.balance(item) == Decimal("98")
    assert services.balances([item]) == {item.pk: Decimal("98")}


@pytest.mark.django_db
def test_process_line_reaches_delivered(user, subcontractor, item, processes, no_http):
    """(d) 工程のある発注も、受入の数が発注の数に届けば「納品された」になる。"""
    po = _order(subcontractor, user, item, "100", "25", process=processes["surface"])
    line = po.lines.get()

    purchasing_services.receive(line, Decimal("60"), DAY2, user)
    purchasing_services.receive(line, Decimal("50"), DAY3, user)

    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_DELIVERED
    assert po.get_status_display() == "納品された"


@pytest.mark.django_db
def test_process_line_partial_on_accepted_order(user, subcontractor, item, processes):
    """(d) 「相手が受け入れた」の工程のある発注に一部の受入が入ると「一部納品」、残りが届けば「納品された」。"""
    po = _order(subcontractor, user, item, "100", "25", process=processes["surface"], send=False)
    accepted = purchasing_services.mark_accepted(purchasing_services.mark_sent(po, user), user)
    line = accepted.lines.get()

    purchasing_services.receive(line, Decimal("60"), DAY2, user)
    accepted.refresh_from_db()
    assert accepted.status == PurchaseOrder.STATUS_PARTIAL
    assert accepted.get_status_display() == "一部納品"

    purchasing_services.receive(line, Decimal("40"), DAY3, user)
    accepted.refresh_from_db()
    assert accepted.status == PurchaseOrder.STATUS_DELIVERED
    assert StockMove.objects.count() == 0
