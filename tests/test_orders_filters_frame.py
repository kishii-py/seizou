"""注文残の盤と受注の一覧の絞りを、絞り込みの枠の 1 か所に移した形 (N126。本人の決め 11-04 17:3x)。名前は作り物だけ。

盤: 「出荷の残りがある物だけ」(flag・既定で入)・「納期」(期間)・「納期が過ぎた物だけ」(flag・既定で切)・「指示書の無い明細だけ」(flag・既定で切。
数える関数 core.dashboard.without_workorder と同じ条件。nav-01)。
受注の一覧: 「出荷の残りがある受注だけ」(flag・既定で入)。状態を選ぶと既定を外す (N118)。
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
    assert 'name="f_open" value="1" class="checkbox checkbox-sm" checked data-list-filter>出荷の残りがある受注だけ</label>' in body
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
    """状態を選ぶと「出荷の残りがある受注だけ」が切に見えて押せず、表は選んだ状態の全部 (N143 = N126 の知らせの 3 の食い違いを無くす)。"""
    open_order = _order(customer, shaft, user)
    done = _order(customer, shaft, user, qty="2")
    shipping_services.ship(done.lines.get(), Decimal("2"), timezone.localdate(), user)
    cancelled = _order(customer, shaft, user, qty="1")
    services.cancel_order(cancelled, user)
    url = reverse("orders:order_list")
    box_on = 'name="f_open" value="1" class="checkbox checkbox-sm" checked data-list-filter>出荷の残りがある受注だけ</label>'
    box_off = 'name="f_open" value="1" class="checkbox checkbox-sm" disabled data-list-filter>出荷の残りがある受注だけ</label>'

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


FRAME = '<details class="group rounded-lg bg-base-200 p-4" open'
WIDTH = 'window.matchMedia("(min-width: 640px)")'


def _frame(body: str) -> str:
    """絞り込みの枠 (details の開きから閉じまで) の HTML。"""
    start = body.index(FRAME)
    return body[start : body.index("</details>", start)]


def _script_after_frame(body: str) -> str:
    """枠のすぐ後の script の中身 (枠の閉じと script の間は空白だけ)。"""
    after = body[body.index("</details>", body.index(FRAME)) + len("</details>") :]
    head, rest = after.split("<script>", 1)
    assert head.strip() == "", head
    return rest.split("</script>", 1)[0]


@pytest.mark.django_db
def test_order_list_frame_is_details_and_closes_on_phone(user, logged_in, customer, shaft):  # noqa: F811
    """絞り込みの枠は details。HTML は開いた形で描き、幅が 640px より狭くキーワードが無いときだけ、すぐ後の script が閉じる (C39-2 の a。a-flagged-14)。"""
    order = _order(customer, shaft, user)
    url = reverse("orders:order_list")

    body = logged_in.get(url).content.decode()
    assert body.count(FRAME) == 1
    frame = _frame(body)
    # summary の 1 行に「絞り込み」と「クリア」と山形の印 (閉じると右向き、開くと下向き = 左のメニューと同じ字)
    summary = frame[frame.index("<summary") : frame.index("</summary>")]
    assert ">絞り込み</span>" in summary and "クリア</a>" in summary
    assert "-rotate-90" in summary and "group-open:rotate-0" in summary
    # role="search" とキーワードの欄と絞りの欄は details の中、summary の後
    assert frame.index("</summary>") < frame.index('<div role="search">') < frame.index('id="list-q"') < frame.index('id="list-f-status"')
    # 素の GET は開いた形で描き、キーワードの印は無い
    assert f"{FRAME}>" in body and f"{FRAME} data-list-keyword>" not in body

    script = _script_after_frame(body)
    assert WIDTH in script and "document.currentScript.previousElementSibling" in script
    assert 'hasAttribute("data-list-keyword")' in script and "frame.open = false" in script
    # 出来事を待つ登録はしない (枠と一緒に入り直して走る)。左のメニューの戻るの字も書かない
    for absent in ("addEventListener", "htmx:", "seizouSidebarReopen", 'role="search"', "list-filter-clear", "list-per"):
        assert absent not in script, absent

    # キーワードがあるときは、狭い幅でも開いて出す印が付く (空のキーワードには付かない)
    assert f"{FRAME} data-list-keyword>" in logged_in.get(url, {"q": order.order_no}).content.decode()
    assert f"{FRAME}>" in logged_in.get(url, {"q": ""}).content.decode()


@pytest.mark.django_db
def test_order_list_chips_stay_outside_closed_frame(user, logged_in, customer, shaft):  # noqa: F811
    """得意先を選ぶと、札は枠の外 (#list-filter-clear) に出る (閉じた枠でも絞りが見える)。札の × が取り直す #list-card に、枠と script が入る。"""
    _order(customer, shaft, user)
    url = reverse("orders:order_list")
    params = {"f_customer": str(customer.pk)}

    body = logged_in.get(url, params).content.decode()
    frame_end = body.index("</details>", body.index(FRAME))
    clear = body.index('<div id="list-filter-clear">')
    assert frame_end < clear < body.index('id="list-per"')
    section = body[clear : body.index('id="list-per"')]
    assert f'得意先: <span class="font-medium">{customer}</span>' in section

    # 札の × の要求 (HX-Target が list-card): 画面の全体が返り、#list-card の中に枠と script がある
    card = logged_in.get(url, params, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="list-card").content.decode()
    assert card.index('id="list-card"') < card.index(FRAME) < card.index(WIDTH) < card.index('id="rows"')

    # 表だけの取り直し (#rows) では枠を描き直さない (人が開いた枠はそのまま)
    rows = logged_in.get(url, params, HTTP_HX_REQUEST="true").content.decode()
    assert FRAME not in rows and WIDTH not in rows
