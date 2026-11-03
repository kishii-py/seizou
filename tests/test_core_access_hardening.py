"""精査 A2 の権限の判定の直し (マニュアルの画像・設定の画面の権限の判定・403 の画面の名前・記録だけの金額・ブックマーク・部署の読みの控え) を見る。

人と部署は accounts の services で作る。名前は作り物 (genba = 権限なし・金額なし、keiri = 経理・金額あり、
a2_manager = アカウント管理だけ)。判定を有効にする試験は settings の fixture で SEIZOU を替える
(tests/settings.py は SEIZOU = {} のまま = 3 つとも記録だけ)。Django の test client で HTML と表の状態を見る。
"""

import logging
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.bookmarks import services as bookmarks_services
from seizou.bookmarks.models import Bookmark
from seizou.bookmarks.templatetags import bookmarks_tags
from seizou.core import access
from seizou.core import conf
from seizou.core import services as core_services
from seizou.core.models import SettingOverride
from seizou.manuals import services as manuals_services
from seizou.manuals.models import ManualImage
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.test_core_settings_edit import clear_url, edit_url
from tests.test_manuals_views import png_upload

User = get_user_model()

#: 試験だけの合言葉 (アカウントの発行の確かめに通る長さ)
PASSWORD = "kumo-tests-2611"
ALL_ON = {"department": "on", "write": "on", "money": "on"}
#: マニュアルの編集の権限が無いときの断りの文の頭
NO_MANUAL_WRITE = "マニュアルの編集の権限がありません"


@pytest.fixture
def on(settings):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture
def admin(db):
    return User.objects.create_superuser("admin", password=PASSWORD)


def account(login_id, *, departments=(), permissions=(), **flags):
    """アカウントを発行し、控えの無い利用者の物を返す。"""
    accounts_services.issue_account(login_id, PASSWORD, list(departments), list(permissions), flags, None)
    return User.objects.get(username=login_id)


def access_records(caplog):
    return [record for record in caplog.records if record.name == "seizou.access"]


@pytest.fixture
def manual(admin, media):
    return manuals_services.create_manual(None, "段取り替えの手順", "steps", admin)


def _upload(client, manual):
    return client.post(
        reverse("manuals:manualimage_upload", args=[manual.pk]), {"file": png_upload()}, HTTP_HX_REQUEST="true"
    )


def _delete(client, image):
    return client.post(reverse("manuals:manualimage_delete", args=[image.pk]), HTTP_HX_REQUEST="true")


# ---------------------------------------------------------------- マニュアルの画像 (精査 A2 の B2)


def test_manual_images_refused_without_permission(on, client, manual):
    genba = account("genba")
    client.force_login(genba)

    added = _upload(client, manual)
    assert added.status_code == 200
    assert NO_MANUAL_WRITE in added.content.decode()
    assert ManualImage.objects.count() == 0

    image = manuals_services.add_image(manual, png_upload(), None)
    removed = _delete(client, image)
    assert removed.status_code == 200
    assert NO_MANUAL_WRITE in removed.content.decode()
    assert ManualImage.objects.filter(pk=image.pk).exists()

    writer = account("tejun", permissions=["manuals"])
    client.force_login(writer)
    assert _upload(client, manual).status_code == 200
    assert ManualImage.objects.count() == 2
    deleted = _delete(client, image)
    assert deleted.status_code == 200
    assert NO_MANUAL_WRITE not in deleted.content.decode()
    assert not ManualImage.objects.filter(pk=image.pk).exists()


def test_manual_images_log_mode_records(client, manual, caplog):
    caplog.set_level(logging.WARNING, logger="seizou.access")
    genba = account("genba")
    client.force_login(genba)

    assert _upload(client, manual).status_code == 200

    assert ManualImage.objects.count() == 1
    messages = [record.getMessage() for record in access_records(caplog)]
    assert sum("manuals.add_image を保存しようとした" in message for message in messages) == 1, messages


# ---------------------------------------------------------------- 設定の画面の権限の判定 (精査 A2 の S5)


def test_access_control_edit_is_superuser_only(client, admin):
    # システム管理者が先に画面の値を置く (「答えの値に戻す」が消せる行がある形で見る)。
    core_services.set_setting("access_control", {"department": "log", "write": "log", "money": "log"}, admin)
    before = (conf.setting("access_control"), SettingOverride.objects.count())
    a2_manager = account("a2_manager", manage_accounts=True)
    client.force_login(a2_manager)

    for response in (
        client.get(edit_url("access_control")),
        client.post(edit_url("access_control"), {"department": "on", "write": "on", "money": "off"}),
        client.post(clear_url("access_control")),
    ):
        assert response.status_code == 403
        body = response.content.decode()
        assert "この画面は開けません" in body
        assert "システム管理者だけ" in body
        assert "no-store" in response["Cache-Control"]
    assert (conf.setting("access_control"), SettingOverride.objects.count()) == before

    page = client.get(reverse("core:settings")).content.decode()
    assert edit_url("over_shipment") in page
    assert edit_url("access_control") not in page
    # ほかの鍵は今までどおりアカウント管理の権限で変えられる。
    changed = client.post(edit_url("over_shipment"), {"over_shipment": "stop"})
    assert changed.status_code == 302
    assert conf.setting("over_shipment") == "stop"

    client.force_login(admin)
    assert client.get(edit_url("access_control")).status_code == 200
    assert edit_url("access_control") in client.get(reverse("core:settings")).content.decode()


# ---------------------------------------------------------------- 403 の画面の名前 (精査 A2 の S6)


