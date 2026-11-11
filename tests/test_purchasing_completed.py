"""発注の明細の「完了」の印 (見比べのまとめの M03。本人の決め 11-11 08:4x・09:3x。RULES.md R25)。

印は人だけが付け外しする (purchasing.services.mark_completed・clear_completed と、受入の「残りは受け入れずに完了にする」= receive の close_rest)。
受入と発注の「変更」では書き換えない。完了の明細は足りない分を受入の残に数えないので、「納品された」の判定と、発注の一覧の「受入の残」が従う。
外した時は、発注の状態を受入の有る無しと注文回答のタイプから決め直す。相手の seizou へは何も送らない。
相手の受け口への HTTP は requests.post を偽の物に差し替える (外へは出ない)。作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal

import pytest

from seizou.connect import services as connect_services
from seizou.core.models import ChangeLog
from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder, PurchaseOrderLine, Receipt
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

ORDER_DATE = date(2026, 11, 2)
DAY = date(2026, 11, 5)
ENV_NAME = "SEIZOU_PARTNER_DONESUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"
SENT_NOTE = (
    "相手の seizou には伝わりません。相手にも止めてほしいときは、完了を外してから、"
    "発注の「変更」で数量を受入済みの数に直して送ってください。"
)


@pytest.fixture
def items(user):
    return [
        masters_services.create_item({"part_no": f"T-{n}00", "name": f"作り物の部品 {n}", "unit": "個"}, user)
        for n in range(1, 4)
    ]


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def fake(monkeypatch):
    post = FakePost()
    monkeypatch.setattr("requests.post", post)
    return post


@pytest.fixture
def sent_partner(user, monkeypatch, fake):
    monkeypatch.setenv(ENV_NAME, _new_secret())
    partner = masters_services.create_partner(
        {"code": "S002", "name": "作り物の協力企業 2", "role": "subcontractor"}, user
    )
    connect_services.register_connection(partner, PARTNER_URL, ENV_NAME, "send", user)
    return partner


def _po(user, partner, items, qtys, **extra):
    po = services.create_purchase_order(
        partner,
        [
            {"item": item, "qty": Decimal(qty), "unit_price": Decimal("30"), **extra}
            for item, qty in zip(items, qtys, strict=False)
        ],
        user,
        order_date=ORDER_DATE,
    )
    return po, list(po.lines.order_by("line_no"))


def _line(pk):
    return PurchaseOrderLine.objects.get(pk=pk)


def _status(po):
    po.refresh_from_db()
    return po.status


def _order_rest(po):
    return services.annotate_order_received(PurchaseOrder.objects.filter(pk=po.pk)).get()


# ---------------------------------------------------------------- 印は人だけが書く


@pytest.mark.django_db
def test_receipt_reaching_qty_keeps_mark_false(user, subcontractor, items):
    po, (line,) = _po(user, subcontractor, items, ["100"])
    services.receive(line, Decimal("100"), DAY, user)

    row = _line(line.pk)
    assert row.completed is False
    assert services.remaining_qty(row) == Decimal("0")
    assert _status(po) == PurchaseOrder.STATUS_DELIVERED


@pytest.mark.django_db
def test_change_does_not_write_mark(user, sent_partner, items):
    po, (first, second, third) = _po(user, sent_partner, items, ["100", "10", "5"])
    services.receive(first, Decimal("98"), DAY, user)

    # 数量を受入済みの数に直して終えても、印は書かない
    services.change_purchase_order(
        po, {}, [{"line": _line(first.pk), "qty": Decimal("98")}, {"line": second}, {"line": third}], user
    )
    assert _line(first.pk).completed is False
    assert services.remaining_qty(_line(first.pk)) == Decimal("0")

    # 完了の明細の数量を直しても、印は外れない (3 行目が残るので「変更」を送れる)
    services.mark_completed(second, user)
    services.change_purchase_order(
        po, {}, [{"line": _line(first.pk)}, {"line": _line(second.pk), "qty": Decimal("12")}, {"line": third}], user
    )
    assert _line(second.pk).completed is True
    assert services.remaining_qty(_line(second.pk)) == Decimal("0")
    assert _status(po) == PurchaseOrder.STATUS_SENT


# ---------------------------------------------------------------- 受入の残と発注の一覧の「受入の残」


@pytest.mark.django_db
def test_order_receive_remaining_drops_only_the_unfilled(user, sent_partner, items):
    po, (first, second, third) = _po(user, sent_partner, items, ["100", "10", "5"])
    # 3 行目を取り消してから、取り消した明細に受け入れる (受入は断らない = 現物優先)
    services.change_purchase_order(po, {}, [{"line": first}, {"line": second}], user)
    assert _line(third.pk).is_cancelled
    services.receive(_line(third.pk), Decimal("3"), DAY, user)
    services.receive(first, Decimal("98"), DAY, user)

    rest = _order_rest(po)
    assert (rest.ordered_total, rest.received_total, rest.receive_remaining) == (
        Decimal("110"), Decimal("101"), Decimal("9")
    )

    services.mark_completed(first, user)
    rest = _order_rest(po)
    assert (rest.ordered_total, rest.received_total, rest.receive_remaining) == (
        Decimal("110"), Decimal("101"), Decimal("7")
    )

    services.clear_completed(first, user)
    assert _order_rest(po).receive_remaining == Decimal("9")


@pytest.mark.django_db
def test_order_receive_remaining_keeps_over_receipt_negative(user, subcontractor, items):
    po, (first, second) = _po(user, subcontractor, items, ["100", "10"])
    services.receive(first, Decimal("98"), DAY, user)
    services.mark_completed(first, user)
    services.receive(_line(first.pk), Decimal("5"), DAY, user)

    # 完了の 1 行目は受けすぎの -3、2 行目は残り 10
    assert _order_rest(po).receive_remaining == Decimal("7")
    assert services.remaining_qty(_line(second.pk)) == Decimal("10")


@pytest.mark.django_db
def test_cancelled_order_rest_stays_zero(user, subcontractor, items):
    po, (line,) = _po(user, subcontractor, items, ["100"])
    services.cancel_purchase_order(po, user)
    assert _order_rest(po).receive_remaining == Decimal("0")


# ---------------------------------------------------------------- 「納品された」と外した時の状態


@pytest.mark.django_db
def test_completing_the_rest_line_delivers(user, subcontractor, items):
    po, (first, second) = _po(user, subcontractor, items, ["100", "10"])
    services.receive(second, Decimal("10"), DAY, user)
    services.receive(first, Decimal("98"), DAY, user)
    assert _status(po) == PurchaseOrder.STATUS_DRAFT

    services.mark_completed(first, user)
    assert _status(po) == PurchaseOrder.STATUS_DELIVERED
    assert ChangeLog.objects.filter(model="purchasing.purchaseorder", object_pk=po.pk, action="updated").exists()


@pytest.mark.django_db
def test_receiving_the_other_line_after_completion_delivers(user, subcontractor, items):
    po, (first, second) = _po(user, subcontractor, items, ["100", "10"])
    services.receive(first, Decimal("98"), DAY, user)
    services.mark_completed(first, user)
    assert _status(po) == PurchaseOrder.STATUS_DRAFT

    services.receive(second, Decimal("10"), DAY, user)
    assert _status(po) == PurchaseOrder.STATUS_DELIVERED


@pytest.mark.django_db
def test_clear_after_receipts_on_accepted_order_is_partial(user, sent_partner, items):
    po, (first, second) = _po(user, sent_partner, items, ["100", "10"])
    services.mark_accepted(po, None)
    services.receive(first, Decimal("98"), DAY, user)
    assert _status(po) == PurchaseOrder.STATUS_PARTIAL
    services.receive(second, Decimal("10"), DAY, user)
    services.mark_completed(first, user)
    assert _status(po) == PurchaseOrder.STATUS_DELIVERED

    cleared = services.clear_completed(first, user)
    assert _status(po) == PurchaseOrder.STATUS_PARTIAL
    assert cleared.completed is False
    assert cleared.saved_message == f"{po.po_no}-1 の完了を外しました。残り 2 をまた受け入れられます。"


@pytest.mark.django_db
def test_clear_without_receipts_on_unsent_order_is_draft(user, subcontractor, items):
    po, (line,) = _po(user, subcontractor, items, ["100"])
    services.mark_completed(line, user)  # 受入が 0 件でも完了にできる
    assert _status(po) == PurchaseOrder.STATUS_DELIVERED

    services.clear_completed(line, user)
    assert _status(po) == PurchaseOrder.STATUS_DRAFT


@pytest.mark.django_db
def test_clear_on_sent_order_without_reply_is_sent(user, sent_partner, items):
    po, (line,) = _po(user, sent_partner, items, ["100"])
    services.receive(line, Decimal("98"), DAY, user)
    services.mark_completed(line, user)
    assert _status(po) == PurchaseOrder.STATUS_DELIVERED

    services.clear_completed(line, user)
    po.refresh_from_db()
    assert po.reply_type == ""
    assert po.status == PurchaseOrder.STATUS_SENT


# ---------------------------------------------------------------- 受入の「残りは受け入れずに完了にする」


@pytest.mark.django_db
def test_receive_close_rest_marks_the_short_line(user, subcontractor, items):
    po, (first, second) = _po(user, subcontractor, items, ["100", "10"])
    services.receive(first, Decimal("98"), DAY, user)

    receipt = services.receive(_line(first.pk), Decimal("1"), DAY, user, close_rest=True)
    row = _line(first.pk)
    assert row.completed is True
    assert services.remaining_qty(row) == Decimal("0")
    assert services.unfilled_qty(row) == Decimal("1")
    assert services.received_qty(row) == Decimal("99")
    assert receipt.saved_message == (
        f"受入を保存しました。{po.po_no}-1 を完了にしました。残り 1 は受け入れずに終えます。"
        "また受け入れるときは、明細の ⋮ から完了を外してください。"
    )

    # 数量に届く受入では印を書かない
    services.receive(second, Decimal("10"), DAY, user, close_rest=True)
    assert _line(second.pk).completed is False
    assert _status(po) == PurchaseOrder.STATUS_DELIVERED


# ---------------------------------------------------------------- 断る


@pytest.mark.django_db
def test_cancelled_order_and_cancelled_line_are_refused(user, subcontractor, sent_partner, items):
    po, (line,) = _po(user, subcontractor, items, ["100"])
    services.cancel_purchase_order(po, user)
    with pytest.raises(ValueError, match="^保存できません。取り消した発注の明細は完了にできません。"):
        services.mark_completed(line, user)
    with pytest.raises(ValueError, match="^保存できません。取り消した発注の明細の完了は外せません。"):
        services.clear_completed(line, user)

    sent, (first, second) = _po(user, sent_partner, items, ["100", "10"])
    services.change_purchase_order(sent, {}, [{"line": first}], user)
    with pytest.raises(ValueError, match="^保存できません。取り消した明細は完了にできません。"):
        services.mark_completed(_line(second.pk), user)
    assert _line(second.pk).completed is False


@pytest.mark.django_db
def test_lump_line_without_amount_is_refused(user, subcontractor, items):
    po, (line,) = _po(user, subcontractor, items, ["10"], price_basis="lump")
    services.receive(line, Decimal("4"), DAY, user)
    assert Receipt.objects.get(line=line).amount == Decimal("0")

    with pytest.raises(ValueError, match="^保存できません。一式の明細で、受け入れた物に一式の金額がまだ載っていないので、完了にできません。"):
        services.mark_completed(line, user)
    assert _line(line.pk).completed is False

    # 受入の「残りは受け入れずに完了にする」も同じ断りで、受入ごと残らない
    with pytest.raises(ValueError, match="^保存できません。一式の明細で"):
        services.receive(_line(line.pk), Decimal("1"), DAY, user, close_rest=True)
    assert Receipt.objects.filter(line=line).count() == 1


@pytest.mark.django_db
def test_lump_line_without_receipt_can_be_completed(user, subcontractor, items):
    po, (line,) = _po(user, subcontractor, items, ["10"], price_basis="lump")
    marked = services.mark_completed(line, user)
    assert marked.completed is True
    assert services.unfilled_qty(_line(line.pk)) == Decimal("10")


# ---------------------------------------------------------------- 送った発注


@pytest.mark.django_db
def test_sent_order_completion_warns_and_sends_nothing(user, sent_partner, items, fake):
    po, (line,) = _po(user, sent_partner, items, ["100"])
    services.receive(line, Decimal("98"), DAY, user)
    calls = len(fake.calls)

    marked = services.mark_completed(line, user)
    assert len(fake.calls) == calls
    assert marked.saved_level == "warning"
    assert marked.saved_message == (
        f"{po.po_no}-1 を完了にしました。残り 2 は受け入れずに終えます。また受け入れるときは、明細の ⋮ から完了を外してください。"
        + SENT_NOTE
    )
    assert _status(po) == PurchaseOrder.STATUS_DELIVERED
    assert po.revision_no == 1


@pytest.mark.django_db
def test_unsent_order_completion_is_plain_notice(user, subcontractor, items):
    po, (line,) = _po(user, subcontractor, items, ["100"])
    services.receive(line, Decimal("98"), DAY, user)
    marked = services.mark_completed(line, user)
    assert getattr(marked, "saved_level", None) is None
    assert SENT_NOTE not in marked.saved_message


@pytest.mark.django_db
def test_mark_on_line_received_to_qty_writes_nothing(user, subcontractor, items):
    po, (line,) = _po(user, subcontractor, items, ["100"])
    services.receive(line, Decimal("100"), DAY, user)
    count = ChangeLog.objects.count()

    result = services.mark_completed(line, user)
    assert result.completed is False
    assert result.saved_message == f"{po.po_no}-1 は数量まで受け入れ済みで、終わっています。"
    assert ChangeLog.objects.count() == count
    assert _line(line.pk).completed is False
