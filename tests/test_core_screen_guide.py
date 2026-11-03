"""上の帯の「案内」のボタンと小窓の口 (core.guide.screen_guide) と、ユースケースの 4 行 (core.screens.usecase_rows)。

Django の test client で HTML を見る (本物のブラウザは使わない)。データは作り物だけ。
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core.screens import USECASE_KEYS, usecase_rows
from seizou.core.views import HomeView

GUIDE_LABEL = 'aria-label="この画面の案内"'
SCREEN_LABEL = 'aria-label="この画面の説明"'


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def page(client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200, url
    return response.content.decode()


def guide(client, anchor: str):
    return client.get(reverse("core:screen_guide", args=[anchor]), HTTP_HX_REQUEST="true")


def test_usecase_rows_splits_four_keys():
    rows = usecase_rows("使う人: 営業 / いつ: 朝 / 何のために: 受注を見る / 次にすること: 詳細へ")
    assert rows == [("使う人", "営業"), ("いつ", "朝"), ("何のために", "受注を見る"), ("次にすること", "詳細へ")]
    assert usecase_rows("") == []
    assert usecase_rows("使う人: 全員 / 次にすること: 一覧へ / 詳細へ") == [("使う人", "全員"), ("次にすること", "一覧へ / 詳細へ")]


@pytest.mark.parametrize(
    ("url_name", "anchor"),
    [("core:home", "core_home"), ("orders:order_list", "orders_order_list"), ("masters:item_list", "masters_item_list")],
)
def test_topbar_has_guide_button_on_usecase_screens(logged_in, url_name, anchor):
    html = page(logged_in, reverse(url_name))
    assert html.count(GUIDE_LABEL) == 1, url_name
    assert f'hx-get="/guide/{anchor}/"' in html, url_name
    assert 'aria-label="使い方"' in html
    assert html.count(SCREEN_LABEL) == 1


def test_topbar_hides_guide_button_without_usecase(logged_in, monkeypatch):
    monkeypatch.setattr(HomeView, "__doc__", "最初の画面。")
    html = page(logged_in, reverse("core:home"))
    assert GUIDE_LABEL not in html
    assert 'hx-get="/guide/' not in html
    assert 'aria-label="使い方"' in html


def test_guide_returns_four_rows_for_htmx(logged_in):
    response = guide(logged_in, "orders_order_list")
    assert response.status_code == 200
    body = response.content.decode()
    assert re.findall(r'<dt class="text-base-content/70">([^<]+)</dt>', body) == list(USECASE_KEYS)
    assert "<dd>営業</dd>" in body
    assert "受注でできること" in body  # 題は画面の名前 (受注の一覧の screen_name は「受注」)
    assert "btn-primary" not in body


def test_guide_shows_flow_neighbors(logged_in):
    body = guide(logged_in, "shipping_shipment_list").content.decode()
    assert "受注から入金まで" in body
    workorders_url = reverse("orders:orderline_list")
    notes_url = reverse("shipping:deliverynote_list")
    assert re.search(rf'<a class="btn btn-sm btn-outline border-base-300" href="{workorders_url}">.*?前の段: 指示書</a>', body)
    assert re.search(rf'<a class="btn btn-sm btn-outline border-base-300" href="{notes_url}">次の段: 納品書', body)

    items = guide(logged_in, "masters_item_list")
    assert items.status_code == 200
    assert "前の段" not in items.content.decode()
    assert "次の段" not in items.content.decode()


def test_guide_without_htmx_is_400(logged_in):
    response = logged_in.get(reverse("core:screen_guide", args=["core_home"]))
    assert response.status_code == 400


def test_guide_unknown_or_without_usecase_is_404(logged_in, monkeypatch):
    assert guide(logged_in, "nothing_here").status_code == 404
    assert guide(logged_in, "core_home").status_code == 200
    monkeypatch.setattr(HomeView, "__doc__", "最初の画面。")
    assert guide(logged_in, "core_home").status_code == 404


@pytest.mark.django_db
def test_guide_needs_login(client):
    response = guide(client, "core_home")
    assert response.status_code == 302
    assert response.url.startswith(reverse("core:login"))
