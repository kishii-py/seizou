"""精査 A2 の権限の判定の直し (マニュアルの画像・設定の画面の権限の判定・403 の画面の名前・記録だけの金額) を見る。

人と部署は accounts の services で作る。名前は作り物 (genba = 権限なし・金額なし、keiri = 経理・金額あり、
a2_manager = アカウント管理だけ)。判定を有効にする試験は settings の fixture で SEIZOU を替える
(tests/settings.py は SEIZOU = {} のまま = 3 つとも記録だけ)。Django の test client で HTML と表の状態を見る。
"""

import logging

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.core import conf
from seizou.core import services as core_services
from seizou.core.models import SettingOverride
from seizou.manuals import services as manuals_services
from seizou.manuals.models import ManualImage
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
