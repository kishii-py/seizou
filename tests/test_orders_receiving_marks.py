"""受注の側の入荷の入口 (決め 11-06 18:3x。依頼書 N183)。

- 受注の詳細の明細の行のメニュー (orders/_line_menu.html) に「入荷を記録する」。入荷の新規登録を ?order_line=<pk> で小窓に開く。
  出すのは receiving の部品があり、can_write "receiving.Receiving" が真の人だけ (部署の判定が on なら、入荷を部署で開ける人)。
  受注が取消でなく、残が 0 より大きい明細だけ (出荷・指示書の発行・発注と同じ組)。
- 使い方の業務の流れの章 (core.manual.FLOWS) の「注文を受けてから入金まで」に、受注の後・指示書の前の入荷の手順。
  入荷の部品が無い会社 (目録に入荷の画面が無い) では落ちる (土台の manual_flows のとおり)。
受注・出荷は services で作る。人と名前と品番は作り物。

N184 (決め 11-06 18:3x と本体の決め 11-06 20:1x) で足した物:
- 受注の詳細の明細の表の「預かり」の列 (「検収」と「残」の間。値は「引当の合計 / 受注の数」。引当は 1 回の問い合わせで読む)。
- 生産管理ボードの「預かり」の列 (board__allocated。「在庫」と「指示書」の間。0 は —。CSV は数) と、引当が受注の数より
  少ない行の「預かり待ち」の札 (会社の設定 receiving_required が off でないときだけ)。盤の問い合わせの数は行の数で増えない。
- 盤の行のメニューの「入荷を記録する」と、盤と詳細の位置 (「指示書を発行する」の直後)。
入荷は receiving の services (create_receiving) で入れる。
"""

import re
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.core import manual
from seizou.orders import services as orders_services
from seizou.orders.models import Order
from seizou.orders.templatetags import orders_detail
from seizou.receiving import services as receiving_services
from seizou.shipping import services as shipping_services
from tests import test_orders_board
from tests.query_helpers import count_queries
from tests.test_billing_payment_views import SHIPPED_AT
from tests.test_core_access import account
from tests.test_core_entry_buttons import _page, _section
from tests.test_core_manual_flows import booklet_html, flows_box, without
from tests.test_orders_board import _line
from tests.test_orders_board import ORDER_DATE, _cells, _tr

# 注残の盤の試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
user = test_orders_board.user
logged_in = test_orders_board.logged_in
customer = test_orders_board.customer
item = test_orders_board.item

ENTRY = "入荷を記録する"


def _receiving_href(line):
    return f"{reverse('receiving:receiving_create')}?order_line={line.pk}"


def _lines_html(client, line):
    response = client.get(reverse("orders:order_detail", args=[line.order_id]))
    assert response.status_code == 200
    return _section(response.content.decode(), "order-lines-title")


# --- 1. 受注の詳細の行のメニュー ---


@pytest.mark.django_db
def test_detail_line_menu_offers_receiving_in_modal(logged_in, user, customer, item):
    line = _line(user, customer, item)

    lines_html = _lines_html(logged_in, line)

    href = _receiving_href(line)
    assert lines_html.count(ENTRY) == 1
    assert f'href="{href}" data-modal-form="{href}"' in lines_html


