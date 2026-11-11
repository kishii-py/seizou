"""協力企業への発注の明細を指示書の工程に結ぶ (見比べのまとめの M01。N406 の 1 段目)。

発注の新規登録の明細の鍵 workorder_step と handed_on (部品と工程は指示書の工程から写す)・渡した日を直す set_handed_on・
読むだけの関数 (out_qty・waiting_qty・annotate_subcontract・step_lines・steps_to_subcontract・default_subcontractor) と、
指示書の工程を指す明細の発注を相手へ送るときの明細の項目を見る。
作り物のデータは services で作る。相手の受け口への HTTP は requests.post を偽の物に差し替える (外へは出ない)。
"""

import json
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from seizou.connect import services as connect_services
from seizou.core.models import ChangeLog
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.progress import services as progress_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrderLine
from seizou.workorders import services as workorders_services
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

ORDER_DATE = date(2026, 11, 2)
HANDED = date(2026, 11, 4)
DAY = date(2026, 11, 5)
ENV_NAME = "SEIZOU_PARTNER_PLATESUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"
#: 今の送る明細の項目の鍵 (connect.services.send_order。指示書の工程と渡した日は送らない)
SENT_LINE_KEYS = {
    "line_no",
    "part_no",
    "part_name",
    "qty",
    "unit",
    "unit_code",
    "unit_price",
    "price_basis",
    "due_date",
    "note",
    "line_status_code",
    "line_status_reason_code",
    "process_name",
    "quote_no",
}


