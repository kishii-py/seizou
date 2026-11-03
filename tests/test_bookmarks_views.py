"""ブックマークの帯の ☆ と小窓・最初の画面の「よく使う画面」・登録と解除の口 (N37 の 2 段目)。

画面の HTML は Django の test client で開き、☆ の小窓 (<details>) と「よく使う画面」の節を切り出して見る
(左のメニューにも同じ link があるため)。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.bookmarks import services
from seizou.bookmarks.models import Bookmark
from seizou.bookmarks.services import catalog
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services

TOGGLE_URL = "/bookmarks/toggle/"


@pytest.fixture(autouse=True)
def fresh_catalog():
    catalog.cache_clear()
    yield
    catalog.cache_clear()


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def order(user):
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=date(2026, 11, 3),
    )


def html_of(response) -> str:
    return response.content.decode()


def star_of(html: str) -> str:
    """上の帯の ☆ の <details> (summary と小窓) を切り出す。"""
    found = re.search(r'<details class="dropdown dropdown-end">\s*<summary[^>]*aria-label="ブックマーク".*?</details>', html, re.S)
    assert found, "上の帯に ☆ が無い"
    return found.group(0)


def entrances_of(html: str) -> str:
    """最初の画面の「よく使う画面」の節を切り出す。"""
    found = re.search(r'<section[^>]*aria-labelledby="entrances-title".*?</section>', html, re.S)
    assert found, "最初の画面に「よく使う画面」の節が無い"
    return found.group(0)


def forms_of(html: str) -> list[str]:
    return re.findall(r'<form method="post" action="/bookmarks/toggle/".*?</form>', html, re.S)


def test_star_on_list_screen_offers_registration(client_logged_in):
    star = star_of(html_of(client_logged_in.get("/orders/")))

    assert "このページを登録" in star
    [form] = forms_of(star)
    assert "csrfmiddlewaretoken" in form
    assert 'name="url_name" value="orders:order_list"' in form
    assert 'name="on" value="1"' in form
    assert 'name="next" value="/orders/"' in form
    assert "fill-current" not in star
    assert "登録した画面がここに並びます。" in star


def test_toggle_on_then_star_is_filled_and_listed(client_logged_in, user):
    response = client_logged_in.post(TOGGLE_URL, {"url_name": "orders:order_list", "on": "1", "next": "/orders/"})

    assert response.status_code == 302
    assert response["Location"] == "/orders/"
    assert Bookmark.objects.filter(user=user).count() == 1

    html = html_of(client_logged_in.get("/orders/"))
    assert 'id="seizou-toasts"' not in html  # 済んだ知らせは出さない
    star = star_of(html)
    assert "fill-current" in star
    assert "登録済み" in star and "外す" in star
    assert "このページを登録" not in star
    link = re.search(r'<a class="flex items-center gap-2" href="/orders/">.*?</a>', star, re.S)
    assert link and "受注" in link.group(0) and "表示中" in link.group(0)

    home = entrances_of(html_of(client_logged_in.get("/")))
    assert "☆ で登録した画面" in home
    assert re.search(r'href="/orders/">.*?受注', home, re.S)
    assert 'href="/masters/items/"' not in home

    # 登録と解除の口は POST だけ。
    assert client_logged_in.get(TOGGLE_URL).status_code == 405


def test_toggle_off_leaves_hint_only(client_logged_in, user):
    services.add_bookmark(user, "orders:order_list")
    assert 'href="/masters/items/"' not in entrances_of(html_of(client_logged_in.get("/")))

    response = client_logged_in.post(TOGGLE_URL, {"url_name": "orders:order_list", "on": "0", "next": "/"})

    assert response.status_code == 302
    assert response["Location"] == "/"
    assert not Bookmark.objects.filter(user=user).exists()
    home = entrances_of(html_of(client_logged_in.get("/")))
    assert "上の帯の ☆ で登録した画面がここに並びます。" in home
    assert 'href="/orders/"' not in home and 'href="/masters/items/"' not in home


def test_detail_screen_cannot_be_registered(client_logged_in, order):
    detail_url = reverse("orders:order_detail", args=[order.pk])
    star = star_of(html_of(client_logged_in.get(detail_url)))

    assert "一覧や目録の画面だけ登録できます。" in star
    assert "<form" not in star

    response = client_logged_in.post(
        TOGGLE_URL, {"url_name": "orders:order_detail", "on": "1", "next": detail_url}, follow=True
    )

    assert response.redirect_chain == [(detail_url, 302)]
    assert Bookmark.objects.count() == 0
    # 赤の知らせは base.html の右下の知らせ (core/_toasts.html) が出す。
    toast = re.search(r'<div id="seizou-toasts".*?role="alert".*?</div>\s*</div>', html_of(response), re.S)
    assert toast
    assert "bg-error" in toast.group(0)
    assert "この画面は登録できません。一覧や目録のような、番号の付かない画面だけ登録できます。" in toast.group(0)


def test_next_outside_host_goes_home(client_logged_in):
    for next_url in ["https://example.com/", "//example.com/orders/", ""]:
        response = client_logged_in.post(TOGGLE_URL, {"url_name": "orders:order_list", "on": "1", "next": next_url})
        assert response.status_code == 302
        assert response["Location"] == "/"


@pytest.mark.django_db
def test_anonymous_is_sent_to_login(client):
    response = client.post(TOGGLE_URL, {"url_name": "orders:order_list", "on": "1", "next": "/orders/"})

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("core:login"))
    assert Bookmark.objects.count() == 0
    assert 'aria-label="ブックマーク"' not in html_of(client.get(reverse("core:login")))


def test_lost_bookmark_can_be_removed(client_logged_in, user):
    Bookmark.objects.create(user=user, url_name="old:gone_list", label="昔の画面", position=10)

    star = star_of(html_of(client_logged_in.get("/orders/")))
    assert "昔の画面 (見つかりません)" in star
    [form] = [form for form in forms_of(star) if 'value="old:gone_list"' in form]
    assert "消す" in form and 'name="on" value="0"' in form
    # 最初の画面には見つからなくなった画面を並べない (並べる物が無ければ 1 行の案内)。
    home = entrances_of(html_of(client_logged_in.get("/")))
    assert "昔の画面" not in home
    assert "上の帯の ☆ で登録した画面がここに並びます。" in home

    response = client_logged_in.post(TOGGLE_URL, {"url_name": "old:gone_list", "on": "0", "next": "/orders/"})

    assert response.status_code == 302
    assert response["Location"] == "/orders/"
    assert not Bookmark.objects.filter(user=user).exists()
