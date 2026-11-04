"""注文残の盤と受注の一覧の絞りを、絞り込みの枠の 1 か所に移した形 (N126。本人の決め 11-04 17:3x)。名前は作り物だけ。

盤: 「出荷の残りがある物だけ」(flag・既定で入)・「納期」(期間)・「納期が過ぎた物だけ」(flag・既定で切)。
受注の一覧: 「出荷の残っている受注だけ」(flag・既定で入)。状態を選ぶと既定を外す (N118)。
  状態を選んだ間は、印を切に描いて押せなくする (土台の disabled_when。htmx の応答でも描き直す。N143)。
古い GET (all・month・due_from・due_to・overdue) は読まない (別名を足さない)。
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from seizou.core import views as core_views
from seizou.orders import services
from seizou.shipping import services as shipping_services
from tests.test_orders_views import customer, logged_in, shaft, user  # noqa: F401 - fixture を借りる


def _order(partner, item, user, *, due=None, qty="5", order_date=None):  # noqa: F811
    """明細 1 行の受注 (納期は due。受注日は既定で今日)。"""
    lines = [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100"), "due_date": due}]
    return services.create_order(partner, lines, user, order_date=order_date or timezone.localdate())


def _listed(response):
    return [row["obj"] for row in response.context["rows"]]


@pytest.mark.django_db
def test_board_default_is_open_lines_and_flags_narrow(user, logged_in, customer, shaft):  # noqa: F811
    today = timezone.localdate()
    late = _order(customer, shaft, user, due=today - timedelta(days=1)).lines.get()
    soon = _order(customer, shaft, user, due=today + timedelta(days=3)).lines.get()
    done = _order(customer, shaft, user, due=today - timedelta(days=2), qty="2").lines.get()
    shipping_services.ship(done, Decimal("2"), today, user)
    cancelled = _order(customer, shaft, user, due=today + timedelta(days=5)).lines.get()
    services.cancel_order(cancelled.order, user)
    url = reverse("orders:orderline_list")

    assert set(_listed(logged_in.get(url))) == {late, soon}
    assert set(_listed(logged_in.get(url, {"f_open": "0"}))) == {late, soon, done, cancelled}
    # チェックの欄は隠しの 0 の後に 1 を送る (入のときは両方が届き、後の 1 を読む)
    assert set(_listed(logged_in.get(url + "?f_open=0&f_open=1"))) == {late, soon}
    assert _listed(logged_in.get(url, {"f_overdue": "1"})) == [late]
    assert set(_listed(logged_in.get(url, {"f_open": "0", "f_overdue": "1"}))) == {late, done}
    period = {"f_due_from": today.isoformat(), "f_due_to": (today + timedelta(days=4)).isoformat()}
    assert _listed(logged_in.get(url, period)) == [soon]
    assert _listed(logged_in.get(url, {"f_overdue": "1"}, HTTP_HX_REQUEST="true")) == [late]
    body = logged_in.get(url, {"f_open": "0", "format": "csv"}).content.decode("utf-8-sig")
    assert done.order.order_no in body and cancelled.order.order_no in body
    # 古い GET は読まない (既定のまま)
    for old in ({"all": "1"}, {"overdue": "1"}, {"due_from": (today + timedelta(days=1)).isoformat()}):
        assert set(_listed(logged_in.get(url, old))) == {late, soon}, old


@pytest.mark.django_db
def test_order_list_open_flag_gives_way_to_status(user, logged_in, customer, shaft):  # noqa: F811
    open_order = _order(customer, shaft, user)
    done = _order(customer, shaft, user, qty="2")
    shipping_services.ship(done.lines.get(), Decimal("2"), timezone.localdate(), user)
    cancelled = _order(customer, shaft, user, qty="1")
    services.cancel_order(cancelled, user)
    url = reverse("orders:order_list")

    response = logged_in.get(url)
    assert _listed(response) == [open_order]
    body = response.content.decode()
    assert 'name="f_open" value="1" class="checkbox checkbox-sm" checked data-list-filter>出荷の残っている受注だけ</label>' in body
    assert 'id="orders-all"' not in body and 'aria-current="true">すべて</span>' not in body
    assert set(_listed(logged_in.get(url, {"f_open": "0"}))) == {open_order, done, cancelled}
    # 状態を選ぶと、印が入のままでも既定を外す (N118)。印を外して状態を選んでも同じ。
    assert _listed(logged_in.get(url, {"f_status": "shipped"})) == [done]
    assert _listed(logged_in.get(url, {"f_open": "1", "f_status": "cancelled"})) == [cancelled]
    assert _listed(logged_in.get(url, {"f_open": "0", "f_status": "open"})) == [open_order]
    # 古い GET は読まない (既定のまま)
    for old in ({"all": "1"}, {"month": timezone.localdate().strftime("%Y-%m")}):
        assert _listed(logged_in.get(url, old)) == [open_order], old


@pytest.mark.django_db
def test_dashboard_month_card_links_to_frame_filters(user, logged_in, customer, shaft):  # noqa: F811
    today = timezone.localdate()
    first = today.replace(day=1)
    last = (first + timedelta(days=31)).replace(day=1) - timedelta(days=1)
    kept = _order(customer, shaft, user)
    cancelled = _order(customer, shaft, user)
    services.cancel_order(cancelled, user)
    _order(customer, shaft, user, order_date=first - timedelta(days=1))

    (stat,) = [s for s in core_views.dashboard_stats() if s["label"] == "今月の受注"]
    assert stat["url"] == reverse("orders:order_list") + f"?f_open=0&f_ordered_from={first}&f_ordered_to={last}"
    assert stat["value"] == 1  # カードの数は取消を除く (本体への知らせの 2 の (a))
    listing = logged_in.get(stat["url"])
    assert set(_listed(listing)) == {kept, cancelled}
    body = listing.content.decode()
    assert f'name="f_ordered_from" value="{first}"' in body and f'name="f_ordered_to" value="{last}"' in body
    assert 'name="f_open" value="1" class="checkbox checkbox-sm" data-list-filter>' in body


@pytest.mark.django_db
def test_order_list_open_flag_is_drawn_off_while_status_chosen(user, logged_in, customer, shaft):  # noqa: F811
    """状態を選ぶと「出荷の残っている受注だけ」が切に見えて押せず、表は選んだ状態の全部 (N143 = N126 の知らせの 3 の食い違いを無くす)。"""
    open_order = _order(customer, shaft, user)
    done = _order(customer, shaft, user, qty="2")
    shipping_services.ship(done.lines.get(), Decimal("2"), timezone.localdate(), user)
    cancelled = _order(customer, shaft, user, qty="1")
    services.cancel_order(cancelled, user)
    url = reverse("orders:order_list")
    box_on = 'name="f_open" value="1" class="checkbox checkbox-sm" checked data-list-filter>出荷の残っている受注だけ</label>'
    box_off = 'name="f_open" value="1" class="checkbox checkbox-sm" disabled data-list-filter>出荷の残っている受注だけ</label>'

    # 状態を選ばなければ既定どおり (入で掛かる)
    body = logged_in.get(url).content.decode()
    assert box_on in body and box_off not in body

    # 状態を選ぶと、印は切に描かれて押せず、表は選んだ状態の全部。隠しの欄は印の値を持ち越す
    response = logged_in.get(url, {"f_status": "shipped"})
    assert _listed(response) == [done]
    body = response.content.decode()
    assert box_off in body and box_on not in body
    assert '<input type="hidden" name="f_open" value="1">' in body
    assert 'value="0">' not in body.split('id="list-f-open-box"', 1)[1].split("</div>", 1)[0]
    flag = [item for item in response.context["filters"] if item["name"] == "open"][0]
    assert (flag["checked"], flag["disabled"], flag["active"]) == (False, True, False)

    # htmx で状態を変えたときも、印の箱を oob で描き直す (チェックと表が合う)
    oob = '<div id="list-f-open-box" hx-swap-oob="true" hx-get="/orders/" hx-include="closest form" hx-target="#rows" hx-push-url="true" hx-trigger="change">'
    response = logged_in.get(url, {"f_open": ["0", "1"], "f_status": "cancelled"}, HTTP_HX_REQUEST="true")
    assert _listed(response) == [cancelled]
    partial = response.content.decode()
    assert oob in partial and box_off in partial.split(oob, 1)[1]
    # 状態を外すと (隠しの欄の 1 が届く)、印は入に戻って掛かる
    response = logged_in.get(url, {"f_open": "1", "f_status": ""}, HTTP_HX_REQUEST="true")
    assert _listed(response) == [open_order]
    partial = response.content.decode()
    assert oob in partial and box_on in partial.split(oob, 1)[1]

    # 盤の印 (状態の絞りが無い) は従う印でないので、htmx の応答で描き直さない
    partial = logged_in.get(reverse("orders:orderline_list"), {"f_open": "0"}, HTTP_HX_REQUEST="true").content.decode()
    assert "list-f-open-box" not in partial
