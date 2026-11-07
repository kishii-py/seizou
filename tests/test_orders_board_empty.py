"""生産管理ボード (注文残の一覧) が空 (検索も絞りの札も無い) のときの枠 (ONB-01。orders/_board_empty.html)。

「はじめての方へ」の「指示書」の段を押した先が、空の表だけで次の一手が無かったので、「受注を入れると、出荷の残りがある明細がここに並びます。」の
1 文と、枠だけのボタン「受注の新規登録」(受注を書ける人にだけ) を出す。検索か札のある 0 件と、行があるときは core の形のまま。
Django の test client で HTML を見る (本物のブラウザは使わない)。名前は作り物だけ。
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services

NONE_YET = "まだ 1 件もありません。"
NEXT_LINE = "受注を入れると、出荷の残りがある明細がここに並びます。"
HX = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "rows"}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def board(client, **params) -> str:
    response = client.get(reverse("orders:orderline_list"), params)
    assert response.status_code == 200
    return response.content.decode()


def create_button() -> str:
    return f'<a class="btn btn-sm btn-outline mt-4 border-base-300" href="{reverse("orders:order_create")}">'


def make_order(user):
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": timezone.localdate() + timedelta(days=3)}],
        user,
        order_date=timezone.localdate(),
    )


def test_empty_board_says_next_step_and_links_order_create(logged_in):
    body = board(logged_in)
    assert NONE_YET in body
    assert NEXT_LINE in body
    assert body.count(create_button()) == 1
    assert "受注の新規登録" in body
    # 頭の「受注の一覧」のボタンは今のまま。空の枠は主色のボタンを足さない
    assert f'href="{reverse("orders:order_list")}" data-tour="order-link"' in body
    assert "btn-primary" not in body[body.index(NONE_YET) :]


def test_empty_board_partial_keeps_list_meta(logged_in):
    partial = logged_in.get(reverse("orders:orderline_list"), **HX).content.decode()
    assert "<html" not in partial
    assert NEXT_LINE in partial
    assert create_button() in partial
    assert "hx-swap-oob" in partial


def test_filtered_or_searched_empty_board_keeps_core_sentence(logged_in):
    for params in ({"q": "合わない字"}, {"f_no_workorder": "1"}):
        body = board(logged_in, **params)
        assert "に合う物はありません。" in body, params
        assert NEXT_LINE not in body, params
        assert NONE_YET not in body, params
        assert create_button() not in body, params


def test_board_with_rows_has_no_empty_frame(user, logged_in):
    order = make_order(user)
    body = board(logged_in)
    assert f'id="row-{order.lines.get().pk}"' in body
    assert NEXT_LINE not in body
    assert NONE_YET not in body


def test_create_button_follows_write_permission(settings, client, user):
    admin = get_user_model().objects.create_superuser("admin-tester", password="pass-for-tests-only")

    def body_for(person):
        client.force_login(person)
        return board(client)

    # 既定 (log) では誰にでも出る
    assert create_button() in body_for(user)

    settings.SEIZOU = {"access_control": {"department": "log", "write": "on", "money": "log"}}
    denied = body_for(user)
    assert create_button() not in denied
    assert "受注の新規登録" not in denied
    assert NEXT_LINE in denied  # 1 文は誰にも出す
    assert create_button() in body_for(admin)