@pytest.mark.django_db
def test_receiving_entry_opens_form_for_the_line(logged_in, user, customer, item):
    line = _line(user, customer, item, qty="5")

    response = logged_in.get(_receiving_href(line), HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    # form が明細の部品・得意先・残りの数・出所 = 預かりを初期値にする (?order_line= が form に届いた)
    initial = response.context["form"].initial
    assert initial["item"] == item.pk
    assert initial["partner"] == customer.pk
    assert initial["qty"] == Decimal("5")
    assert initial["source"] == "consigned"


@pytest.mark.django_db
def test_receiving_entry_hidden_on_cancelled_and_finished_lines(logged_in, user, customer, item):
    cancelled = _line(user, customer, item)
    orders_services.cancel_order(cancelled.order, user)
    finished = _line(user, customer, item, qty="2")
    shipping_services.ship(finished, Decimal("2"), SHIPPED_AT, user)

    for line in (cancelled, finished):
        lines_html = _lines_html(logged_in, line)
        assert ENTRY not in lines_html
        assert _receiving_href(line) not in lines_html
        assert "部品の情報を見る" in lines_html  # ⋮ は空にならない


@pytest.mark.django_db
def test_receiving_entry_follows_department(settings, client, user, customer, item):
    settings.SEIZOU = {"access_control": {"department": "on", "write": "on", "money": "on"}}
    line = _line(user, customer, item)
    detail_url = reverse("orders:order_detail", args=[line.order_id])
    sales = accounts_services.create_department("受注と出荷", ["orders", "shipping", "masters"], ["orders", "shipping"], None)
    floor = accounts_services.create_department("受け入れ", ["orders", "receiving", "masters"], ["orders"], None)
    # 入荷の部品を部署で開けない人 (受注と出荷だけ) には出さない
    jimu = account("jimu", departments=[sales], permissions=["slip_edit"])
    # 入荷の部品を部署で開ける人と、部署の判定の外の人 (システム管理者) には出す
    genba = account("genba", departments=[floor], permissions=["slip_edit"])
    admin = get_user_model().objects.create_superuser("admin-tester", password="pass-for-tests-only")

    denied = _section(_page(client, jimu, detail_url), "order-lines-title")
    assert ENTRY not in denied
    assert _receiving_href(line) not in denied
    assert "部品の情報を見る" in denied
    for person in (genba, admin):
        allowed = _section(_page(client, person, detail_url), "order-lines-title")
        assert ENTRY in allowed, person.username
        assert _receiving_href(line) in allowed, person.username


# --- 3. 使い方の業務の流れの章 ---

RECEIVING_DO = "得意先からワークや支給材が届いたら「入荷」で記録する (表面処理のように現物が無いと始められない会社向け)。"


def test_sales_flow_has_receiving_step_after_order_before_workorder():
    sales = manual.FLOWS[0]
    assert sales["title"] == "注文を受けてから入金まで"
    names = [step["url_name"] for step in sales["steps"]]
    at = names.index("receiving:receiving_create")
    assert names[:at] == ["orders:order_create", "connect:inbox_accept"]
    assert names[at + 1] == "workorders:workorder_create"
    step = sales["steps"][at]
    assert step["do"] == RECEIVING_DO
    # 入荷は受注の段を進めない (段の帯の名前は書かない)
    assert step["stage"] == "" and step["note"] == ""


@pytest.fixture
def folder(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    (tmp_path / "manual").mkdir()
    return tmp_path / "manual"


@pytest.mark.django_db
def test_manual_page_and_booklet_show_receiving_step(client, user, folder):
    client.force_login(user)
    anchor = 'href="#receiving_receiving_create">入荷の新規登録</a>'
    pages = [booklet_html(), *(client.get(reverse("core:manual"), params).content.decode() for params in ({}, {"print": "1"}))]
    for html in pages:
        box = flows_box(html)
        assert anchor in box
        assert RECEIVING_DO in box
        assert box.index(anchor) < box.index('href="#workorders_workorder_create"')
        assert 'id="receiving_receiving_create"' in html


def test_receiving_step_drops_without_receiving(monkeypatch):
    without(monkeypatch, "receiving")
    sales = manual.manual_flows()[0]
    anchors = [step["anchor"] for step in sales["steps"]]
    assert "receiving_receiving_create" not in anchors
    assert anchors[:3] == ["orders_order_create", "connect_inbox_accept", "workorders_workorder_create"]


# --- N184: 預かりの列・預かり待ちの札・盤の行のメニュー ---

_TH = re.compile(r"<th[^>]*>(.*?)</th>", re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
BADGE = '<span class="badge badge-sm badge-soft badge-warning">預かり待ち</span>'


def _text(html):
    """枡の字 (絵と tag を外し、空白を 1 つにまとめる)。"""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", re.sub(r"<svg.*?</svg>", "", html, flags=re.S))).strip()


def _receive(line, qty, user):
    """得意先から預かった現物を、明細から入荷として入れる (同じ atomic の中で明細に引き当てる)。"""
    return receiving_services.create_receiving(
        {
            "item": line.item,
            "partner": line.order.customer,
            "source": "consigned",
            "qty": Decimal(qty),
            "received_on": ORDER_DATE,
        },
        user,
        order_line=line,
    )


def _detail_heads_and_cells(client, line):
    """受注の詳細の明細の表の見出しの字と、最初の行の枡の字。"""
    table = _lines_html(client, line)
    table = table[: table.index("</table>")]
    heads = [_text(th) for th in _TH.findall(table)]
    row = re.search(r'<tr class="h-10"[^>]*>(.*?)</tr>', table, re.S).group(1)
    return heads, [_text(td) for td in _TD.findall(row)]


def _menu_labels(html):
    """最初の行のメニューの li の字の並び (絵と tag を外す。線の li は "")。"""
    ul = re.search(r'<ul class="menu dropdown-content[^"]*">(.*?)</ul>', html, re.S).group(1)
    return [_text(li) for li in re.findall(r"<li[^>]*>(.*?)</li>", ul, re.S)]


def _board(client, **params):
    return client.get(reverse("orders:orderline_list"), params)


def _tfoot(body):
    return re.search(r"<tfoot>.*?</tfoot>", body, re.S).group(0)


# --- N184 1. 受注の詳細の「預かり」の列 ---


@pytest.mark.django_db
def test_detail_consigned_column_between_acceptance_and_remaining(logged_in, user, customer, item):
    line = _line(user, customer, item, qty="5")

    heads, cells = _detail_heads_and_cells(logged_in, line)
    at = heads.index("預かり")
    assert heads[at - 1] == "検収"
    assert heads[-1] == "残" and at == len(heads) - 2  # 最後の欄は「残」のまま (E2E が行の最後の td で残を読む)
    assert len(cells) == len(heads)
    assert cells[at] == "0 / 5"

    _receive(line, "5", user)

    heads, cells = _detail_heads_and_cells(logged_in, line)
    assert cells[heads.index("預かり")] == "5 / 5"
    assert cells[-1] == "5"  # 残は変わらない (入荷は出荷でない)


@pytest.mark.django_db
def test_detail_allocations_read_once_for_all_lines(user, customer, item, django_assert_num_queries):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100")} for qty in ("5", "4", "3")],
        user,
        order_date=ORDER_DATE,
    )
    first, second, _third = order.lines.order_by("line_no")
    _receive(first, "2", user)
    _receive(second, "4", user)
    order = Order.objects.prefetch_related("lines").get(pk=order.pk)

    with django_assert_num_queries(1):
        rows = orders_detail.order_line_allocations(order)

    assert [(line.line_no, total) for line, total in rows] == [(1, Decimal("2")), (2, Decimal("4")), (3, Decimal("0"))]


@pytest.mark.django_db
def test_detail_allocations_are_zero_without_receiving(monkeypatch, user, customer, item, django_assert_num_queries):
    line = _line(user, customer, item, qty="5")
    _receive(line, "5", user)
    order = Order.objects.prefetch_related("lines").get(pk=line.order_id)
    monkeypatch.setattr(orders_detail, "_allocation_model", lambda: None)

    with django_assert_num_queries(0):
        rows = orders_detail.order_line_allocations(order)

    assert rows == [(line, Decimal("0"))]


# --- N184 2. 生産管理ボードの「預かり」の列と「預かり待ち」の札 ---


@pytest.mark.django_db
def test_board_consigned_column_between_stock_and_workorder(logged_in, user, customer, item):
    empty = _line(user, customer, item, qty="5")
    held = _line(user, customer, item, qty="5")
    _receive(held, "3", user)

    response = _board(logged_in)
    labels = [col.label for col in response.context["columns"]]
    at = labels.index("預かり")
    assert labels[at - 1 : at + 2] == ["在庫", "預かり", "指示書"]
    column = response.context["columns"][at]
    assert column.name == "board__allocated" and column.numeric and column.sortable
    cells = {row["obj"].pk: row["cells"][at]["value"] for row in response.context["rows"]}
    assert cells[empty.pk] == 0
    assert cells[held.pk] == 3

    body = response.content.decode()
    head = re.search(r'<th class="([^"]*)"[^>]*>\s*<a[^>]*sort=board__allocated"', body)
    assert head and "text-right" in head.group(1)
    assert _cells(_tr(body, empty.pk))[at] == "—"
    assert '<span class="text-base-content/60">—</span>' in _TD.findall(_tr(body, empty.pk))[at]
    assert _cells(_tr(body, held.pk))[at] == "3"
    assert _cells(_tfoot(body))[at] == ""  # 合計は出さない


@pytest.mark.django_db
def test_board_sorts_by_allocated_total(logged_in, user, customer, item):
    small = _line(user, customer, item, qty="5")
    _receive(small, "1", user)
    large = _line(user, customer, item, qty="5")
    _receive(large, "4", user)

    for sort, first in (("board__allocated", small), ("-board__allocated", large)):
        response = _board(logged_in, sort=sort)
        assert response.context["sort"] == sort
        assert response.context["rows"][0]["obj"].pk == first.pk, sort


@pytest.mark.django_db
@pytest.mark.parametrize(("mode", "shown"), [("stop", True), ("warn", True), ("off", False), (None, False)])
def test_board_waiting_badge_follows_setting(settings, logged_in, user, customer, item, mode, shown):
    none = _line(user, customer, item, qty="5")
    short = _line(user, customer, item, qty="5")
    _receive(short, "2", user)
    full = _line(user, customer, item, qty="5")
    _receive(full, "5", user)
    settings.SEIZOU = {} if mode is None else {"receiving_required": mode}

    body = _board(logged_in).content.decode()

    for line in (none, short):
        assert (BADGE in _tr(body, line.pk)) is shown, (mode, line.pk)
    assert BADGE not in _tr(body, full.pk)  # 引当が受注の数に届いた行には出さない
    assert "預かり待ち" not in _tfoot(body)


@pytest.mark.django_db
def test_board_query_count_does_not_grow_with_allocated_rows(logged_in, user, customer, item):
    def add_lines(count):
        for _ in range(count):
            _receive(_line(user, customer, item, qty="5"), "3", user)

    add_lines(3)
    _board(logged_in)  # 最初の 1 回の控え (session など) を外す (tests/test_orders_board.py の形)
    small, response = count_queries(logged_in)
    assert len(response.context["rows"]) == 3

    add_lines(9)
    large, response = count_queries(logged_in)
    assert len(response.context["rows"]) == 12
    assert all(row["obj"].board.allocated == Decimal("3") for row in response.context["rows"])
    assert small == large, (small, large)


@pytest.mark.django_db
def test_board_csv_has_consigned_numbers(logged_in, user, customer, item):
    empty = _line(user, customer, item, qty="5")
    held = _line(user, customer, item, qty="5")
    _receive(held, "3", user)

    csv_body = _board(logged_in, format="csv").content.decode()
    header, *rows = csv_body.lstrip("﻿").splitlines()
    heads = header.split(",")
    at = heads.index("預かり")
    assert heads[at - 1 : at + 2] == ["在庫", "預かり", "指示書"]
    values = {row.split(",")[0]: Decimal(row.split(",")[at]) for row in rows}
    assert values == {empty.order.order_no: Decimal("0"), held.order.order_no: Decimal("3")}


# --- N184 3. 行のメニューの「入荷を記録する」(盤と詳細。「指示書を発行する」の直後) ---


@pytest.mark.django_db
def test_board_row_menu_offers_receiving_after_issue(logged_in, user, customer, item):
    line = _line(user, customer, item)

    tr_html = _tr(_board(logged_in).content.decode(), line.pk)

    labels = _menu_labels(tr_html)
    assert labels[labels.index("指示書を発行する") + 1] == ENTRY
    assert labels.index(ENTRY) < labels.index("")  # 1 組目 (社内で進める) の中
    href = _receiving_href(line)
    assert f'href="{href}" data-modal-form="{href}"' in tr_html
    assert tr_html.count(ENTRY) == 1


@pytest.mark.django_db
def test_detail_line_menu_puts_receiving_after_issue(logged_in, user, customer, item):
    line = _line(user, customer, item)

    labels = _menu_labels(_lines_html(logged_in, line))

    assert labels[labels.index("指示書を発行する") + 1] == ENTRY
    assert labels.index(ENTRY) < labels.index("発注する")


@pytest.mark.django_db
def test_board_receiving_entry_hidden_on_cancelled_and_finished_lines(logged_in, user, customer, item):
    cancelled = _line(user, customer, item)
    orders_services.cancel_order(cancelled.order, user)
    finished = _line(user, customer, item, qty="2")
    shipping_services.ship(finished, Decimal("2"), SHIPPED_AT, user)

    body = _board(logged_in, f_open="0").content.decode()

    for line in (cancelled, finished):
        tr_html = _tr(body, line.pk)
        assert ENTRY not in tr_html
        assert _receiving_href(line) not in tr_html
        assert "受注の詳細" in tr_html  # ⋮ は空にならない


@pytest.mark.django_db
def test_board_receiving_entry_follows_department(settings, client, user, customer, item):
    settings.SEIZOU = {"access_control": {"department": "on", "write": "on", "money": "on"}}
    line = _line(user, customer, item)
    board_url = reverse("orders:orderline_list")
    sales = accounts_services.create_department("受注と出荷", ["orders", "shipping", "masters"], ["orders", "shipping"], None)
    floor = accounts_services.create_department("受け入れ", ["orders", "receiving", "masters"], ["orders"], None)
    # 入荷の部品を部署で開けない人には出さない
    jimu = account("jimu", departments=[sales], permissions=["slip_edit"])
    # 入荷の部品を部署で開ける人には出す
    genba = account("genba", departments=[floor], permissions=["slip_edit"])

    denied = _tr(_page(client, jimu, board_url), line.pk)
    assert ENTRY not in denied
    assert _receiving_href(line) not in denied
    allowed = _tr(_page(client, genba, board_url), line.pk)
    assert ENTRY in allowed
    assert _receiving_href(line) in allowed
