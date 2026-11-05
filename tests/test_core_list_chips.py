"""一覧の効いている絞りの札と、0 件の枠の 2 種類 (BaseListView.filter_chips と core/_filter_clear.html・core/_rows.html。N161)。

本人の決め 11-05 16:2x: 絞り込みの枠の値のうち狭める物 (flag は入のときだけ) を、枠の下に「見出し: 値 ×」の札で並べる。
キーワード (q)・件数・並び替えは札にしない。札が 1 つ以上あるときだけ、右端に「絞りを外す」を出す。
札の × (その絞りだけを外す)・「絞りを外す」(枠の絞りを全部外す)・0 件の枠の「すべて外す」は、一覧の枠 (#list-card) を htmx で取り直す
(土台は HX-Target が list-card の要求に画面の全体を返す)。
0 件の枠は、検索も札も無ければ「まだ 1 件もありません。」、あれば「<効いている条件>に合う物はありません。」と「すべて外す」。
休日の一覧を年で絞った 0 件は「YYYY 年の休日はありません。」。
作り物と画面は tests/test_core_list_filters.py から借りる。名前は作り物だけ。
"""

import re
from datetime import date

from django.http import QueryDict
from django.test import RequestFactory
from django.urls import reverse
from django.utils.html import escape

from seizou.core.views import BaseListView
from seizou.masters import services as masters_services
from seizou.masters.models import Holiday
from seizou.orders.models import Order
from tests.test_core_list_filters import _FlagOrderList, _FollowOrderList, _ItemList, _OrderList, call, listed, orders, user  # noqa: F401

STATUS = dict(Order._meta.get_field("status").flatchoices)
CHIP = 'class="badge badge-soft badge-primary gap-1'
LIST_CARD = 'hx-target="#list-card" hx-select="#list-card" hx-swap="outerHTML ignoreTitle:true" hx-push-url="true"'
EMPTY_OOB = '<div id="list-filter-clear" hx-swap-oob="true">'


def chips(response) -> list:
    return [(chip["name"], chip["heading"], chip["text"]) for chip in response.context_data["filter_chips"]]


def htmx(view, user, headers=None, **params):  # noqa: F811
    request = RequestFactory().get("/", params, HTTP_HX_REQUEST="true", **(headers or {}))
    request.user = user
    return view.as_view()(request)


def test_chips_name_each_active_filter_with_its_value(user, orders):  # noqa: F811
    south = orders["south"]
    response = call(
        _OrderList,
        user,
        f_status="shipped",
        f_ordered_from="2026-11-02",
        f_ordered_to="2026-11-04",
        f_customer=str(south.pk),
        q="作り物",
        per="20",
    )
    # 宣言の順。キーワードは札にしない
    assert chips(response) == [
        ("status", "状態", STATUS["shipped"]),
        ("ordered", "受注日", "2026-11-02〜11-04"),
        ("customer", "得意先", str(south)),
    ]
    body = response.render().content.decode()
    assert body.count('id="list-filter-clear"') == 1
    # 札は絞り込みの枠の下、件数の選びの行の上
    assert body.index('role="search"') < body.index('<div id="list-filter-clear">') < body.index('id="list-per"')
    section = body[body.index('<div id="list-filter-clear">') : body.index('id="list-per"')]
    assert section.count(CHIP) == 3
    assert f'状態: <span class="font-medium">{STATUS["shipped"]}</span>' in section
    assert section.count("絞りを外す") == 1
    assert section.count('hx-target="#list-card"') == 4  # 札の × が 3 つと「絞りを外す」

    # 期間の片方だけ・年をまたぐ期間・数の範囲
    assert chips(call(_OrderList, user, f_ordered_from="2026-11-04")) == [("ordered", "受注日", "2026-11-04〜")]
    assert chips(call(_OrderList, user, f_ordered_to="2026-11-02")) == [("ordered", "受注日", "〜2026-11-02")]
    crossing = call(_OrderList, user, f_ordered_from="2025-12-30", f_ordered_to="2026-11-02")
    assert chips(crossing) == [("ordered", "受注日", "2025-12-30〜2026-11-02")]
    assert chips(call(_ItemList, user, f_price_from="100", f_price_to="1,000")) == [("price", "単価", "100〜1000")]


