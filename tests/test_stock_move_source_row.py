"""在庫の動きの詳細の「元の記録」の行 (候補 C89)。

在庫の動き 1 件の詳細の基本の表の最後に「元の記録」の行を出し、製造の完了は元の指示書、受入は元の受入の詳細へ
同じタブで移る link にする。払い出しと棚卸しの調整は「—」。部署の判定が on で、元の記録の部品を部署で使わない人には
link にせず字だけで出す (stock の tag move_source)。在庫の動きでない詳細には行を足さない (土台の block basic_extra は空)。
受注・指示書・発注・受入は services で作る。人・会社・品番は作り物。
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.purchasing import services as purchasing_services
from seizou.stock import services
from seizou.stock.models import KIND_RECEIVED, StockMove
from tests import test_stock_services
from tests.test_core_access import account
from tests.test_core_department_buttons import _departments
from tests.test_core_entry_buttons import _page
from tests.test_stock_services import DAY1, DAY2, ORDER_DATE, _issue, _line
from tests.test_stock_views import _detail_items

# 在庫の services の試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
user = test_stock_services.user
customer = test_stock_services.customer
subcontractor = test_stock_services.subcontractor
items = test_stock_services.items
processes = test_stock_services.processes
no_http = test_stock_services.no_http

#: 部署の判定だけを on にし、編集の権限と金額の閲覧も on (作り物の会社の種と同じ形)
DEPARTMENT_ON = {"access_control": {"department": "on", "write": "on", "money": "on"}}
DEPARTMENT_LOG = {"access_control": {"department": "log", "write": "on", "money": "on"}}


def _moves(user, customer, subcontractor, item, processes):
    """製造の完了・受入・払い出し・棚卸しの調整の動きと、元の指示書と受入。"""
    workorder = _issue(user, _line(user, customer, item), processes[:1])
    finished = services.record_finished(workorder, Decimal("2"), user, moved_on=DAY1)
    po = purchasing_services.create_purchase_order(
        subcontractor,
        [{"item": item, "qty": Decimal("5"), "unit_price": Decimal("10")}],
        user,
        order_date=ORDER_DATE,
    )
    receipt = purchasing_services.receive(po.lines.get(), Decimal("5"), DAY2, user)
    received = StockMove.objects.get(kind=KIND_RECEIVED)
    issued = services.record_issued(item, Decimal("1"), DAY2, user)
    adjusted = services.adjust(item, Decimal("3"), DAY2, user)
    return {
        "workorder": workorder,
        "receipt": receipt,
        "finished": finished,
        "received": received,
        "issued": issued,
        "adjusted": adjusted,
    }


def _detail(move):
    return reverse("stock:stockmove_detail", args=[move.pk])


def _source_row(body):
    """data-move-source の行の HTML (行が無ければ空の字)。"""
    start = body.find("data-move-source")
    if start < 0:
        return ""
    return body[start : body.index("</div>", start)]


def _href(url):
    return f'href="{url}"'


@pytest.mark.django_db
def test_finished_and_received_link_to_their_source(client, user, customer, subcontractor, items, processes, no_http):
    """(a) 製造の完了は元の指示書の番号、(b) 受入は受入の字で、それぞれの詳細へ移る link (同じタブ)。最後の項目が「元の記録」。"""
    moves = _moves(user, customer, subcontractor, items[0], processes)
    workorder, receipt = moves["workorder"], moves["receipt"]
    client.force_login(user)

    body = client.get(_detail(moves["finished"])).content.decode()
    fields = _detail_items(body)
    assert list(fields)[-1] == "元の記録"
    assert fields["元の記録"] == workorder.wo_no
    workorder_url = reverse("workorders:workorder_detail", args=[workorder.pk])
    assert f'<a class="link link-primary" href="{workorder_url}">{workorder.wo_no}</a>' in body
    assert "target=" not in _source_row(body)

    body = client.get(_detail(moves["received"])).content.decode()
    fields = _detail_items(body)
    assert list(fields)[-1] == "元の記録"
    assert fields["元の記録"] == moves["received"].source_label() == str(receipt)
    receipt_url = reverse("purchasing:receipt_detail", args=[receipt.pk])
    assert f'<a class="link link-primary" href="{receipt_url}">' in _source_row(body)


@pytest.mark.django_db
def test_issued_and_adjusted_show_dash(client, user, customer, subcontractor, items, processes, no_http):
    """(c) 払い出しと棚卸しの調整は元の記録を持たないので「—」で、行の中に link が無い。"""
    moves = _moves(user, customer, subcontractor, items[0], processes)
    client.force_login(user)
    for kind in ("issued", "adjusted"):
        body = client.get(_detail(moves[kind])).content.decode()
        fields = _detail_items(body)
        assert list(fields)[-1] == "元の記録", kind
        assert fields["元の記録"] == "—", kind
        row = _source_row(body)
        assert row, kind
        assert "<a" not in row, kind


@pytest.mark.django_db
def test_department_on_shows_source_as_text_to_outsiders(settings, client, user, customer, subcontractor, items, processes, no_http):
    """(d) 部署の判定が on: 元の記録の部品を部署で使わない人には、link にせず字だけで出す。

    隠すのは 403 を避けるためではない (詳細の画面は部署を見ないので、押した先の詳細も開ける)。
    部署で使わない人には関わる記録を字だけで出す今の link の形 (納品書の請求書の行・受注の詳細の「流れ」の枠) と、
    同じ基本の表に link を足す候補 C80 にそろえるため。どの人も在庫の動きの詳細そのものは開ける。
    """
    settings.SEIZOU = DEPARTMENT_ON
    moves = _moves(user, customer, subcontractor, items[0], processes)
    workorder, receipt = moves["workorder"], moves["receipt"]
    workorder_url = reverse("workorders:workorder_detail", args=[workorder.pk])
    receipt_url = reverse("purchasing:receipt_detail", args=[receipt.pk])
    depts = _departments()
    seizo = account("seizo-tester", departments=[depts["seizo"]])
    sales = account("sales-tester", departments=[depts["sales"]])

    # 「製造」の部署 (workorders と stock を使う): 指示書は link、受入は字だけ
    body = _page(client, seizo, _detail(moves["finished"]))
    assert _href(workorder_url) in _source_row(body)
    body = _page(client, seizo, _detail(moves["received"]))
    assert _detail_items(body)["元の記録"] == str(receipt)
    assert _href(receipt_url) not in body
    assert "<a" not in _source_row(body)

    # 「受注と出荷」の部署 (purchasing と stock を使う): 受入は link、指示書は番号の字だけ
    body = _page(client, sales, _detail(moves["received"]))
    assert _href(receipt_url) in _source_row(body)
    body = _page(client, sales, _detail(moves["finished"]))
    assert _detail_items(body)["元の記録"] == workorder.wo_no
    assert _href(workorder_url) not in body
    assert "<a" not in _source_row(body)


@pytest.mark.django_db
def test_department_log_links_without_writing_records(settings, client, caplog, user, customer, subcontractor, items, processes, no_http):
    """(e) 部署の判定が log: 「受注と出荷」の人にも指示書への link を出し、部署の記録の行を書かない (行を出すかの判定は record=False)。"""
    settings.SEIZOU = DEPARTMENT_LOG
    moves = _moves(user, customer, subcontractor, items[0], processes)
    workorder = moves["workorder"]
    sales = account("sales-tester", departments=[_departments()["sales"]])

    body = _page(client, sales, _detail(moves["finished"]))
    assert _href(reverse("workorders:workorder_detail", args=[workorder.pk])) in _source_row(body)
    assert [r for r in caplog.records if r.name == "seizou.access" and "(部署)" in r.getMessage()] == []

    # 見方の確かめ: 部署の外の一覧 (部署の門がある画面) を開くと、同じ見方で記録の行が拾える
    _page(client, sales, reverse("workorders:workorder_list"))
    assert [r for r in caplog.records if r.name == "seizou.access" and "(部署)" in r.getMessage()] != []


@pytest.mark.django_db
def test_other_details_have_no_source_row(client, user, customer, subcontractor, items, processes, no_http):
    """(f) 在庫の動きでない詳細 (指示書の詳細) には「元の記録」の行が無い (土台の block basic_extra は空)。"""
    moves = _moves(user, customer, subcontractor, items[0], processes)
    client.force_login(user)
    body = client.get(reverse("workorders:workorder_detail", args=[moves["workorder"].pk])).content.decode()
    assert "data-move-source" not in body
