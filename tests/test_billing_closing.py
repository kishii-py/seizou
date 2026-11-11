"""締めの一括の 1 段目 (依頼書 N402。billing.services の closing_date・closing_plan・create_closing_invoices)。

締日を選ぶと、その日が締日の得意先 (請求の仕方が「締日でまとめる」) の請求書を得意先ごとに 1 枚ずつ、1 つの transaction で作る。
- (a) 2026-10-31 で作ると、締日 31 の 2 社に 1 枚ずつできる (期間・明細・支払期日・知らせの字)
- (b) 締日 20 の得意先は 2026-10-20 の回に入る (期間は 2026-09-21〜2026-10-20)
- (c) 締日が空の得意先は作らずに "no_closing_day" の行で挙げる (黄の知らせ)
- (d) 2026-02-28 の回に締日 30 の得意先が入る。都度の得意先はどの回にも入らない
- (e) 同じ期間の請求書がもう在る得意先は作らない (全部がもう在れば ValueError)
- (f) 納品書を作っていない出荷も明細に入る
- (g) 金額を決めていない一式の明細が残る得意先は作らない (最後の出荷日が締める日より後の一式は止めない)
- (h) 「請求しない」の印の明細の出荷と、請求書の外で受け取った入金のある出荷は入らない
- (i) 期間より前と締める日より後の出荷は入らない
- (j) 締める日が今日より後なら何も読まずに止める
- (k) どれかの得意先で断られたら 1 枚も作らない (断りの文の頭に得意先の名前)
- (l) closing_plan の "make" の得意先と、作った請求書の得意先が同じ
- (m) closing_date の月末の寄せと、1〜31 の外の値
今日は全部の試験で 2026-11-11 に固定する。作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from seizou.billing import services
from seizou.billing.models import Invoice
from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from tests.billing_helpers import shipped

TODAY = date(2026, 11, 11)
ORDER_DATE = date(2026, 1, 5)
OCT_20 = date(2026, 10, 20)
OCT_31 = date(2026, 10, 31)
FEB_28 = date(2026, 2, 28)


@pytest.fixture(autouse=True)
def today(monkeypatch):
    """今日を 2026-11-11 に固定する (締める日が今日より後かを見る所が timezone.localdate() を読む)。"""
    monkeypatch.setattr("seizou.billing.services.timezone.localdate", lambda *args, **kwargs: TODAY)
    return TODAY


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _customer(user, code, closing_day, **extra):
    data = {
        "code": code,
        "name": f"作り物の得意先 {code}",
        "role": "customer",
        "closing_day": closing_day,
        "payment_month": "next",
        "payment_day": 31,
        **extra,
    }
    return masters_services.create_partner(data, user)


def _line(user, customer, item, qty="1000", price="100", price_basis=None):
    row = {"item": item, "qty": Decimal(qty), "unit_price": Decimal(price)}
    if price_basis:
        row["price_basis"] = price_basis
    order = orders_services.create_order(customer, [row], user, order_date=ORDER_DATE)
    return order.lines.get()


@pytest.fixture
def world(user, item):
    """締日 31 の 2 社 (A・B)・締日 20 の 1 社 (T)・締日が空の 1 社 (N)・締日 30 の 1 社 (M)・都度の 1 社 (P) と、その出荷。"""
    a = _customer(user, "C001", 31)
    b = _customer(user, "C002", 31)
    t = _customer(user, "C003", 20)
    n = _customer(user, "C004", None)
    m = _customer(user, "C005", 30)
    p = _customer(user, "C006", 31, billing_method="per_delivery")
    lines = {key: _line(user, partner, item) for key, partner in {"a": a, "b": b, "t": t, "n": n, "m": m, "p": p}.items()}
    ships = SimpleNamespace(
        a_oct05=shipped(user, a, lines["a"], "4", date(2026, 10, 5)),
        a_oct25=shipped(user, a, lines["a"], "6", date(2026, 10, 25)),
        b_oct10=shipped(user, b, lines["b"], "3", date(2026, 10, 10)),
        t_sep25=shipped(user, t, lines["t"], "2", date(2026, 9, 25)),
        t_oct15=shipped(user, t, lines["t"], "5", date(2026, 10, 15)),
        t_oct25=shipped(user, t, lines["t"], "1", date(2026, 10, 25)),
        n_oct12=shipped(user, n, lines["n"], "7", date(2026, 10, 12)),
        m_jan31=shipped(user, m, lines["m"], "1", date(2026, 1, 31)),
        m_feb10=shipped(user, m, lines["m"], "2", date(2026, 2, 10)),
        p_oct08=shipped(user, p, lines["p"], "1", date(2026, 10, 8)),
        p_feb10=shipped(user, p, lines["p"], "1", date(2026, 2, 10)),
    )
    return SimpleNamespace(a=a, b=b, t=t, n=n, m=m, p=p, lines=lines, ships=ships, item=item)


def _close(closing_on, user, issued_on=None):
    return services.create_closing_invoices(closing_on=closing_on, user=user, issued_on=issued_on or closing_on)


def _rows(closing_on, **kwargs):
    return {row["customer"].pk: row for row in services.closing_plan(closing_on, **kwargs)}


def _shipment_pks(invoice):
    return {line.shipment_id for line in invoice.lines.all()}


# ---------------------------------------------------------------- (a)


@pytest.mark.django_db
def test_a_month_end_closing_makes_one_invoice_per_customer(user, world):
    plan = _rows(OCT_31)

    result = _close(OCT_31, user)

    assert [invoice.customer_id for invoice in result] == [world.a.pk, world.b.pk]
    assert Invoice.objects.count() == 2
    for invoice in result:
        customer = world.a if invoice.customer_id == world.a.pk else world.b
        assert (invoice.period_from, invoice.period_to) == (date(2026, 10, 1), OCT_31)
        assert (invoice.period_from, invoice.period_to) == (plan[customer.pk]["period_from"], plan[customer.pk]["period_to"])
        assert invoice.due_on == services.payment_due_date(customer, OCT_31)
        assert invoice.issued_on == OCT_31
        assert invoice.status == Invoice.STATUS_ISSUED
    assert _shipment_pks(result[0]) == {world.ships.a_oct05.pk, world.ships.a_oct25.pk}
    assert _shipment_pks(result[1]) == {world.ships.b_oct10.pk}
    assert "2026-10-31 締めで 2 社の請求書を作りました" in result.saved_message
    assert f"({result[0].invoice_no}〜{result[1].invoice_no})" in result.saved_message


@pytest.mark.django_db
def test_a_issued_on_is_what_the_person_entered(user, world):
    result = _close(OCT_31, user, issued_on=date(2026, 11, 2))

    assert {invoice.issued_on for invoice in result} == {date(2026, 11, 2)}
    assert {invoice.due_on for invoice in result} == {services.payment_due_date(world.a, OCT_31)}


@pytest.mark.django_db
def test_a_inactive_customer_with_billable_shipments_is_made(user, world):
    """使用停止の得意先でも、請求できる出荷があれば作る (使用中の印は見ない)。"""
    masters_services.update_partner(world.b, {"active": False}, user)

    result = _close(OCT_31, user)

    assert world.b.pk in {invoice.customer_id for invoice in result}


# ---------------------------------------------------------------- (b)


@pytest.mark.django_db
def test_b_closing_day_20_is_in_the_20th_run_only(user, world):
    assert world.t.pk not in _rows(OCT_31)

    plan = _rows(OCT_20)
    result = _close(OCT_20, user)

    assert [invoice.customer_id for invoice in result] == [world.t.pk]
    invoice = result[0]
    assert (invoice.period_from, invoice.period_to) == (date(2026, 9, 21), OCT_20)
    assert (plan[world.t.pk]["period_from"], plan[world.t.pk]["period_to"]) == (date(2026, 9, 21), OCT_20)
    assert _shipment_pks(invoice) == {world.ships.t_sep25.pk, world.ships.t_oct15.pk}
    assert world.a.pk not in plan and world.b.pk not in plan


# ---------------------------------------------------------------- (c)


@pytest.mark.django_db
def test_c_customer_without_closing_day_is_listed_not_made(user, world, item):
    quiet = _customer(user, "C009", None)
    shipped(user, quiet, _line(user, quiet, item), "1", date(2026, 11, 1))

    plan = _rows(OCT_31)
    result = _close(OCT_31, user)

    row = plan[world.n.pk]
    assert row["state"] == "no_closing_day"
    assert "締日が空" in row["reason"]
    assert (row["period_from"], row["period_to"]) == (None, None)
    assert [shipment.pk for shipment in row["shipments"]] == [world.ships.n_oct12.pk]
    assert row["invoices"] == [] and row["waiting_lumps"] == []
    assert "¥" not in row["reason"]
    assert quiet.pk not in plan
    assert world.n.pk not in {invoice.customer_id for invoice in result}
    assert "締日が空" in result.saved_note
    assert world.n.name in result.saved_note
    assert result.saved_level == "warning"


# ---------------------------------------------------------------- (d)


@pytest.mark.django_db
def test_d_february_month_end_takes_closing_day_30(user, world):
    plan = _rows(FEB_28)
    result = _close(FEB_28, user)

    assert [invoice.customer_id for invoice in result] == [world.m.pk]
    invoice = result[0]
    assert (invoice.period_from, invoice.period_to) == (date(2026, 1, 31), FEB_28)
    assert (plan[world.m.pk]["period_from"], plan[world.m.pk]["period_to"]) == (date(2026, 1, 31), FEB_28)
    assert _shipment_pks(invoice) == {world.ships.m_jan31.pk, world.ships.m_feb10.pk}


@pytest.mark.django_db
def test_d_per_delivery_customer_is_in_no_run(user, world):
    for day in (OCT_20, date(2026, 10, 8), OCT_31, FEB_28):
        assert world.p.pk not in _rows(day)


@pytest.mark.django_db
def test_d_closing_day_30_is_not_in_the_31st_run(user, world):
    """30 日の月の締めの日は、締日 30 と 31 の得意先の両方が入る (月末に寄せる)。31 日の月の 31 日には締日 30 の得意先は入らない。"""
    s = _customer(user, "C010", 30)
    shipped(user, s, _line(user, s, world.item), "1", date(2026, 10, 30))
    shipped(user, s, _line(user, s, world.item), "1", date(2026, 9, 30))
    shipped(user, world.a, world.lines["a"], "1", date(2026, 9, 15))

    assert s.pk not in _rows(OCT_31)
    assert s.pk in _rows(date(2026, 10, 30))
    september = _rows(date(2026, 9, 30))
    assert {world.a.pk, s.pk} <= set(september)
    assert (september[s.pk]["period_from"], september[s.pk]["period_to"]) == (date(2026, 8, 31), date(2026, 9, 30))
    assert (september[world.a.pk]["period_from"], september[world.a.pk]["period_to"]) == (date(2026, 9, 1), date(2026, 9, 30))


# ---------------------------------------------------------------- (e)


@pytest.mark.django_db
def test_e_second_run_with_nothing_new_is_refused(user, world):
    _close(OCT_31, user)

    with pytest.raises(ValueError) as caught:
        _close(OCT_31, user)

    message = str(caught.value)
    assert message.startswith("発行できません。")
    assert "作る請求書がありません" in message
    assert "もう在る 2 社" in message
    assert "¥" not in message
    assert Invoice.objects.count() == 2


@pytest.mark.django_db
def test_e_second_run_makes_only_the_new_customer(user, world):
    first = _close(OCT_31, user)
    c = _customer(user, "C007", 31)
    c_ship = shipped(user, c, _line(user, c, world.item), "2", date(2026, 10, 20))

    result = _close(OCT_31, user)

    assert [invoice.customer_id for invoice in result] == [c.pk]
    assert _shipment_pks(result[0]) == {c_ship.pk}
    assert f"{world.a.name} (もう在る)" in result.saved_note
    assert f"{world.b.name} (もう在る)" in result.saved_note
    assert result.saved_message == f"2026-10-31 締めで 1 社の請求書を作りました ({result[0].invoice_no})。"
    assert Invoice.objects.count() == len(first) + 1


@pytest.mark.django_db
def test_e_existing_invoice_with_left_shipments_needs_reissue(user, world):
    first = _close(OCT_31, user)
    late = shipped(user, world.a, world.lines["a"], "1", date(2026, 10, 28))

    row = _rows(OCT_31)[world.a.pk]

    assert row["state"] == "exists"
    assert [invoice.pk for invoice in row["invoices"]] == [first[0].pk]
    assert [shipment.pk for shipment in row["shipments"]] == [late.pk]
    assert first[0].invoice_no in row["reason"]
    assert late.shipment_no in row["reason"]
    assert "作り直し" in row["reason"]
    assert _rows(OCT_31)[world.b.pk]["reason"] == f"請求書 {first[1].invoice_no} がもう在ります。"


# ---------------------------------------------------------------- (f)


@pytest.mark.django_db
def test_f_shipment_without_delivery_note_is_billed(user, world):
    bare = shipping_services.ship(world.lines["a"], Decimal("2"), date(2026, 10, 20), user)
    assert bare.delivery_note_id is None

    result = _close(OCT_31, user)

    assert bare.pk in _shipment_pks(result[0])


# ---------------------------------------------------------------- (g)


def _ended_lump(user, customer, item, shipped_at):
    """一式 300 を 100 出荷し (納品書も作る)、受注を取り消した明細 (出し切らずに終えた一式)。"""
    line = _line(user, customer, item, qty="300", price="50000", price_basis="lump")
    shipped(user, customer, line, "100", shipped_at)
    orders_services.cancel_order(line.order, user)
    line.refresh_from_db()
    return line


@pytest.mark.django_db
def test_g_customer_with_open_lump_is_not_made(user, world):
    lump = _ended_lump(user, world.a, world.item, date(2026, 10, 10))
    _ended_lump(user, world.b, world.item, date(2026, 11, 5))

    row = _rows(OCT_31)[world.a.pk]
    result = _close(OCT_31, user)

    assert row["state"] == "waiting"
    assert [line.pk for line in row["waiting_lumps"]] == [lump.pk]
    assert f"受注 {lump.order.order_no} の 1 行目" in row["reason"]
    assert "¥" not in row["reason"]
    assert [invoice.customer_id for invoice in result] == [world.b.pk]
    assert f"{world.a.name} (一式の金額)" in result.saved_note
    assert result.saved_level == "warning"


# ---------------------------------------------------------------- (h)


@pytest.mark.django_db
def test_h_no_bill_and_outside_paid_shipments_are_left_out(user, world):
    no_bill_line = _line(user, world.a, world.item)
    no_bill = shipped(user, world.a, no_bill_line, "1", date(2026, 10, 7))
    orders_services.mark_no_bill(no_bill_line, user)
    paid = shipped(user, world.a, world.lines["a"], "1", date(2026, 10, 8))
    services.record_outside_payment(paid, date(2026, 10, 8), Decimal("100"), user)

    h = _customer(user, "C008", 31)
    h_no_bill_line = _line(user, h, world.item)
    shipped(user, h, h_no_bill_line, "1", date(2026, 10, 9))
    orders_services.mark_no_bill(h_no_bill_line, user)
    h_paid = shipped(user, h, _line(user, h, world.item), "1", date(2026, 10, 9))
    services.record_outside_payment(h_paid, date(2026, 10, 9), Decimal("100"), user)

    plan = _rows(OCT_31)
    result = _close(OCT_31, user)

    assert h.pk not in plan
    lines = _shipment_pks(result[0])
    assert no_bill.pk not in lines and paid.pk not in lines
    assert lines == {world.ships.a_oct05.pk, world.ships.a_oct25.pk}
    assert h.pk not in {invoice.customer_id for invoice in result}


# ---------------------------------------------------------------- (i)


@pytest.mark.django_db
def test_i_shipments_outside_the_period_are_left_out(user, world):
    before = shipped(user, world.a, world.lines["a"], "1", date(2026, 9, 30))
    after = shipped(user, world.a, world.lines["a"], "1", date(2026, 11, 1))

    result = _close(OCT_31, user)

    lines = _shipment_pks(result[0])
    assert before.pk not in lines and after.pk not in lines
    assert lines == {world.ships.a_oct05.pk, world.ships.a_oct25.pk}


# ---------------------------------------------------------------- (j)


@pytest.mark.django_db
def test_j_future_closing_is_refused_without_reading(user, world, django_assert_num_queries):
    with django_assert_num_queries(0):
        with pytest.raises(ValueError) as caught:
            _close(date(2026, 11, 20), user)

    message = str(caught.value)
    assert message.startswith("発行できません。")
    assert "2026-11-20" in message
    assert "まだ来ていません" in message
    assert Invoice.objects.count() == 0


@pytest.mark.django_db
def test_j_closing_today_is_allowed(user, world):
    eleven = _customer(user, "C011", 11)
    shipped(user, eleven, _line(user, eleven, world.item), "1", date(2026, 11, 5))

    result = _close(TODAY, user)

    assert [invoice.customer_id for invoice in result] == [eleven.pk]


# ---------------------------------------------------------------- (k)


@pytest.mark.django_db
@pytest.mark.parametrize("error", ["rule", "value"])
def test_k_refusal_for_one_customer_rolls_back_all(user, world, monkeypatch, error):
    original = services._issue_invoice

    def refuse_second(customer, shipments, issued_on, user, **kwargs):
        if customer.pk == world.b.pk:
            if error == "rule":
                raise RuleViolation("R09", "発行できません。作り物の断りです。外してください。")
            raise ValueError("発行できません。作り物の断りです。外してください。")
        return original(customer, shipments, issued_on, user, **kwargs)

    monkeypatch.setattr(services, "_issue_invoice", refuse_second)
    expected = RuleViolation if error == "rule" else ValueError

    with pytest.raises(expected) as caught:
        _close(OCT_31, user)

    message = caught.value.message if error == "rule" else str(caught.value)
    assert message == f"発行できません。{world.b.name}: 作り物の断りです。外してください。"
    if error == "rule":
        assert caught.value.code == "R09"
    assert Invoice.objects.count() == 0


# ---------------------------------------------------------------- (l)


@pytest.mark.django_db
def test_l_plan_make_rows_are_what_is_created(user, world):
    plan = services.closing_plan(OCT_31)
    hidden = services.closing_plan(OCT_31, hide_money=True)

    assert [row["customer"].code for row in plan] == ["C001", "C002", "C004"]
    assert [row["state"] for row in plan] == ["make", "make", "no_closing_day"]
    assert {row["reason"] for row in plan if row["state"] == "make"} == {""}
    a_row = plan[0]
    assert a_row["amount"] == sum(
        (services.shipment_amount(world.a, shipment) for shipment in a_row["shipments"]), Decimal("0")
    )
    assert a_row["amount"] == Decimal("1000")
    assert [shipment.pk for shipment in a_row["shipments"]] == [world.ships.a_oct05.pk, world.ships.a_oct25.pk]
    assert {row["amount"] for row in hidden} == {None}

    result = _close(OCT_31, user)

    assert {row["customer"].pk for row in plan if row["state"] == "make"} == {invoice.customer_id for invoice in result}


# ---------------------------------------------------------------- (m)


def test_m_closing_date_moves_to_month_end():
    assert services.closing_date(2027, 2, 31) == date(2027, 2, 28)
    assert services.closing_date(2028, 2, 30) == date(2028, 2, 29)
    assert services.closing_date(2026, 10, 20) == date(2026, 10, 20)
    assert services.closing_date(2026, 10, 31) == date(2026, 10, 31)


@pytest.mark.parametrize("closing_day", [0, 32, "20", None, 20.0, True])
def test_m_closing_date_refuses_out_of_range(closing_day):
    with pytest.raises(ValueError) as caught:
        services.closing_date(2026, 10, closing_day)
    assert str(caught.value).startswith("発行できません。")


# ---------------------------------------------------------------- 知らせの字と訳


def test_saved_note_lists_five_names_then_the_rest():
    rows = [
        {"customer": SimpleNamespace(name=f"作り物の得意先 {index}"), "state": "exists", "shipments": [], "invoices": [object()]}
        for index in range(1, 8)
    ]

    result = services.ClosedInvoices([], OCT_31, rows)

    assert result.saved_note == (
        "作らなかった得意先 7 社: 作り物の得意先 1 (もう在る)・作り物の得意先 2 (もう在る)・作り物の得意先 3 (もう在る)・"
        "作り物の得意先 4 (もう在る)・作り物の得意先 5 (もう在る)・ほか 2 社。"
    )
    assert result.saved_level == ""


def test_saved_level_is_warning_only_for_rows_that_need_a_hand():
    def row(state, shipments=()):
        return {"customer": SimpleNamespace(name="作り物の得意先"), "state": state, "shipments": list(shipments), "invoices": []}

    assert services.ClosedInvoices([], OCT_31, [row("exists")]).saved_level == ""
    assert services.ClosedInvoices([], OCT_31, [row("exists", [object()])]).saved_level == "warning"
    assert services.ClosedInvoices([], OCT_31, [row("waiting")]).saved_level == "warning"
    assert services.ClosedInvoices([], OCT_31, [row("no_closing_day", [object()])]).saved_level == "warning"
    assert services.ClosedInvoices([], OCT_31, []).saved_note == ""


@pytest.mark.parametrize("func", ["create_closing_invoices", "_refuse_early_closing"])
def test_closing_functions_state_their_reason(func):
    doc = getattr(services, func).__doc__
    reason = [row.strip() for row in doc.splitlines() if row.strip().startswith("訳:")]
    assert reason and "出所 =" in reason[0] and "M12" in reason[0]