def test_flag_chips_only_when_they_narrow(user, orders):  # noqa: F811
    # 何も無い: 札も「絞りを外す」も無く、空の箱だけ
    response = call(_FlagOrderList, user)
    assert response.context_data["filter_chips"] == []
    body = response.render().content.decode()
    assert '<div id="list-filter-clear"></div>' in body
    assert "絞りを外す" not in body

    # 入にした印は札になる (見出しは書かず、印の見出しを値の字にする)
    response = call(_FlagOrderList, user, f_early=["0", "1"])
    assert chips(response) == [("early", "", "11-03 より前の受注だけ")]
    body = response.render().content.decode()
    assert "11-03 より前の受注だけ: " not in body
    assert "絞りを外す" in body

    # 既定で入の印を切っただけは札にしない
    assert call(_FlagOrderList, user, f_open="0").context_data["filter_chips"] == []
    # ほかの絞りに従って切に描いた印も札にしない
    followed = call(_FollowOrderList, user, f_status="shipped")
    assert [chip["name"] for chip in followed.context_data["filter_chips"]] == ["status"]


def test_chip_x_drops_only_its_filter_and_keeps_the_rest(user, orders):  # noqa: F811
    made, south = orders["orders"], orders["south"]
    params = {"f_status": "shipped", "f_customer": str(south.pk), "q": "作り物", "sort": "-order_date", "per": "20", "page": "1"}
    response = call(_OrderList, user, **params)
    (status,) = [chip for chip in response.context_data["filter_chips"] if chip["name"] == "status"]
    query = QueryDict(status["query"])
    for key in ("f_status", "page", "format"):
        assert key not in query, key
    for key in ("f_customer", "q", "sort", "per"):
        assert key in query, key
    # その GET で取り直すと、得意先の絞りとキーワードと並び替えは効いたまま
    assert listed(call(_OrderList, user, **query.dict())) == [made[3].pk, made[1].pk]

    body = response.render().content.decode()
    assert f'href="?{escape(status["query"])}"' in body
    assert f'hx-get="/?{escape(status["query"])}"' in body
    chip = body[body.index(f'hx-get="/?{escape(status["query"])}"') :]
    chip = chip[: chip.index("</a>")]
    for attribute in ('hx-select="#list-card"', 'hx-swap="outerHTML ignoreTitle:true"', 'hx-push-url="true"', 'aria-label="状態の条件を外す"'):
        assert attribute in chip, attribute
    # 札の名前に「絞りを外す」の字を入れない (右端の link と取り違えない)
    assert re.findall(r'aria-label="[^"]*絞りを外す[^"]*"', body) == []


def test_clear_link_drops_every_filter_but_keeps_keyword(user, orders):  # noqa: F811
    south = orders["south"]
    params = {"f_status": "shipped", "f_customer": str(south.pk), "q": "作り物", "sort": "-order_date", "per": "20", "page": "1"}
    response = call(_OrderList, user, **params)
    context = response.context_data
    body = response.render().content.decode()
    clear = re.search(r'<a class="ml-auto[^"]*" href="\?([^"]*)"', body)
    assert clear and clear.group(1) == escape(context["query_without_filters"])
    query = QueryDict(context["query_without_filters"])
    assert not [key for key in query if key.startswith("f_")]
    for key in ("q", "sort", "per"):
        assert key in query, key
    assert f'hx-get="/?{escape(context["query_without_filters"])}" {LIST_CARD}>' in body

    # 行の部分の応答も札の箱を oob で描き直す (札は状態と得意先の 2 つ)
    partial = htmx(_OrderList, user, **params).content.decode()
    assert "<html" not in partial
    assert EMPTY_OOB in partial
    assert partial.count(CHIP) == 2


