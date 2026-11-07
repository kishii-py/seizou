"""左のメニューの入口の横の待っている数 (core.context_processors.attach_counts と core/_sidebar.html。決め 11-05 の 1-A)。

Django の test client で HTML を見る (本物のブラウザは使わない)。左のメニューの字は <aside から </aside> までを切り出して見る。
問い合わせの数は RequestFactory で作った要求の context processor と、CaptureQueriesContext で拾った SQL で見る。名前は作り物だけ。
"""

import re
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from seizou.accounts import services as accounts_services
from seizou.connect.models import Inbox
from seizou.core import dashboard, manual
from seizou.core.context_processors import MENU_COUNTS, count_url, menu, seizou
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.views import OrderLineListView
from seizou.workorders import services as workorders_services

BADGE = '<span class="tooltip tooltip-left ml-auto text-xs" data-tip="'
INBOX_TABLE = "connect_inbox"
#: 画面ごとの説明書の標準の組 (core/manual_default/)
MANUAL_DEFAULT = Path(manual.__file__).parent / "manual_default"


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {
            "code": "C001",
            "name": "作り物の得意先",
            "role": "customer",
            "closing_day": 20,
            "payment_month": "next",
            "payment_day": 25,
        },
        user,
    )


def received(customer):
    """受信した注文を 1 件 (状態は「届いた」)。"""
    return Inbox.objects.create(message_id=uuid.uuid4(), partner=customer, kind="order", payload={})


def _aside(html: str) -> str:
    start = html.index("<aside")
    return html[start : html.index("</aside>", start)]


def page(client, url) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def _entry_link(aside: str, url: str) -> str:
    """左のメニューの入口 (href が url の <a>) の字。数の印は入口の <a> の外なので入らない。"""
    match = re.search(rf'<a href="{re.escape(url)}"[^>]*>.*?</a>', aside, re.S)
    assert match, url
    return match.group(0)


def _entry_row(aside: str, url: str) -> str:
    """左のメニューの入口の li (入口の <a> と、その横の数の印の入れ物) の字。"""
    match = re.search(rf'<li[^>]*><a href="{re.escape(url)}"[^>]*>.*?</li>', aside, re.S)
    assert match, url
    return match.group(0)


def _count_link(row: str) -> str:
    """入口の li の中の、数の印の <a> (入口の <a> の後ろ)。"""
    entry_end = row.index("</a>") + len("</a>")
    match = re.search(r'<a href="[^"]*"[^>]*>.*?</a>', row[entry_end:], re.S)
    assert match, row
    return match.group(0)


def _inbox_counts(queries) -> list[str]:
    return [q["sql"] for q in queries if INBOX_TABLE in q["sql"] and "COUNT" in q["sql"].upper()]


def test_no_badge_when_nothing_is_waiting(logged_in):
    aside = _aside(page(logged_in, reverse("core:home")))

    assert reverse("connect:inbox_list") in aside  # 入口は在る
    assert aside.count(BADGE) == 0  # 0 は印を出さない (偽の数字を置かない)


def test_inbox_count_appears_next_to_the_entry(logged_in, customer):
    message = received(customer)
    inbox_url = reverse("connect:inbox_list")

    aside = _aside(page(logged_in, reverse("core:home")))
    row = _entry_row(aside, inbox_url)
    assert 'data-tip="未処理の受信した注文 1 件"><span class="badge badge-soft badge-primary badge-sm">1</span></span>' in row
    assert row.index('<span class="truncate">受信した注文</span>') < row.index(BADGE)  # 印は入口の字の後ろ
    assert BADGE not in _entry_link(aside, inbox_url)  # 印は入口の <a> の外 (<a> の中に <a> を入れない)
    assert aside.count(BADGE) == 1  # ほかの入口には印が無い
    # 印を押すと、未処理 (状態が「届いた」) に絞った受信した注文が開く (決め 11-07 20:5x の 2。今日やることの行と同じ移る先)
    link = _count_link(row)
    assert link.startswith(f'<a href="{inbox_url}?f_status=received" ')
    assert 'aria-label="未処理の受信した注文 1 件"' in link

    Inbox.objects.filter(pk=message.pk).update(status=Inbox.STATUS_ACCEPTED)
    aside = _aside(page(logged_in, reverse("core:home")))
    assert BADGE not in _entry_row(aside, inbox_url)
    assert aside.count(BADGE) == 0


