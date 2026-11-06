"""受注の側の入荷の入口 (決め 11-06 18:3x。依頼書 N183)。

- 受注の詳細の明細の行のメニュー (orders/_line_menu.html) に「入荷を記録する」。入荷の新規登録を ?order_line=<pk> で小窓に開く。
  出すのは receiving の部品があり、can_write "receiving.Receiving" が真の人だけ (部署の判定が on なら、入荷を部署で開ける人)。
  受注が取消でなく、残が 0 より大きい明細だけ (出荷・指示書の発行・発注と同じ組)。
- 使い方の業務の流れの章 (core.manual.FLOWS) の「注文を受けてから入金まで」に、受注の後・指示書の前の入荷の手順。
  入荷の部品が無い会社 (目録に入荷の画面が無い) では落ちる (土台の manual_flows のとおり)。
受注・出荷は services で作る。人と名前と品番は作り物。
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.core import manual
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from tests import test_orders_board
from tests.test_billing_payment_views import SHIPPED_AT
from tests.test_core_access import account
from tests.test_core_entry_buttons import _page, _section
from tests.test_core_manual_flows import booklet_html, flows_box, without
from tests.test_orders_board import _line

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
        assert "部品の小窓" in lines_html  # ⋮ は空にならない


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
    assert "部品の小窓" in denied
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
