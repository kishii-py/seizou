"""受注の詳細の右上の緑のボタンを、明細の進み具合で変える (本人の決め 11-12 19:0x。517 回目の本体の直し)。

訳: 緑のボタンは「その伝票で次にする操作 1 つ」(作者の決め 11-06 22:1x) だが、受注の詳細では、出荷の残がある間ずっと「出荷登録」だった。
受注を入れた直後に次にするのは指示書の発行で、入荷が無いと出荷を止める設定の会社では、押すと「入荷がありません」で止まった
(精査「事務の 1 人の 1 日」の 9)。
決め方 (上から順に、当たる明細が 1 つでもあり、その人が行える最初の操作):
1. 入荷を記録する: 会社の設定 receiving_required が off でなく、入荷の引当が 1 つも無い明細 (指示書の発行と出荷の検査が止めるか知らせる形と同じ見方)。
2. 指示書を発行する: 指示書の数量の合計が残より少なく、完成の数も在庫も残に届かない明細 (生産管理ボードの判断の「指示書が要る」と同じ見方)。
3. 出荷登録: 残のある明細 (今までと同じ)。
明細の行の ⋮ のメニューは変えない。作り物の組み方は tests/test_detail_columns.py と tests/test_detail_header.py の fixture を借りる。
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.orders import services as orders_services
from seizou.receiving import services as receiving_services
from seizou.receiving.models import SOURCE_CONSIGNED
from seizou.stock import services as stock_services
from seizou.workorders import services as workorders_services
from tests import test_detail_columns
from tests.test_core_access import account
from tests.test_detail_header import _actions, _get, _issue_all, cut  # noqa: F401 (fixture)

customer = test_detail_columns.customer
item = test_detail_columns.item
order = test_detail_columns.order

SHIP = "出荷登録"
ISSUE = "指示書を発行する"
RECEIVE = "入荷を記録する"


def _lines(order):  # noqa: F811
    return list(order.lines.order_by("line_no"))


def _link(url_name, line):
    """明細 1 行の小窓の入口 (明細の ⋮ の項目と同じ href と data-modal-form)。"""
    url = f"{reverse(url_name)}?order_line={line.pk}"
    return f'href="{url}" data-modal-form="{url}"'


def _receive(user, line):
    data = {"item": line.item, "partner": line.order.customer, "source": SOURCE_CONSIGNED, "qty": line.qty, "received_on": timezone.localdate()}
    return receiving_services.create_receiving(data, user, order_line=line)


def _head(client, order):  # noqa: F811
    return _actions(_get(client, "orders:order_detail", order.pk)[1])


# --- 指示書が要る ---


@pytest.mark.django_db
def test_new_order_offers_issue_for_lines_without_workorder(logged_in, order):  # noqa: F811
    """受注を入れた直後 (指示書が無い): 緑は「指示書を発行する」(2 行なので行を選ぶメニュー)。「編集」は白。"""
    actions = _head(logged_in, order)
    first, second = _lines(order)

    assert '<summary class="btn btn-primary">' in actions and ISSUE in actions
    assert SHIP not in actions and RECEIVE not in actions
    for line in (first, second):
        assert actions.count(_link("workorders:workorder_create", line)) == 1, line.line_no
    assert "指示書を発行する行を選ぶ" in actions
    assert actions.count("btn-primary") == 1
    edit = f'<a class="btn btn-outline border-base-300" href="{reverse("orders:order_update", args=[order.pk])}'
    assert actions.index(edit) < actions.index("data-detail-more") < actions.index('<summary class="btn btn-primary">')


@pytest.mark.django_db
def test_one_line_without_workorder_opens_issue_directly(logged_in, user, order, cut):  # noqa: F811
    """1 行目は指示書が数量まで出ていて、2 行目だけ指示書が無い: 緑の「指示書を発行する」が 2 行目の小窓を直に開く。"""
    first, second = _lines(order)
    workorders_services.issue_workorder(first, first.qty, [{"process": cut, "planned_qty": first.qty}], user)

    actions = _head(logged_in, order)

    assert f'<a class="btn btn-primary" {_link("workorders:workorder_create", second)}>' in actions and ISSUE in actions
    assert f"?order_line={first.pk}" not in actions
    assert '<summary class="btn btn-primary">' not in actions


# --- 製造中と、在庫で出せる時は今までどおり「出荷登録」 ---


@pytest.mark.django_db
def test_lines_in_production_keep_ship(logged_in, user, order, cut):  # noqa: F811
    _issue_all(order, user, cut)

    actions = _head(logged_in, order)

    assert '<summary class="btn btn-primary">' in actions and SHIP in actions and ISSUE not in actions
    for line in _lines(order):
        assert actions.count(_link("shipping:shipment_create", line)) == 1
    assert "出荷する行を選ぶ" in actions


@pytest.mark.django_db
def test_lines_covered_by_stock_offer_ship(logged_in, user, order):  # noqa: F811
    """指示書が無くても、在庫が残以上なら「出荷できる」なので、緑は「出荷登録」(生産管理ボードの判断と同じ見方)。"""
    for part in {line.item for line in _lines(order)}:  # 2 行が同じ部品のこともあるので、部品ごとに 1 回
        stock_services.adjust(part, Decimal("1000"), timezone.localdate(), user)

    actions = _head(logged_in, order)

    assert SHIP in actions and ISSUE not in actions


# --- 入荷が要る会社 ---


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["stop", "warn"])
def test_receiving_first_when_required_and_nothing_received(settings, logged_in, user, order, mode):  # noqa: F811
    """入荷が無いと指示書と出荷を止める (か知らせる) 会社: 入荷の引当が 1 つも無い明細があれば、緑は「入荷を記録する」。"""
    settings.SEIZOU = {"receiving_required": mode}
    first, second = _lines(order)

    actions = _head(logged_in, order)
    assert '<summary class="btn btn-primary">' in actions and RECEIVE in actions
    assert ISSUE not in actions and SHIP not in actions
    assert actions.count(_link("receiving:receiving_create", first)) == 1 and actions.count(_link("receiving:receiving_create", second)) == 1
    assert "入荷を記録する行を選ぶ" in actions

    # 1 行目に入荷を記録すると、入荷が要るのは 2 行目だけ (小窓を直に開く)
    _receive(user, first)
    actions = _head(logged_in, order)
    assert f'<a class="btn btn-primary" {_link("receiving:receiving_create", second)}>' in actions and RECEIVE in actions

    # 2 行目にも入荷を記録すると、次は指示書
    _receive(user, second)
    actions = _head(logged_in, order)
    assert ISSUE in actions and RECEIVE not in actions


@pytest.mark.django_db
def test_receiving_is_not_offered_when_setting_is_off(settings, logged_in, order):  # noqa: F811
    settings.SEIZOU = {"receiving_required": "off"}

    assert RECEIVE not in _head(logged_in, order)


# --- 行える人にだけ ---


@pytest.mark.django_db
def test_falls_back_to_the_first_action_the_person_can_do(settings, client, order):  # noqa: F811
    """指示書を発行できない人 (出荷だけの権限) には、次に行える操作の「出荷登録」を出す (今までと同じ)。見るだけの人には緑を出さない。"""
    settings.SEIZOU = {"access_control": {"department": "off", "write": "on", "money": "off"}}

    client.force_login(account("shukka", permissions=["shipping"]))
    actions = _head(client, order)
    assert SHIP in actions and ISSUE not in actions

    client.force_login(account("seizo", permissions=["workorders"]))
    actions = _head(client, order)
    assert ISSUE in actions and SHIP not in actions

    client.force_login(account("miru"))
    actions = _head(client, order)
    assert SHIP not in actions and ISSUE not in actions and "btn-primary" not in actions


# --- 問い合わせの数 ---


def _detail_queries(client, order) -> int:  # noqa: F811
    url = reverse("orders:order_detail", args=[order.pk])
    assert client.get(url).status_code == 200  # 最初の 1 回の控え (session など) を外す
    with CaptureQueriesContext(connection) as queries:
        assert client.get(url).status_code == 200
    return len(queries.captured_queries)


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["off", "stop"])
def test_next_action_does_not_add_queries_per_line(settings, logged_in, user, customer, item, mode):  # noqa: F811
    """緑のボタンを決めるための読み (引当の合計・在庫数・指示書の数量の合計) は、指示書の無い受注でも、明細の数で問い合わせを増やさない。

    指示書と出荷のある受注は tests/test_orders_detail_queries.py が見る (残のある明細で、同じ読みを通る)。
    """
    settings.SEIZOU = {"receiving_required": mode}

    def fresh_order(count):
        lines = [{"item": item, "qty": Decimal("3"), "unit_price": Decimal("100")} for _ in range(count)]
        return orders_services.create_order(customer, lines, user, order_date=date(2026, 11, 2))

    small = _detail_queries(logged_in, fresh_order(1))
    large = _detail_queries(logged_in, fresh_order(5))

    assert small == large, (small, large)


# --- 明細ごとの値 ---


@pytest.mark.django_db
def test_board_row_next_action(settings, user, order, cut):  # noqa: F811
    first, second = _lines(order)
    assert [line.board.next_action for line in (first, second)] == ["issue", "issue"]

    workorders_services.issue_workorder(first, first.qty, [{"process": cut, "planned_qty": first.qty}], user)
    first, second = _lines(order)
    assert [line.board.next_action for line in (first, second)] == ["ship", "issue"]

    settings.SEIZOU = {"receiving_required": "stop"}
    first, second = _lines(order)
    assert [line.board.next_action for line in (first, second)] == ["receive", "receive"]

    orders_services.cancel_order(order, user)
    order.refresh_from_db()  # 明細が持つ受注の写しを、取消の後の状態にする
    first, second = _lines(order)
    assert [line.board.next_action for line in (first, second)] == ["", ""]


def _judged(line):
    """明細を引き直した (判断の印の code, 次の操作) の組。board は cached_property なので、変えた後は引き直す。"""
    row = type(line).objects.get(pk=line.pk).board
    return row.judgement_code, row.next_action


@pytest.mark.django_db
def test_next_action_follows_the_board_judgement(user, customer, item, cut):  # noqa: F811
    """次の操作は、生産管理ボードの判断の印と同じ見方 (本人の決め 11-12 19:0x)。判断の式を変えたら、この試験が落ちて次の操作の式に気づく。

    指示書が要る → issue / 製造中 → ship / 出荷できる → ship / 納期遅れは、指示書の数量の合計が残より少なければ issue、ほかは ship /
    完了の印・出荷済み・取消 → 空。入荷が要る設定は既定の off (入荷は見ない)。
    """
    today = timezone.localdate()

    def line_due(due):
        data = [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": due}]
        return orders_services.create_order(customer, data, user, order_date=today - timedelta(days=40)).lines.get()

    def issue(line):
        workorders_services.issue_workorder(line, line.qty, [{"process": cut, "planned_qty": line.qty}], user)

    ahead, behind = today + timedelta(days=30), today - timedelta(days=1)

    fresh = line_due(ahead)
    assert _judged(fresh) == ("need_workorder", "issue")
    issue(fresh)
    assert _judged(fresh) == ("making", "ship")

    late = line_due(behind)
    assert _judged(late) == ("late", "issue")
    issue(late)
    assert _judged(late) == ("late", "ship")

    closed = line_due(ahead)
    orders_services.mark_completed(closed, user)
    assert _judged(closed) == ("completed", "")

    cancelled = line_due(ahead)
    orders_services.cancel_order(cancelled.order, user)
    assert _judged(cancelled) == ("cancelled", "")

    # 在庫が残以上になると、指示書の無い明細も「出荷できる」(同じ部品の明細の全部に当たるので、最後に見る)
    stocked = line_due(ahead)
    stock_services.adjust(item, Decimal("1000"), today, user)
    assert _judged(stocked) == ("ship", "ship")