def build_workorder(user, *, code="1"):
    """作り物の受注 (数量 10) と指示書 (工程 3 つ。2 つ目のめっきが協力企業に出す工程) と協力企業を作る。

    返す物の step は 2 つ目の工程。code を変えると、別の得意先・部品・工程の組で作る (同じ試験の中で 2 つ作るとき)。
    """
    customer = masters_services.create_partner(
        {"code": f"C00{code}", "name": f"作り物の得意先 {code}", "role": "customer"}, user
    )
    subcontractor = masters_services.create_partner(
        {"code": f"S00{code}", "name": f"作り物のめっき屋 {code}", "role": "subcontractor"}, user
    )
    item = masters_services.create_item({"part_no": f"T-{code}00", "name": f"作り物の軸 {code}", "unit": "個"}, user)
    cut = masters_services.create_process({"code": f"CUT{code}", "name": "切断"}, user)
    plate = masters_services.create_process({"code": f"PLATE{code}", "name": "めっき", "is_outsourced": True}, user)
    check = masters_services.create_process({"code": f"INSP{code}", "name": "検査"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    workorder = workorders_services.issue_workorder(
        order.lines.get(),
        Decimal("10"),
        [{"process": process, "planned_qty": Decimal("10")} for process in (cut, plate, check)],
        user,
    )
    steps = list(workorder.steps.order_by("seq"))
    return SimpleNamespace(
        customer=customer,
        subcontractor=subcontractor,
        item=item,
        processes=(cut, plate, check),
        order=order,
        workorder=workorder,
        steps=steps,
        step=steps[1],
    )


def subcontract_po(user, partner, step, qty="10", *, handed_on=HANDED, **extra):
    """指示書の工程を指す明細 1 行の発注を作り、(発注, 明細) を返す。handed_on が None なら渡した日を入れない。"""
    line = {"workorder_step": step, "qty": Decimal(qty), "unit_price": Decimal("30"), **extra}
    if handed_on is not None:
        line["handed_on"] = handed_on
    po = services.create_purchase_order(partner, [line], user, order_date=ORDER_DATE)
    return po, po.lines.get()


def line_of(pk):
    return PurchaseOrderLine.objects.select_related("purchase_order").get(pk=pk)


@pytest.fixture
def wo(user):
    return build_workorder(user)


@pytest.fixture
def fake(monkeypatch):
    post = FakePost()
    monkeypatch.setattr("requests.post", post)
    return post


@pytest.fixture
def sent_partner(user, monkeypatch, fake):
    """向き「送る」のつながりのある協力企業 (seizou どうし。送る物は偽の requests.post が受ける)。"""
    monkeypatch.setenv(ENV_NAME, _new_secret())
    partner = masters_services.create_partner({"code": "S090", "name": "作り物のめっき屋 送る", "role": "subcontractor"}, user)
    connect_services.register_connection(partner, PARTNER_URL, ENV_NAME, "send", user)
    return partner


def _wo_text(wo):
    return f"指示書 {wo.workorder.wo_no} の工程 2 めっき"


# ---------------------------------------------------------------- 3 発注を作る (写しと断り)


@pytest.mark.django_db
def test_create_copies_item_and_process_from_step(user, wo):
    po, line = subcontract_po(user, wo.subcontractor, wo.step)
    assert line.workorder_step == wo.step
    assert line.item == wo.item
    assert line.process == wo.processes[1]
    assert line.handed_on == HANDED
    assert line.line_no == 1 and line.qty == Decimal("10")


@pytest.mark.django_db
def test_create_without_handed_on_keeps_it_empty(user, wo):
    _po, line = subcontract_po(user, wo.subcontractor, wo.step, handed_on=None)
    assert line.workorder_step == wo.step
    assert line.handed_on is None


@pytest.mark.django_db
def test_create_accepts_same_item_and_process(user, wo):
    _po, line = subcontract_po(user, wo.subcontractor, wo.step, item=wo.item, process=wo.processes[1])
    assert line.item == wo.item and line.process == wo.processes[1]


@pytest.mark.django_db
def test_create_refuses_other_item(user, wo):
    other = masters_services.create_item({"part_no": "T-900", "name": "作り物の別の部品", "unit": "個"}, user)
    with pytest.raises(ValueError) as caught:
        subcontract_po(user, wo.subcontractor, wo.step, item=other)
    assert str(caught.value) == (
        f"保存できません。明細 1 の部品が、{_wo_text(wo)} と違います。"
        "指示書の工程から出すときは、部品と工程を変えずに保存してください。"
    )
    assert not PurchaseOrderLine.objects.exists()


@pytest.mark.django_db
def test_create_refuses_other_process(user, wo):
    with pytest.raises(ValueError) as caught:
        subcontract_po(user, wo.subcontractor, wo.step, process=wo.processes[0])
    assert str(caught.value) == (
        f"保存できません。明細 1 の工程が、{_wo_text(wo)} と違います。"
        "指示書の工程から出すときは、部品と工程を変えずに保存してください。"
    )
    with pytest.raises(ValueError, match="明細 1 の工程が"):
        subcontract_po(user, wo.subcontractor, wo.step, process=None)
    assert not PurchaseOrderLine.objects.exists()


@pytest.mark.django_db
def test_create_refuses_handed_on_without_step(user, wo):
    with pytest.raises(ValueError) as caught:
        services.create_purchase_order(
            wo.subcontractor,
            [
                {"item": wo.item, "qty": Decimal("1"), "unit_price": Decimal("30")},
                {"item": wo.item, "qty": Decimal("2"), "unit_price": Decimal("30"), "handed_on": HANDED},
            ],
            user,
            order_date=ORDER_DATE,
        )
    assert str(caught.value).startswith("保存できません。渡した日は、指示書の工程を指す明細だけに入れられます。")
    assert not PurchaseOrderLine.objects.exists()


@pytest.mark.django_db
def test_create_refuses_inactive_copied_item(user, wo):
    masters_services.update_item(wo.item, {"active": False}, user)
    with pytest.raises(ValueError, match="使用停止の部品です"):
        subcontract_po(user, wo.subcontractor, wo.step)


@pytest.mark.django_db
def test_create_does_not_refuse_plain_step_cancelled_workorder_or_two_lines(user, wo):
    """協力企業に出す印の無い工程・取消の指示書・1 つの工程に明細 2 つは断らない (入口が絞る)。"""
    _po, first = subcontract_po(user, wo.subcontractor, wo.steps[0])
    assert first.process == wo.processes[0]
    subcontract_po(user, wo.subcontractor, wo.step)
    subcontract_po(user, wo.subcontractor, wo.step)
    assert PurchaseOrderLine.objects.filter(workorder_step=wo.step).count() == 2
    workorders_services.cancel_workorder(wo.workorder, user)
    _po, after = subcontract_po(user, wo.subcontractor, wo.steps[2])
    assert after.workorder_step == wo.steps[2]


@pytest.mark.django_db
def test_change_refuses_workorder_step_as_unknown_key(user, wo, sent_partner):
    po, line = subcontract_po(user, sent_partner, wo.step)
    for lines in (
        [{"line": line, "workorder_step": wo.step}],
        [{"line": line}, {"item": wo.item, "qty": Decimal("1"), "unit_price": Decimal("30"), "workorder_step": wo.step}],
        [{"line": line}, {"item": wo.item, "qty": Decimal("1"), "unit_price": Decimal("30"), "handed_on": HANDED}],
    ):
        with pytest.raises(ValueError, match="知らない項目"):
            services.change_purchase_order(po, {}, lines, user)


@pytest.mark.django_db
def test_change_added_line_has_no_step(user, wo, sent_partner):
    """送った発注の変更で足す明細は、今までどおり指示書の工程を持たない。"""
    po, line = subcontract_po(user, sent_partner, wo.step)
    services.change_purchase_order(
        po, {}, [{"line": line}, {"item": wo.item, "qty": Decimal("1"), "unit_price": Decimal("30")}], user
    )
    added = po.lines.get(line_no=2)
    assert added.workorder_step is None and added.handed_on is None
    assert line_of(line.pk).workorder_step == wo.step


# ---------------------------------------------------------------- 4 渡した日を直す


@pytest.mark.django_db
def test_set_handed_on_writes_history_and_message(user, wo):
    po, line = subcontract_po(user, wo.subcontractor, wo.step, handed_on=None)
    result = services.set_handed_on(line, HANDED, user)
    assert result.saved_message == f"{po.po_no}-1 の渡した日を 2026-11-04 にしました。"
    assert line_of(line.pk).handed_on == HANDED
    log = ChangeLog.objects.filter(model="purchasing.purchaseorderline", object_pk=line.pk, action="updated").get()
    assert [change["field"] for change in log.changes] == ["handed_on"]

    cleared = services.set_handed_on(line, None, user)
    assert cleared.saved_message == f"{po.po_no}-1 の渡した日を空にしました (まだ渡していない扱いです)。"
    assert line_of(line.pk).handed_on is None


@pytest.mark.django_db
def test_set_handed_on_allows_completed_and_received_lines(user, wo):
    _po, line = subcontract_po(user, wo.subcontractor, wo.step, handed_on=None)
    services.receive(line, Decimal("3"), DAY, user)
    services.mark_completed(line, user)
    services.set_handed_on(line, HANDED, user)
    assert line_of(line.pk).handed_on == HANDED


@pytest.mark.django_db
def test_set_handed_on_refuses_line_without_step(user, wo):
    po = services.create_purchase_order(
        wo.subcontractor, [{"item": wo.item, "qty": Decimal("1"), "unit_price": Decimal("30")}], user, order_date=ORDER_DATE
    )
    with pytest.raises(ValueError) as caught:
        services.set_handed_on(po.lines.get(), HANDED, user)
    assert str(caught.value).startswith("保存できません。渡した日は、指示書の工程を指す明細だけに入れられます。")


@pytest.mark.django_db
def test_set_handed_on_refuses_cancelled_order(user, wo):
    po, line = subcontract_po(user, wo.subcontractor, wo.step, handed_on=None)
    services.cancel_purchase_order(po, user)
    with pytest.raises(ValueError) as caught:
        services.set_handed_on(line, HANDED, user)
    assert str(caught.value).startswith("保存できません。")
    assert line_of(line.pk).handed_on is None


@pytest.mark.django_db
def test_set_handed_on_refuses_cancelled_line(user, wo, sent_partner):
    po = services.create_purchase_order(
        sent_partner,
        [
            {"workorder_step": wo.step, "qty": Decimal("10"), "unit_price": Decimal("30")},
            {"workorder_step": wo.step, "qty": Decimal("5"), "unit_price": Decimal("30")},
        ],
        user,
        order_date=ORDER_DATE,
    )
    first, second = po.lines.order_by("line_no")
    services.change_purchase_order(po, {}, [{"line": first}], user)
    assert line_of(second.pk).is_cancelled
    with pytest.raises(ValueError) as caught:
        services.set_handed_on(second, HANDED, user)
    assert str(caught.value).startswith("保存できません。")
    assert line_of(second.pk).handed_on is None


# ---------------------------------------------------------------- 7 読むだけの関数 (出ている数・まだ渡していない数)


@pytest.mark.django_db
def test_out_and_waiting_qty(user, wo, sent_partner):
    _po, handed = subcontract_po(user, wo.subcontractor, wo.step)
    _po, waiting = subcontract_po(user, wo.subcontractor, wo.step, "6", handed_on=None)
    assert services.out_qty(line_of(handed.pk)) == Decimal("10")
    assert services.waiting_qty(line_of(handed.pk)) == Decimal("0")
    assert services.out_qty(line_of(waiting.pk)) == Decimal("0")
    assert services.waiting_qty(line_of(waiting.pk)) == Decimal("6")

    # 受け入れた分は出ている数から引く。多く戻った明細は 0 (マイナスにしない)
    services.receive(handed, Decimal("4"), DAY, user)
    assert services.out_qty(line_of(handed.pk)) == Decimal("6")
    services.receive(handed, Decimal("8"), DAY, user)
    assert services.out_qty(line_of(handed.pk)) == Decimal("0")

    # 完了の明細は 0
    services.receive(waiting, Decimal("1"), DAY, user)
    services.mark_completed(waiting, user)
    assert services.waiting_qty(line_of(waiting.pk)) == Decimal("0")

    # 指示書の工程の無い明細は 0
    plain = services.create_purchase_order(
        wo.subcontractor, [{"item": wo.item, "qty": Decimal("3"), "unit_price": Decimal("30")}], user, order_date=ORDER_DATE
    ).lines.get()
    assert services.out_qty(plain) == Decimal("0") and services.waiting_qty(plain) == Decimal("0")


@pytest.mark.django_db
def test_out_and_waiting_qty_are_zero_for_cancelled(user, wo, sent_partner):
    cancelled_po, cancelled = subcontract_po(user, wo.subcontractor, wo.step)
    services.cancel_purchase_order(cancelled_po, user)
    assert services.out_qty(line_of(cancelled.pk)) == Decimal("0")
    _po, cancelled_waiting = subcontract_po(user, wo.subcontractor, wo.step, handed_on=None)
    services.cancel_purchase_order(cancelled_waiting.purchase_order, user)
    assert services.waiting_qty(line_of(cancelled_waiting.pk)) == Decimal("0")

    po = services.create_purchase_order(
        sent_partner,
        [
            {"workorder_step": wo.step, "qty": Decimal("10"), "unit_price": Decimal("30"), "handed_on": HANDED},
            {"workorder_step": wo.step, "qty": Decimal("5"), "unit_price": Decimal("30"), "handed_on": HANDED},
            {"workorder_step": wo.step, "qty": Decimal("5"), "unit_price": Decimal("30")},
        ],
        user,
        order_date=ORDER_DATE,
    )
    first, second, third = po.lines.order_by("line_no")
    services.change_purchase_order(po, {}, [{"line": first}], user)
    assert line_of(second.pk).is_cancelled and line_of(third.pk).is_cancelled
    assert services.out_qty(line_of(second.pk)) == Decimal("0")
    assert services.waiting_qty(line_of(third.pk)) == Decimal("0")
    assert services.out_qty(line_of(first.pk)) == Decimal("10")


@pytest.mark.django_db
def test_annotate_subcontract_matches_line_functions(user, wo):
    other = build_workorder(user, code="2")
    _po, a = subcontract_po(user, wo.subcontractor, wo.step)
    _po, b = subcontract_po(user, wo.subcontractor, wo.step, "4", handed_on=None)
    _po, c = subcontract_po(user, wo.subcontractor, wo.step, "7")
    services.receive(a, Decimal("3"), DAY, user)
    services.receive(c, Decimal("9"), DAY, user)  # 多く戻った明細
    cancelled_po, _d = subcontract_po(user, wo.subcontractor, wo.step, "5")
    services.cancel_purchase_order(cancelled_po, user)
    _po, e = subcontract_po(user, other.subcontractor, other.step, "2")

    def _expected(step):
        lines = [line_of(row.pk) for row in PurchaseOrderLine.objects.filter(workorder_step=step)]
        return (
            sum((services.out_qty(line) for line in lines), Decimal("0")),
            sum((services.waiting_qty(line) for line in lines), Decimal("0")),
            sum(1 for line in lines if not line.is_cancelled and line.purchase_order.status != "cancelled"),
        )

    StepModel = type(wo.step)
    rows = {row.pk: row for row in services.annotate_subcontract(StepModel.objects.all())}
    for step in [*wo.steps, *other.steps]:
        row = rows[step.pk]
        got = (Decimal(row.subcontract_out_total), Decimal(row.subcontract_waiting_total), row.subcontract_line_count)
        assert got == _expected(step)
    assert (rows[wo.step.pk].subcontract_out_total, rows[wo.step.pk].subcontract_waiting_total) == (Decimal("7"), Decimal("4"))
    assert rows[wo.step.pk].subcontract_line_count == 3
    assert rows[other.step.pk].subcontract_out_total == Decimal("2")
    assert rows[wo.steps[0].pk].subcontract_line_count == 0


@pytest.mark.django_db
def test_annotate_subcontract_query_count_does_not_grow(user, wo):
    subcontract_po(user, wo.subcontractor, wo.step)
    StepModel = type(wo.step)

    def _count():
        with CaptureQueriesContext(connection) as captured:
            list(services.annotate_subcontract(StepModel.objects.all()))
        return len(captured.captured_queries)

    before = _count()
    other = build_workorder(user, code="2")
    subcontract_po(user, other.subcontractor, other.step)
    assert _count() == before == 1


@pytest.mark.django_db
def test_step_lines(user, wo):
    _po, first = subcontract_po(user, wo.subcontractor, wo.step)
    _po, second = subcontract_po(user, wo.subcontractor, wo.step, "4", handed_on=None)
    cancelled_po, _third = subcontract_po(user, wo.subcontractor, wo.step)
    services.cancel_purchase_order(cancelled_po, user)
    services.receive(first, Decimal("3"), DAY, user)
    rows = services.step_lines(wo.step)
    assert [row.pk for row in rows] == [first.pk, second.pk]
    assert [(row.received_total, row.remaining_total) for row in rows] == [
        (Decimal("3"), Decimal("7")),
        (Decimal("0"), Decimal("4")),
    ]


@pytest.mark.django_db
def test_steps_to_subcontract(user, wo):
    other = build_workorder(user, code="2")
    third = build_workorder(user, code="3")
    fourth = build_workorder(user, code="4")
    pks = lambda: [step.pk for step in services.steps_to_subcontract()]  # noqa: E731
    # 協力企業に出す工程 (めっき) だけ
    assert pks() == [wo.step.pk, other.step.pk, third.step.pk, fourth.step.pk]

    # 取消の指示書と、取消の受注の指示書を除く
    workorders_services.cancel_workorder(other.workorder, user)
    orders_services.cancel_order(third.order, user)
    assert pks() == [wo.step.pk, fourth.step.pk]

    # 取り消していない明細のある工程を除き、発注を取り消すと戻る
    po, _line = subcontract_po(user, wo.subcontractor, wo.step, handed_on=None)
    assert pks() == [fourth.step.pk]
    services.cancel_purchase_order(po, user)
    assert pks() == [wo.step.pk, fourth.step.pk]

    # 工程の完了の印の付いた実績のある工程を除く
    progress_services.record_progress(fourth.step, Decimal("10"), timezone.now(), user, completes_step=True)
    assert pks() == [wo.step.pk]


@pytest.mark.django_db
def test_default_subcontractor(user, wo):
    plate = wo.processes[1]
    first = masters_services.create_partner({"code": "S101", "name": "作り物のめっき屋 甲", "role": "subcontractor"}, user)
    second = masters_services.create_partner({"code": "S102", "name": "作り物のめっき屋 乙", "role": "both"}, user)

    def _price(partner=None, process=plate, start_date=None, kind="purchase"):
        partner_item = None
        if partner is not None:
            partner_item = masters_services.add_partner_item({"partner": partner, "item": wo.item}, user)
        data = {"item": wo.item, "partner_item": partner_item, "kind": kind, "process": process, "unit_price": Decimal("30")}
        if start_date is not None:
            data["start_date"] = start_date
        return masters_services.add_item_price(data, user)

    # 共通の行だけなら None
    _price()
    assert services.default_subcontractor(wo.item, plate) is None
    # 1 社ならその取引先 (売りの行と、ほかの工程の行は数えない)
    _price(first)
    _price(second, kind="sale")
    _price(second, process=wo.processes[0])
    assert services.default_subcontractor(wo.item, plate) == first
    # 適用開始日が先の行は数えない (on_date の日なら数える)
    later = timezone.localdate() + timedelta(days=30)
    _price(second, start_date=later)
    assert services.default_subcontractor(wo.item, plate) == first
    assert services.default_subcontractor(wo.item, plate, on_date=later) is None
    # 2 社なら None
    _price(second)
    assert services.default_subcontractor(wo.item, plate) is None
    # 使用停止の取引先は数えない
    masters_services.update_partner(second, {"active": False}, user)
    assert services.default_subcontractor(wo.item, plate) == first


@pytest.mark.django_db
def test_default_subcontractor_skips_customer_only_partner(user, wo):
    plate = wo.processes[1]
    customer_item = masters_services.add_partner_item({"partner": wo.customer, "item": wo.item}, user)
    masters_services.add_item_price(
        {"item": wo.item, "partner_item": customer_item, "kind": "purchase", "process": plate, "unit_price": Decimal("30")}, user
    )
    assert services.default_subcontractor(wo.item, plate) is None


# ---------------------------------------------------------------- 11 送り合いは変えない


@pytest.mark.django_db
def test_sent_line_keys_do_not_change(user, wo, sent_partner, fake):
    po = services.create_purchase_order(
        sent_partner,
        [
            {"workorder_step": wo.step, "qty": Decimal("10"), "unit_price": Decimal("30"), "handed_on": HANDED},
            {"item": wo.item, "qty": Decimal("2"), "unit_price": Decimal("30")},
        ],
        user,
        order_date=ORDER_DATE,
    )
    assert po.status == po.STATUS_SENT
    body = json.loads(fake.calls[-1]["data"])
    assert [set(line) for line in body["lines"]] == [SENT_LINE_KEYS, SENT_LINE_KEYS]
    assert body["lines"][0]["process_name"] == "めっき"
