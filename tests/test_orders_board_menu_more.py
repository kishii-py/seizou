"""盤と受注の詳細の行のメニューの「納品書を作る」(決め 11-04 17:4x の (3)。N130)。

- 納品書の無い出荷 (納品書が取消の物を含む) がある明細の行だけに出し、出荷日が最後の出荷を ?shipments=<pk> で
  納品書の入力へ渡す。入力の土台の小窓で開く (data-modal-form。href も同じ URL)。
- 受注が取消の明細と、残の無い明細にも出す (出荷した物の納品書は要るため)。
- 出荷の無い明細と、出荷が全部納品書に入った明細には出さない。納品書の作成の権限が無い人には出さない (決め 11-04 00:0x)。
- 盤は出荷と納品書を 1 ページ分まとめて読む (行の数で問い合わせの数が増えない)。
「受入を登録」は、発注の明細が受注の明細を指す列が無いので足していない (後の依頼書)。
作り物のデータだけを使う。作り方は tests/test_orders_board_bulk.py を写した。
"""

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services

ORDER_DATE = date(2026, 11, 2)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
LABEL = "納品書を作る"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _order(user, customer, item, *qtys):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100"), "due_date": None} for qty in qtys],
        user,
        order_date=ORDER_DATE,
    )


def _line(user, customer, item, qty="10"):
    return _order(user, customer, item, qty).lines.get()


def _ship(line, qty, user, days=1):
    return shipping_services.ship(line, Decimal(qty), ORDER_DATE + timedelta(days=days), user)


def _board(client, **params):
    return client.get(reverse("orders:orderline_list"), params).content.decode()


def _tr(body, pk):
    match = re.search(rf'<tr id="row-{pk}".*?</tr>', body, re.S)
    assert match, f"row-{pk} が無い"
    return match.group(0)


def _note_link(shipment):
    url = f'{reverse("shipping:deliverynote_create")}?shipments={shipment.pk}'
    return f'href="{url}" data-modal-form="{url}"'


@pytest.mark.django_db
def test_row_menu_offers_note_for_latest_unnoted_shipment(user, logged_in, customer, item):
    line = _line(user, customer, item)
    later = _ship(line, "3", user, days=3)
    earlier = _ship(line, "2", user, days=1)  # pk は後だが、出荷日は前

    tr_html = _tr(_board(logged_in), line.pk)
    assert f'{_note_link(later)}>' in tr_html
    assert tr_html.count(LABEL) == 1
    assert f"shipments={earlier.pk}" not in tr_html

    # 最後の出荷が納品書に入ったら、残りの出荷が対象になる。
    shipping_services.create_delivery_note(customer, [later], user)
    tr_html = _tr(_board(logged_in), line.pk)
    assert _note_link(earlier) in tr_html


@pytest.mark.django_db
def test_no_note_item_without_shipment_or_when_every_shipment_has_note(user, logged_in, customer, item):
    plain = _line(user, customer, item)
    noted = _line(user, customer, item)
    shipment = _ship(noted, "4", user)
    note = shipping_services.create_delivery_note(customer, [shipment], user)

    body = _board(logged_in)
    assert LABEL not in _tr(body, plain.pk)
    assert LABEL not in _tr(body, noted.pk)

    # 納品書を取り消すと、その出荷はまた納品書の候補になる (納品書の入力の候補と同じ決まり)。
    shipping_services.cancel_delivery_note(note, user)
    assert _note_link(shipment) in _tr(_board(logged_in), noted.pk)


@pytest.mark.django_db
def test_note_item_on_finished_and_cancelled_rows(user, logged_in, customer, item):
    done = _line(user, customer, item, qty="2")
    done_shipment = _ship(done, "2", user)
    cancelled = _line(user, customer, item)
    cancelled_shipment = _ship(cancelled, "3", user)
    orders_services.cancel_order(cancelled.order, user)

    # 既定の絞り (出荷の残りがある物だけ) では 2 行とも出ない。
    body = _board(logged_in)
    assert f'id="row-{done.pk}"' not in body
    assert f'id="row-{cancelled.pk}"' not in body

    body = _board(logged_in, f_open="0")
    for line, shipment in ((done, done_shipment), (cancelled, cancelled_shipment)):
        tr_html = _tr(body, line.pk)
        assert _note_link(shipment) in tr_html
        assert "出荷する" not in tr_html  # 残の無い行・取消の行の出荷と指示書の入口は今のまま出ない


@pytest.mark.django_db
def test_no_write_permission_hides_note_item(settings, client, user, customer, item):
    line = _line(user, customer, item)
    shipment = _ship(line, "4", user)
    settings.SEIZOU = {"access_control": {"department": "log", "write": "on", "money": "log"}}
    client.force_login(user)

    assert LABEL not in _tr(_board(client), line.pk)
    assert LABEL not in client.get(reverse("orders:order_detail", args=[line.order_id])).content.decode()

    admin = get_user_model().objects.create_superuser("admin-tester", password="pass-for-tests-only")
    client.force_login(admin)
    assert _note_link(shipment) in _tr(_board(client), line.pk)


@pytest.mark.django_db
def test_order_detail_line_menu_offers_note(user, logged_in, customer, item):
    order = _order(user, customer, item, "10", "5")
    shipped = order.lines.get(line_no=1)
    shipment = _ship(shipped, "4", user)

    detail = logged_in.get(reverse("orders:order_detail", args=[order.pk])).content.decode()

    assert _note_link(shipment) in detail
    assert detail.count(LABEL) == 1  # 出荷の無い 2 行目には出ない
    # 出荷・指示書・発注・入荷の記録 (N183) が 2 行ずつと、納品書が 1 行と、添付ファイルの枠の「添付ファイルを付ける」の 1 つ (N157) と、右上の利用者のメニューの「表示の設定」の 1 つ (N167) と、頭の「出荷登録」のメニューの 2 つ (2 行とも残がある。C15)と、明細の「完了にする」が 2 行 (2 行とも残がある。N400b)。
    assert detail.count("data-modal-form=") == 16


@pytest.mark.django_db
def test_board_reads_shipments_once_per_page(user, logged_in, customer, item):
    first = _line(user, customer, item)
    _ship(first, "1", user)
    _board(logged_in)  # 1 回目の要求で決まる読み込み (セッション・利用者など) を先に済ませる
    with CaptureQueriesContext(connection) as one_row:
        _board(logged_in)

    for _ in range(2):
        line = _line(user, customer, item)
        shipment = _ship(line, "2", user)
        _ship(line, "1", user, days=2)
        shipping_services.create_delivery_note(customer, [shipment], user)
    with CaptureQueriesContext(connection) as three_rows:
        body = _board(logged_in)

    assert body.count(LABEL) == 3
    assert len(three_rows) == len(one_row)


@pytest.mark.django_db
def test_note_link_opens_form_with_shipment_checked_in_modal(user, logged_in, customer, item):
    line = _line(user, customer, item)
    shipment = _ship(line, "4", user)
    other = _ship(_line(user, customer, item), "1", user)
    url = f'{reverse("shipping:deliverynote_create")}?shipments={shipment.pk}'

    response = logged_in.get(url, **MODAL)

    assert response.status_code == 200
    assert "shipping/_form_modal.html" in [t.name for t in response.templates]
    body = response.content.decode()
    assert re.search(rf'<input[^>]*value="{shipment.pk}"[^>]*checked', body)
    assert not re.search(rf'<input[^>]*value="{other.pk}"[^>]*checked', body)
