"""詳細の頭の型 (決め 11-05 16:2x。N163)。

1 段目にパンくず (一覧の名前 › 題。一覧の名前は土台の BaseDetailView が一覧の画面の screen_name から引く)、
2 段目の左に大きい題・状態の印・「?」、右にボタン (副 → 「⋯」→ 主 1 つ)。赤い操作 (取消・削除) は「⋯」のメニューの最後。
パンくずがあれば「一覧に戻る」は出さない。作り物のデータは全部 services で作る。
"""

import re
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

import seizou.masters.views
from seizou.billing import services as billing_services
from seizou.connect import services as connect_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from seizou.workorders import services as workorders_services
from tests import test_detail_columns
from tests.test_r10_inbox_accept import _line, _payload

# 受注と発注の詳細の 2 列の試験の作り物を借りる (pytest は module の名前で fixture を見つける)。
customer = test_detail_columns.customer
subcontractor = test_detail_columns.subcontractor
item = test_detail_columns.item
order = test_detail_columns.order
purchase_order = test_detail_columns.purchase_order

#: 編集の判定を効かせる (Access の行の無い利用者は書けない。見るのはできる)
ALL_ON = {"department": "on", "write": "on", "money": "on"}
MORE_MENU = '<details class="dropdown dropdown-end row-menu" data-detail-more>'


@pytest.fixture
def cut(user):
    return masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)


@pytest.fixture
def workorder(user, order, cut):
    """発行 (未着手) の指示書。受注の 1 行目から 10 個。"""
    line = order.lines.order_by("line_no").first()
    return workorders_services.issue_workorder(line, Decimal("10"), [{"process": cut, "planned_qty": Decimal("10")}], user)


@pytest.fixture
def shipment(user, order):
    line = order.lines.order_by("line_no").first()
    return shipping_services.ship(line, Decimal("10"), timezone.localdate(), user)


@pytest.fixture
def delivery_note(user, customer, shipment):
    return shipping_services.create_delivery_note(customer, [shipment], user)


@pytest.fixture
def invoice(user, customer, shipment):
    return billing_services.create_invoice(customer, [shipment], user, issued_on=timezone.localdate())


@pytest.fixture
def inbox(user, customer):
    payload = _payload(str(uuid.uuid4()), [_line(1, "A-100")])
    return connect_services.record_inbox(customer, payload, user, received_at=timezone.now() - timedelta(minutes=1))


@pytest.fixture
def holiday(user):
    return masters_services.create_holiday({"date": date(2026, 12, 29), "name": "作り物の会社の休み", "kind": "company"}, user)


def _get(client, name, pk):
    response = client.get(reverse(name, args=[pk]))
    assert response.status_code == 200, name
    return response, response.content.decode()


def _crumb(body):
    return re.search(r'<nav aria-label="パンくず"[^>]*>.*?</nav>', body, re.S).group(0)


def _actions(body):
    """頭の右のボタンの箱 (中に div は無い)。"""
    start = body.index("data-detail-actions")
    return body[start : body.index("</div>", start)]


def _more(body):
    start = body.index(MORE_MENU)
    return body[start : body.index("</details>", start)]


@pytest.mark.django_db
def test_crumb_shows_list_name_on_every_detail(
    logged_in, item, customer, order, purchase_order, workorder, shipment, delivery_note, invoice, inbox
):
    cases = (
        ("masters:item_detail", item, "masters:item_list", "部品マスター"),
        ("masters:partner_detail", customer, "masters:partner_list", "取引先"),
        ("orders:order_detail", order, "orders:order_list", "受注"),
        ("purchasing:purchaseorder_detail", purchase_order, "purchasing:purchaseorder_list", "発注"),
        ("workorders:workorder_detail", workorder, "workorders:workorder_list", "指示書"),
        ("shipping:shipment_detail", shipment, "shipping:shipment_list", "出荷"),
        ("shipping:deliverynote_detail", delivery_note, "shipping:deliverynote_list", "納品書"),
        ("billing:invoice_detail", invoice, "billing:invoice_list", "請求書"),
        ("connect:inbox_detail", inbox, "connect:inbox_list", "受信した注文"),
    )
    for name, obj, list_url_name, list_name in cases:
        response, body = _get(logged_in, name, obj.pk)
        crumb = _crumb(body)
        assert f'href="{reverse(list_url_name)}">{list_name}</a>' in crumb, name
        assert f'aria-current="page">{escape(str(obj))}</span>' in crumb, name
        assert body.index('<nav aria-label="パンくず"') < body.index('<h1 class="truncate'), name
        assert "一覧に戻る" not in body, name
    response = _get(logged_in, "masters:item_detail", item.pk)[0]
    assert response.context["list_name"] == "部品マスター"