def test_counts_use_at_most_five_queries(user, customer, django_assert_max_num_queries, django_assert_num_queries):
    received(customer)
    shaft = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    today = timezone.localdate()
    # 納期が 2 日前の受注 (指示書の無い明細でもある)
    orders_services.create_order(
        customer,
        [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": today - timedelta(days=2)}],
        user,
        order_date=today - timedelta(days=5),
    )
    request = RequestFactory().get("/")
    request.user = user
    items = seizou(request)["seizou"]["menu"]  # ここでは数えない
    assert {item["url_name"] for item in items if "count" in item} == set(MENU_COUNTS)

    with django_assert_max_num_queries(5):
        shown = {item["url_name"]: bool(item.get("count")) for item in items}
    assert {name for name, on in shown.items() if on} == {
        "connect:inbox_list",
        "orders:orderline_list",
        "workorders:workorder_list",
    }

    with django_assert_num_queries(0):  # 同じ要求でもう 1 回読んでも数え直さない
        assert [bool(item.get("count")) for item in items] == list(shown.values())

    # 数はダッシュボードの「今日やること」と納期アラートの遅れと同じ
    count = {item["url_name"]: item["count"] for item in items if "count" in item}
    tasks = {row["key"]: row["count"] for row in dashboard.today_tasks(user)["rows"]}
    assert count["connect:inbox_list"] == tasks["order"] == 1
    assert count["workorders:workorder_list"] == tasks["workorder"] == 1
    assert count["orders:orderline_list"] == dashboard.due_alerts()["overdue"]["count"] == 1


def test_rows_only_response_does_not_count(logged_in, customer):
    assert Inbox._meta.db_table == INBOX_TABLE  # SQL の中で探す表の名前
    received(customer)
    url = reverse("orders:order_list")

    # 画面の全体では左のメニューを描いて数える
    assert 'data-tip="未処理の受信した注文 1 件"><span class="badge badge-soft badge-primary badge-sm">1</span></span>' in _aside(page(logged_in, url))

    with CaptureQueriesContext(connection) as rows:
        rows_only = logged_in.get(url, {"sort": "order_no"}, HTTP_HX_REQUEST="true")
    assert rows_only.status_code == 200
    assert "seizou-menu-section" not in rows_only.content.decode()  # 行だけ = 左のメニューを描かない
    assert not [q["sql"] for q in rows.captured_queries if INBOX_TABLE in q["sql"]]


def test_login_page_has_no_counts(client, customer):
    received(customer)

    # ログインの画面は block body を上書きして左のメニューを描かない。画面の全体に印が無く、受信した注文を数えない
    with CaptureQueriesContext(connection) as ctx:
        html = page(client, reverse("core:login"))
    assert BADGE not in html
    assert not _inbox_counts(ctx.captured_queries)

    # 未ログインの要求でも入口は作る (visible_menu はアカウント管理の入口だけを外す) が、数は付けない (業務の数を外に出さない)
    request = RequestFactory().get(reverse("core:login"))
    request.user = AnonymousUser()
    items = seizou(request)["seizou"]["menu"]
    assert reverse("connect:inbox_list") in [item["url"] for item in items]
    assert not [item for item in items if "count" in item]


def test_department_without_the_app_has_no_count(settings, client, customer):
    settings.SEIZOU = {"access_control": {"department": "on", "write": "on", "money": "on"}}
    factory = accounts_services.create_department("作り物の製造", ["workorders", "progress", "masters"], ["workorders"], None)
    accounts_services.issue_account("genba", "kumo-tests-2611", [factory], [], {}, None)
    client.force_login(get_user_model().objects.get(username="genba"))
    received(customer)

    # ダッシュボードは「今日やること」が受信した注文を数えるので、部署で開ける指示書の一覧で見る
    with CaptureQueriesContext(connection) as ctx:
        aside = _aside(page(client, reverse("workorders:workorder_list")))
    assert reverse("connect:inbox_list") not in aside
    assert reverse("workorders:workorder_list") in aside
    assert not _inbox_counts(ctx.captured_queries)


def test_count_link_falls_back_to_the_entry_when_the_target_screen_is_closed(settings, client, user, customer):
    # 「指示書」の数の移る先 (生産管理ボード = orders の画面) を部署で開けない人には、印の link を入口そのもの (指示書の一覧) にする
    # (押すと断られる入口を作らない)。開けるかは左のメニューに並ぶ入口 (visible_menu の後) で見て、問い合わせを足さない
    settings.SEIZOU = {"access_control": {"department": "on", "write": "on", "money": "on"}}
    factory = accounts_services.create_department("作り物の製造", ["workorders", "progress", "masters"], ["workorders"], None)
    accounts_services.issue_account("genba", "kumo-tests-2611", [factory], [], {}, None)
    shaft = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    orders_services.create_order(
        customer, [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100")}], user, order_date=timezone.localdate()
    )
    workorders_url = reverse("workorders:workorder_list")
    board_url = reverse("orders:orderline_list")

    client.force_login(get_user_model().objects.get(username="genba"))
    aside = _aside(page(client, workorders_url))
    assert board_url not in aside
    link = _count_link(_entry_row(aside, workorders_url))
    assert link.startswith(f'<a href="{workorders_url}" ')
    assert 'aria-label="指示書の無い明細 1 行"' in link

    # 生産管理ボードを開ける人には、その絞りで開く
    client.force_login(user)
    link = _count_link(_entry_row(_aside(page(client, workorders_url)), workorders_url))
    assert link.startswith(f'<a href="{board_url}?f_open=1&amp;f_no_workorder=1" ')


def test_manual_tells_what_the_menu_count_counts(user):
    # 印の移る先の一覧の「?」(画面ごとの説明書の標準の組) に、左のメニューの数が何の数かの 1 行がある。
    # 2 社の種は、標準の組と同じバイトであることを test_core_manual_default と test_examples_manual が見る。
    labels = {item["url_name"]: item["label"] for item in menu()}
    problems = []
    for url_name, (label, unit, _counter, _target) in MENU_COUNTS.items():
        raw = (MANUAL_DEFAULT / f"{manual.fragment_name(url_name)}.html").read_text(encoding="utf-8")
        text = re.sub(r"\s", "", re.sub(r"<[^>]+>", "", raw))
        for words in (f"左のメニューの「{labels[url_name]}」の横の数", f"{label}の{unit}数"):
            if words not in text:
                problems.append(f"{url_name}: {words}")
    assert problems == [], "\n".join(problems)


def test_menu_count_box_stays_when_the_count_is_zero(logged_in, customer):
    # 印の入れ物 (id = menu-count-<URL の名前から : を除いた字>) は数が 0 でも残る。受入の一覧と発注の詳細は、
    # 小窓で保存した後と 2 秒ごとの取り直しで、この入れ物を hx-select-oob で入れ替える (決め C05-2 = a。初見テスト R5 の候補 C05)
    inbox_url = reverse("connect:inbox_list")
    box = '<span id="menu-count-connectinbox_list" class="ml-auto">'

    aside = _aside(page(logged_in, reverse("core:home")))
    for url_name in MENU_COUNTS:
        assert f'id="menu-count-{url_name.replace(":", "")}"' in aside, url_name
    assert aside.count('id="menu-count-') == len(MENU_COUNTS)  # ダッシュボードと検索の入口には入れ物が無い
    assert aside.count(BADGE) == 0

    message = received(customer)
    aside = _aside(page(logged_in, reverse("core:home")))
    row = _entry_row(aside, inbox_url)
    inside = row[row.index(box) : row.rindex("</span></li>")]
    assert BADGE in inside and ">1</span>" in inside
    assert aside.count(BADGE) == 1

    Inbox.objects.filter(pk=message.pk).update(status=Inbox.STATUS_ACCEPTED)
    row = _entry_row(_aside(page(logged_in, reverse("core:home"))), inbox_url)
    assert box + "</span>" in row  # 空の入れ物が残る (印の <a> は無い)
    assert BADGE not in row and row.count("<a ") == 1


def test_board_count_opens_the_overdue_tab(user, logged_in, customer):
    # 生産管理ボードの数 (納期が過ぎた明細) を押すと、盤のタブ「納期が過ぎた」と同じ組 (f_open=1&f_overdue=1) で開き、
    # 同じ行数が並ぶ (決め 11-07 20:5x の 2。N146 の「移り先は変えない」を置き換える。nav-01)。入口の字は今までどおり絞り無し
    shaft = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    today = timezone.localdate()
    for days in (-3, -1, 2):
        orders_services.create_order(
            customer,
            [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": today + timedelta(days=days)}],
            user,
            order_date=today - timedelta(days=5),
        )
    board_url = reverse("orders:orderline_list")
    overdue_tab = next(tab["params"] for tab in OrderLineListView.presets if tab["label"] == "納期が過ぎた")
    assert overdue_tab == {"f_open": "1", "f_overdue": "1"}
    assert count_url("orders:orderline_list") == board_url + "?" + urlencode(overdue_tab)

    aside = _aside(page(logged_in, reverse("core:home")))
    row = _entry_row(aside, board_url)
    assert _entry_link(aside, board_url).startswith(f'<a href="{board_url}" ')
    link = _count_link(row)
    assert link.startswith(f'<a href="{board_url}?f_open=1&amp;f_overdue=1" ')
    assert 'aria-label="納期が過ぎた明細 2 行"' in link and ">2</span>" in link
    assert not re.search(r"<a [^>]*>(?:(?!</a>).)*<a ", aside, re.S)  # <a> の中に <a> が無い

    response = logged_in.get(board_url, overdue_tab)
    assert response.status_code == 200
    assert [tab["label"] for tab in response.context["presets"] if tab["selected"]] == ["納期が過ぎた"]
    assert len(response.context["object_list"]) == 2 == dashboard._overdue_lines().count()


def test_count_targets_resolve_and_fall_back_to_the_entry():
    # 移る先の URL は全部引ける。合う絞りのまだ無い物 (受入・請求書) は入口そのもの (絞り無し) に移る
    entries = {item["url_name"]: item["url"] for item in menu()}
    for url_name, (_label, _unit, _counter, (target_name, params)) in MENU_COUNTS.items():
        url = count_url(url_name)
        assert url is not None and url.startswith(reverse(target_name)), url_name
        if not params:
            assert url == entries[url_name], url_name
    assert count_url("purchasing:receipt_list") == reverse("purchasing:receipt_list")
    assert count_url("billing:invoice_list") == reverse("billing:invoice_list")


def test_workorder_count_opens_the_board_narrowed_to_lines_without_workorder(user, logged_in, customer):
    # 「指示書」の横の数は明細の行を数えているので、指示書の一覧でなく生産管理ボードを、数える関数と同じ絞り
    # 「指示書の無い明細だけ」で開く (nav-01 の (b))。指示書のある明細は数にも表にも入らない
    shaft = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    cut = masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)
    lines = [
        orders_services.create_order(
            customer, [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100")}], user, order_date=timezone.localdate()
        ).lines.get()
        for _ in range(3)
    ]
    workorders_services.issue_workorder(lines[0], Decimal("10"), [{"process": cut, "planned_qty": Decimal("10")}], user)
    board_url = reverse("orders:orderline_list")
    assert count_url("workorders:workorder_list") == board_url + "?f_open=1&f_no_workorder=1"

    aside = _aside(page(logged_in, reverse("core:home")))
    link = _count_link(_entry_row(aside, reverse("workorders:workorder_list")))
    assert link.startswith(f'<a href="{board_url}?f_open=1&amp;f_no_workorder=1" ')
    assert 'aria-label="指示書の無い明細 2 行"' in link

    response = logged_in.get(board_url, {"f_open": "1", "f_no_workorder": "1"})
    assert response.status_code == 200
    assert [item["obj"] for item in response.context["rows"]] == lines[1:]
    assert len(response.context["rows"]) == 2 == dashboard._lines_without_workorder().count()