def test_list_card_request_gets_whole_page(user, orders):  # noqa: F811
    assert BaseListView.LIST_TARGET == "list-card"

    # 一覧の枠を名指しした要求 (札の × など) は画面の全体 (htmx が枠だけを選ぶ)。札の箱は oob にしない
    response = htmx(_OrderList, user, {"HTTP_HX_TARGET": "list-card"}, f_status="shipped")
    body = response.render().content.decode()
    for needed in ("<html", '<div id="list-card"', '<select id="list-f-status"', '<div id="list-filter-clear">'):
        assert needed in body, needed
    assert 'id="list-filter-clear" hx-swap-oob' not in body
    assert "HX-Request" in response["Vary"] and "HX-Target" in response["Vary"]

    # 行の部分を名指しした要求は今のまま行の部分だけ
    rows = htmx(_OrderList, user, {"HTTP_HX_TARGET": "rows"}, f_status="shipped")
    assert "<html" not in rows.content.decode()
    assert "HX-Request" in rows["Vary"] and "HX-Target" in rows["Vary"]

    # ブラウザの戻る (履歴の復元) は今のまま画面の全体
    restore = htmx(_OrderList, user, {"HTTP_HX_HISTORY_RESTORE_REQUEST": "true"}, f_status="shipped")
    assert "<html" in restore.render().content.decode()


def test_empty_list_without_filters_says_none_yet(user):  # noqa: F811
    # 検索も札も無い 0 件 (既定で入の印を切っただけを含む) は「まだ 1 件もありません。」
    for response in (call(_OrderList, user), call(_FlagOrderList, user, f_open="0")):
        body = response.render().content.decode()
        assert "まだ 1 件もありません。" in body
        assert "に合う物はありません" not in body
        assert "すべて外す" not in body


def test_filtered_empty_names_the_filters_and_clears_all(user, orders):  # noqa: F811
    body = call(_OrderList, user, f_status="cancelled").render().content.decode()
    assert f"状態「{STATUS['cancelled']}」に合う物はありません。" in body
    assert "まだ 1 件もありません。" not in body
    assert f'href="/" hx-get="/" {LIST_CARD}>' in body
    assert "すべて外す</a>" in body

    keyword = call(_OrderList, user, q="合わない字").render().content.decode()
    assert "「合わない字」に合う物はありません。" in keyword
    both = call(_OrderList, user, q="合わない字", f_status="cancelled").render().content.decode()
    assert f"「合わない字」、状態「{STATUS['cancelled']}」に合う物はありません。" in both
    period = call(_OrderList, user, f_ordered_from="2026-12-01", f_ordered_to="2026-12-31").render().content.decode()
    assert "受注日「2026-12-01〜12-31」に合う物はありません。" in period

    # 行の部分の応答も同じ文と、札の箱の oob
    partial = htmx(_OrderList, user, f_status="cancelled").content.decode()
    assert f"状態「{STATUS['cancelled']}」に合う物はありません。" in partial
    assert EMPTY_OOB in partial


def test_holiday_year_without_rows_names_the_year(client_logged_in, user):  # noqa: F811
    masters_services.create_holiday({"date": date(2026, 8, 13), "name": "作り物の夏の休み", "kind": Holiday.KIND_COMPANY}, user)
    url = reverse("masters:holiday_list")

    body = client_logged_in.get(url, {"year": "2025"}).content.decode()
    assert "2025 年の休日はありません。" in body
    # 「すべて外す」は年を外した一覧へ画面ごと移る link (hx-get を持たない。見出しの帯の年のボタンの href と取り違えない)
    assert f'<a class="btn btn-sm btn-outline mt-4 border-base-300" href="{url}">' in body
    assert "すべて外す</a>" in body
    assert "まだ 1 件もありません。" not in body

    body = client_logged_in.get(url, {"year": "2026"}).content.decode()
    assert "作り物の夏の休み" in body
    assert "年の休日はありません" not in body

    # 検索の言葉があれば core の空の枠
    body = client_logged_in.get(url, {"year": "2025", "q": "作り物"}).content.decode()
    assert "「作り物」に合う物はありません。" in body
    assert "年の休日はありません" not in body