@pytest.mark.django_db
def test_back_to_list_only_when_no_list_name(logged_in, item, monkeypatch):
    monkeypatch.setattr(seizou.masters.views.ItemListView, "screen_name", "")

    response, body = _get(logged_in, "masters:item_detail", item.pk)

    assert response.context["list_name"] == ""
    assert '<nav aria-label="パンくず"' not in body
    actions = _actions(body)
    assert "一覧に戻る" in actions
    assert f'href="{reverse("masters:item_list")}"' in actions


@pytest.mark.django_db
def test_head_order_and_one_primary(logged_in, item, order, workorder, purchase_order):
    body = _get(logged_in, "orders:order_detail", order.pk)[1]
    edit = f'<a class="btn btn-primary" href="{reverse("orders:order_update", args=[order.pk])}">'
    marks = ('<nav aria-label="パンくず"', '<h1 class="truncate', 'aria-label="この画面の説明"', "data-detail-actions")
    positions = [body.index(mark) for mark in marks]
    start = body.index("data-detail-actions")
    positions += [body.index("data-detail-more", start), body.index(edit, start)]
    assert positions == sorted(positions)
    assert body[start : body.index('id="order-stages"')].count("btn-primary") == 1

    actions = _actions(_get(logged_in, "workorders:workorder_detail", workorder.pk)[1])
    assert actions.index("現品票</a>") < actions.index("data-detail-more") < actions.index("着手</button>")
    assert actions.count("btn-primary") == 1

    actions = _actions(_get(logged_in, "purchasing:purchaseorder_detail", purchase_order.pk)[1])
    assert actions.index("data-detail-more") < actions.index("再送</button>")
    assert actions.count("btn-primary") == 1

    actions = _actions(_get(logged_in, "masters:item_detail", item.pk)[1])
    assert actions.count("<a ") == 1 and "編集</a>" in actions
    assert "data-detail-more" not in actions and "<form" not in actions


@pytest.mark.django_db
def test_red_actions_live_in_more_menu(logged_in, order, purchase_order, workorder, delivery_note, invoice, holiday):
    cases = (
        ("orders:order_detail", order, "orders:order_cancel", "取消", True),
        ("purchasing:purchaseorder_detail", purchase_order, "purchasing:purchaseorder_cancel", "取消", True),
        ("workorders:workorder_detail", workorder, "workorders:workorder_cancel", "取消", True),
        ("shipping:deliverynote_detail", delivery_note, "shipping:deliverynote_cancel", "取消", True),
        ("billing:invoice_detail", invoice, "billing:invoice_cancel", "取消", True),
        ("masters:holiday_detail", holiday, "masters:holiday_delete", "削除", False),
    )
    for name, obj, action, word, modal in cases:
        body = _get(logged_in, name, obj.pk)[1]
        url = reverse(action, args=[obj.pk])
        menu = _more(body)
        last = menu.rsplit("<li>", 1)[1]
        assert f'href="{url}"' in last and "text-error" in last and f"{word}</a>" in last, name
        assert "btn-error" not in body, name
        assert (f'href="{url}" data-modal-form="{url}"' in menu) is modal, name
        if not modal:
            assert "data-modal-form" not in menu, name
        # 赤い操作はボタンの列に出さない (「⋯」の外に同じ URL の link が無い)
        assert body.count(f'href="{url}"') == 1, name


@pytest.mark.django_db
def test_no_more_menu_without_red_action(client, settings, user, customer, item, order):
    client.force_login(user)
    body = _get(client, "masters:item_detail", item.pk)[1]
    assert "data-detail-more" not in body

    lines = [{"item": item, "qty": Decimal("1"), "unit_price": Decimal("1")}]
    cancelled = orders_services.create_order(customer, lines, user, order_date=date(2026, 11, 2))
    orders_services.cancel_order(cancelled, user)
    body = _get(client, "orders:order_detail", cancelled.pk)[1]
    assert "data-detail-more" not in body

    # 書く権限の無い人 (編集の判定を効かせた設定の、Access の行の無い利用者) は「⋯」も取消も無い
    settings.SEIZOU = {"access_control": dict(ALL_ON)}
    body = _get(client, "orders:order_detail", order.pk)[1]
    assert "data-detail-more" not in body
    assert f'href="{reverse("orders:order_cancel", args=[order.pk])}"' not in body