def test_denied_page_names_screen_and_part(db, on, client):
    keiri_dept = accounts_services.create_department("経理", ["billing"], [], None)
    keiri = account("keiri", departments=[keiri_dept], see_money=True)
    client.force_login(keiri)

    expected = {
        "connect:inbox_list": "「届いた注文」の画面です (「受注管理」の部品)。あなたの部署: 経理。",
        "purchasing:receipt_list": "「受入」の画面です (「発注」の部品)。あなたの部署: 経理。",
        # 画面の名前と部品の名前が同じときは括弧を付けない (今の字のまま)。
        "orders:order_list": "「受注」の画面です。あなたの部署: 経理。",
    }
    for name, text in expected.items():
        response = client.get(reverse(name))
        assert response.status_code == 403, name
        assert text in response.content.decode(), name
    assert client.get(reverse("billing:invoice_list")).status_code == 200


# ---------------------------------------------------------------- 記録だけの金額 (精査 A2 の B4)


@pytest.fixture
def order(admin):
    """作り物の受注 1 件 (単価 1,500 × 10)。"""
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, admin)
    shaft = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, admin)
    return orders_services.create_order(
        customer,
        [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("1500")}],
        admin,
        order_date=date(2026, 11, 2),
    )


def _money_records(caplog):
    return [record.getMessage() for record in access_records(caplog) if "(金額の閲覧)" in record.getMessage()]


def test_hide_money_records_once_in_log_mode(settings, client, admin, order, caplog):
    caplog.set_level(logging.WARNING, logger="seizou.access")
    genba = account("genba")
    detail_url = reverse("orders:order_detail", args=[order.pk])

    def open_detail(person):
        client.force_login(person)
        caplog.clear()
        response = client.get(detail_url)
        assert response.status_code == 200
        return response, response.content.decode()

    # 既定 (log): 見せて、記録に 1 行だけ (印を何度読んでも 1 行)。
    response, html = open_detail(genba)
    assert "1,500" in html
    assert _money_records(caplog) == ["記録だけ (金額の閲覧): genba が 単価と金額を見た (金額の閲覧)"]
    assert bool(response.context["seizou"]["hide_money"]) is False
    # 金額の列の無い一覧は印を読まないので、金額の閲覧の記録は増えない。
    caplog.clear()
    assert client.get(reverse("masters:process_list")).status_code == 200
    assert _money_records(caplog) == []

    settings.SEIZOU = {"access_control": dict(ALL_ON)}
    response, html = open_detail(genba)
    assert "1,500" not in html
    assert _money_records(caplog) == []
    assert bool(response.context["seizou"]["hide_money"]) is True
    response, html = open_detail(admin)
    assert "1,500" in html
    assert bool(response.context["seizou"]["hide_money"]) is False

    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "off"}}
    response, html = open_detail(genba)
    assert "1,500" in html
    assert _money_records(caplog) == []
    assert bool(response.context["seizou"]["hide_money"]) is False


# ---------------------------------------------------------------- 部署の読みの控え (N91)


def test_access_rows_are_read_once_per_user_object(db, on, django_assert_num_queries):
    seizo_dept = accounts_services.create_department("製造", ["workorders", "progress", "masters"], [], None)
    genba = account("genba", departments=[seizo_dept])

    # 1 回目は数えない (設定の控えと権限の行を読む。設定の控えの冷え方に数が左右されないように)。
    assert access.can_open(genba, "billing", record=False) is False
    # 同じ利用者の物では、判定を何度呼んでも DB を読まない (利用者の物の控えを読む)。
    with django_assert_num_queries(0):
        assert access.can_open(genba, "workorders", record=False) is True
        assert access.can_open(genba, "orders", record=False) is False
        assert [d.name for d in access.departments_of(genba)] == ["製造"]
        assert access.is_unrestricted(genba) is False

    # 表から取り直した利用者の物は読み直す (権限の行と部署の prefetch の 2 回。設定の控えは温かい)。
    again = User.objects.get(pk=genba.pk)
    with django_assert_num_queries(2):
        assert access.can_open(again, "billing", record=False) is False


# ---------------------------------------------------------------- ブックマーク (精査 A2 の S3)


def test_bookmarks_hide_screens_outside_department(db, on, settings, caplog):
    caplog.set_level(logging.WARNING, logger="seizou.access")
    seizo_dept = accounts_services.create_department("製造", ["workorders", "progress", "masters"], [], None)
    genba = account("genba", departments=[seizo_dept])
    # 部署で開けない画面も登録は断らない (並びに出さないだけ)。
    bookmarks_services.add_bookmark(genba, "billing:invoice_list")
    bookmarks_services.add_bookmark(genba, "workorders:workorder_list")

    def listed(person):
        return [item["url_name"] for item in bookmarks_services.bookmarks_for(person)]

    def home(person):
        request = RequestFactory().get("/")
        request.user = person
        return [item["url_name"] for item in bookmarks_tags.bookmark_home({"request": request})]

    both = ["billing:invoice_list", "workorders:workorder_list"]
    assert listed(genba) == ["workorders:workorder_list"]
    assert Bookmark.objects.filter(user=genba).count() == 2
    assert home(genba) == ["workorders:workorder_list"]

    # 既定 (log) では 2 件とも並び、記録は書かない (左のメニューと同じ)。
    settings.SEIZOU = {}
    genba = User.objects.get(pk=genba.pk)
    assert listed(genba) == both
    assert access_records(caplog) == []

    # on に戻し、部署の開ける部品に請求を足すと 2 件 (登録の行が残っているので、また出る)。
    settings.SEIZOU = {"access_control": dict(ALL_ON)}
    accounts_services.update_department(seizo_dept, {"apps": ["workorders", "progress", "masters", "billing"]}, None)
    genba = User.objects.get(pk=genba.pk)
    assert listed(genba) == both
    assert home(genba) == both
