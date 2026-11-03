"""ブックマークの services (N37 の 1 段目)。登録・外す・並び・上限・見つからなくなった画面。"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser

from seizou.bookmarks import services
from seizou.bookmarks.models import Bookmark
from seizou.bookmarks.services import catalog
from seizou.core import access
from seizou.core.screens import Screen
from seizou.core.templatetags.seizou_icons import menu_icon_name


@pytest.fixture(autouse=True)
def fresh_catalog():
    # 目録は process の中で覚えるので、前の試験 (monkeypatch で増やした物など) が残らないように作り直す。
    catalog.cache_clear()
    yield
    catalog.cache_clear()


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def other(db):
    return get_user_model().objects.create_user("other", password="pass-for-tests-only")


def test_add_list_screen_and_list(user, django_assert_num_queries):
    row = services.add_bookmark(user, "orders:order_list")

    assert Bookmark.objects.filter(user=user).count() == 1
    assert row.label == "受注"
    assert services.is_bookmarked(user, "orders:order_list")
    catalog()
    # 要求の中では、左のメニュー (access.visible_menu) がテンプレートより先に部署の判定を読み、利用者の物に控える。
    # その後の並びは、登録の表を 1 回読むだけ (画面ごとの部署の判定は控えを読む)。
    access.can_open(user, "orders", record=False)
    with django_assert_num_queries(1):
        items = services.bookmarks_for(user)
    assert items == [
        {
            "url_name": "orders:order_list",
            "label": "受注",
            "url": "/orders/",
            "icon": menu_icon_name("orders:order_list", "orders"),
            "lost": False,
        }
    ]

    # もう 1 度登録しても 1 行のまま。控えの名前は目録の今の名前に直る。
    Bookmark.objects.filter(pk=row.pk).update(label="古い名前")
    again = services.add_bookmark(user, "orders:order_list")
    assert again.pk == row.pk
    assert again.label == "受注"
    assert Bookmark.objects.get(pk=row.pk).label == "受注"
    assert Bookmark.objects.filter(user=user).count() == 1


def test_cannot_add_detail_or_unknown_screen(user):
    assert not services.bookmarkable("orders:order_detail")
    assert not services.bookmarkable("no:such")
    assert services.bookmarkable("orders:order_list")

    for url_name in ["orders:order_detail", "no:such", ""]:
        with pytest.raises(ValueError, match="この画面は登録できません。一覧や目録のような、番号の付かない画面だけ登録できます。"):
            services.add_bookmark(user, url_name)

    assert Bookmark.objects.count() == 0


def test_limit_is_fifty(user, monkeypatch):
    screens = {
        f"fake:screen_{i}": Screen(
            app="orders", view="FakeView", name=f"作り物の画面 {i}", help="", url_name=f"fake:screen_{i}", path=f"/fake/{i}/"
        )
        for i in range(services.MAX_BOOKMARKS + 1)
    }
    monkeypatch.setattr(services, "catalog", lambda: screens)

    names = list(screens)
    for url_name in names[: services.MAX_BOOKMARKS]:
        services.add_bookmark(user, url_name)
    assert Bookmark.objects.filter(user=user).count() == 50

    with pytest.raises(ValueError, match="ブックマークは 50 件までです。使わない物を外してから登録してください。"):
        services.add_bookmark(user, names[-1])
    assert Bookmark.objects.filter(user=user).count() == 50

    # 既に登録した画面は、上限でも同じ行が返る (増えない)。
    assert services.add_bookmark(user, names[0]).url_name == names[0]
    assert Bookmark.objects.filter(user=user).count() == 50


def test_remove_is_always_allowed(user, other):
    services.add_bookmark(user, "orders:order_list")
    services.add_bookmark(other, "orders:order_list")

    assert services.remove_bookmark(user, "orders:order_list") is None
    assert not Bookmark.objects.filter(user=user).exists()
    assert Bookmark.objects.filter(user=other, url_name="orders:order_list").exists()

    # 無い物を外しても何も起きない。
    services.remove_bookmark(user, "orders:order_list")
    services.remove_bookmark(user, "no:such")

    # 目録から消えた画面の行も外せる。
    Bookmark.objects.create(user=user, url_name="old:gone_list", label="昔の画面", position=10)
    services.remove_bookmark(user, "old:gone_list")
    assert not Bookmark.objects.filter(user=user).exists()


def test_lost_screen_is_flagged(user):
    Bookmark.objects.create(user=user, url_name="old:gone_list", label="昔の画面", position=10)

    assert services.bookmarks_for(user) == [
        {"url_name": "old:gone_list", "label": "昔の画面", "url": None, "icon": "list", "lost": True}
    ]


def test_order_is_registration_order(user):
    for url_name in ["shipping:shipment_list", "masters:item_list", "orders:order_list"]:
        services.add_bookmark(user, url_name)

    rows = list(Bookmark.objects.filter(user=user).order_by("position"))
    assert [row.position for row in rows] == [10, 20, 30]
    assert [item["url_name"] for item in services.bookmarks_for(user)] == [
        "shipping:shipment_list",
        "masters:item_list",
        "orders:order_list",
    ]


@pytest.mark.django_db
def test_anonymous_has_none():
    assert services.bookmarks_for(AnonymousUser()) == []
    assert services.bookmarks_for(None) == []
    assert not services.is_bookmarked(AnonymousUser(), "orders:order_list")
