"""受入の一覧が空 (検索も絞りも無い) のとき、空の枠の 2 行目を「受入は発注の明細ごとに記録します。…」にし、
枠のボタンを「発注の一覧」にする (UX の見直しの案 list-09。purchasing/_rows.html)。
検索で 0 件のときと行があるときは core/_rows.html のまま。名前は作り物だけ。
"""

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.purchasing import services

HX = {"HTTP_HX_REQUEST": "true"}
EMPTY_HINT = "受入は発注の明細ごとに記録します。受け入れる発注が無ければ、先に発注の一覧で作ります。"


class FakePost:
    def __init__(self):
        self.calls = 0

    def __call__(self, url, data=None, headers=None, timeout=None):
        self.calls += 1
        raise AssertionError("つながりが無いのに HTTP を呼んだ")


@pytest.fixture
def fake_post(monkeypatch):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    return fake


def test_empty_receipt_list_points_to_purchase_orders(client_logged_in):
    body = client_logged_in.get(reverse("purchasing:receipt_list")).content.decode()
    create = reverse("purchasing:receipt_create")
    assert EMPTY_HINT in body
    assert "まだ 1 件もありません。" in body
    assert f'<a class="btn btn-sm btn-outline mt-4 border-base-300" href="{reverse("purchasing:purchaseorder_list")}">' in body
    assert "「新規登録」から 1 件目を登録します。" not in body
    assert f'<a class="btn btn-sm btn-outline mt-4 border-base-300" href="{create}">' not in body
    # 見出しの行の「新規登録」(小窓で開く) は今のまま残る。
    assert f'href="{create}" data-modal-form="{create}"' in body


def test_htmx_empty_returns_receipt_frame(client_logged_in):
    body = client_logged_in.get(reverse("purchasing:receipt_list"), **HX).content.decode()
    assert "<html" not in body
    assert EMPTY_HINT in body
    assert 'id="list-meta"' in body and 'hx-swap-oob="true"' in body


def test_search_miss_keeps_core_message(client_logged_in):
    body = client_logged_in.get(reverse("purchasing:receipt_list"), {"q": "作り物の無い字"}).content.decode()
    assert "「作り物の無い字」に合う物はありません。" in body
    assert EMPTY_HINT not in body


def test_list_with_rows_keeps_core_table(user, client_logged_in, fake_post):
    partner = masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user
    )
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    po = services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("25")}],
        user,
        order_date=timezone.localdate(),
    )
    services.receive(po.lines.get(), Decimal("4"), timezone.localdate(), user)

    body = client_logged_in.get(reverse("purchasing:receipt_list")).content.decode()
    assert po.po_no in body
    assert EMPTY_HINT not in body


def test_purchase_order_list_empty_frame_unchanged(client_logged_in):
    # 土台の 2 行目は発注を書ける人にだけ出る (core/_rows.html の create_url) ので、権限に寄らない 1 行目で見る。
    body = client_logged_in.get(reverse("purchasing:purchaseorder_list")).content.decode()
    assert "まだ 1 件もありません。" in body
    assert EMPTY_HINT not in body
