"""業務マニュアルのフォルダとマニュアルの管理 (名前と色の変更・移動・保護・削除)。"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.manuals import services
from seizou.manuals.models import Manual, ManualFolder

COLOR_MARKS = {
    "gray": "status status-neutral",
    "red": "status status-error",
    "yellow": "status status-warning",
    "green": "status status-success",
    "blue": "status status-info",
    "pink": "status status-secondary",
    "teal": "status status-accent",
}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


def list_html(client, folder=None) -> str:
    params = {"folder": folder.pk} if folder is not None else {}
    return client.get(reverse("manuals:manual_list"), params).content.decode()


@pytest.mark.parametrize("color", list(COLOR_MARKS))
def test_folder_rename_and_recolor(client_logged_in, user, color):
    folder = services.create_folder(None, "古い名前", "gray", user)
    url = reverse("manuals:manualfolder_update", args=[folder.pk])
    page = client_logged_in.get(url).content.decode()
    assert 'value="古い名前"' in page
    response = client_logged_in.post(url, {"name": "新しい名前", "color": color})
    assert response.status_code == 302
    assert response["Location"] == reverse("manuals:manualfolder_detail", args=[folder.pk])
    folder.refresh_from_db()
    assert (folder.name, folder.color) == ("新しい名前", color)
    html = list_html(client_logged_in)
    assert "新しい名前" in html
    assert f'class="{COLOR_MARKS[color]}"' in html


def test_folder_move_choices(client_logged_in, user):
    top = services.create_folder(None, "上", "gray", user)
    child = services.create_folder(top, "子", "gray", user)
    other = services.create_folder(None, "ほか", "gray", user)
    url = reverse("manuals:manualfolder_move", args=[top.pk])
    html = client_logged_in.get(url).content.decode()
    assert '<option value="" selected>ルート</option>' in html
    assert f'<option value="{other.pk}">ほか</option>' in html
    assert f'value="{top.pk}"' not in html and f'value="{child.pk}"' not in html
    refused = client_logged_in.post(url, {"parent": child.pk})
    assert refused.status_code == 200
    top.refresh_from_db()
    assert top.parent_id is None
    response = client_logged_in.post(url, {"parent": other.pk})
    assert response.status_code == 302
    top.refresh_from_db()
    assert top.parent == other
    client_logged_in.post(url, {"parent": ""})
    top.refresh_from_db()
    assert top.parent_id is None


def test_protect_hides_actions_and_refuses(client_logged_in, user):
    folder = services.create_folder(None, "保護する置き場", "gray", user)
    other = services.create_folder(None, "ほか", "gray", user)
    before = list_html(client_logged_in, folder)
    assert reverse("manuals:manualfolder_delete", args=[folder.pk]) in before
    protect_url = reverse("manuals:manualfolder_protect", args=[folder.pk])
    page = client_logged_in.get(protect_url).content.decode()
    assert "保護すると移動と削除ができなくなります" in page
    assert client_logged_in.post(protect_url, {"on": "True"}).status_code == 302
    folder.refresh_from_db()
    assert folder.protected
    html = list_html(client_logged_in, folder)
    assert "保護を解除" in html and "保護中" in html
    for name in ("manualfolder_update", "manualfolder_move", "manualfolder_delete"):
        assert reverse(f"manuals:{name}", args=[folder.pk]) not in html
    moved = client_logged_in.post(reverse("manuals:manualfolder_move", args=[folder.pk]), {"parent": other.pk})
    assert moved.status_code == 200
    assert "保護されたフォルダです" in moved.content.decode()
    deleted = client_logged_in.post(reverse("manuals:manualfolder_delete", args=[folder.pk]), {})
    assert deleted.status_code == 200
    assert "保護されたフォルダです" in deleted.content.decode()
    assert ManualFolder.objects.filter(pk=folder.pk, parent__isnull=True).exists()
    # マニュアルも同じ。保護中は「編集」と「版の履歴」を出し、「移動」と「削除」を出さない。
    manual = services.create_manual(None, "保護する手順", "steps", user)
    client_logged_in.post(reverse("manuals:manual_protect", args=[manual.pk]), {"on": "True"})
    detail = client_logged_in.get(reverse("manuals:manual_detail", args=[manual.pk])).content.decode()
    assert "保護解除" in detail
    assert reverse("manuals:manual_update", args=[manual.pk]) in detail
    assert reverse("manuals:manualversion_list", args=[manual.pk]) in detail
    assert reverse("manuals:manual_move", args=[manual.pk]) not in detail
    assert reverse("manuals:manual_delete", args=[manual.pk]) not in detail
    refused = client_logged_in.post(reverse("manuals:manual_delete", args=[manual.pk]), {})
    assert refused.status_code == 200 and "保護されたマニュアルです" in refused.content.decode()
    # 保護を解除する (隠した欄は今の逆)。
    client_logged_in.post(protect_url, {"on": "False"})
    folder.refresh_from_db()
    assert not folder.protected


def test_folder_delete_empty_and_with_contents(client_logged_in, user):
    top = services.create_folder(None, "上", "gray", user)
    empty = services.create_folder(top, "空", "gray", user)
    response = client_logged_in.post(reverse("manuals:manualfolder_delete", args=[empty.pk]), {})
    assert response.status_code == 302
    assert response["Location"] == reverse("manuals:manualfolder_detail", args=[top.pk])
    assert not ManualFolder.objects.filter(pk=empty.pk).exists()
    full = services.create_folder(top, "中身あり", "gray", user)
    services.create_manual(full, "中の手順", "steps", user)
    url = reverse("manuals:manualfolder_delete", args=[full.pk])
    page = client_logged_in.get(url).content.decode()
    assert "中身も削除する" in page and "btn btn-error" in page
    refused = client_logged_in.post(url, {})
    assert refused.status_code == 200
    assert "フォルダが空ではありません" in refused.content.decode()
    response = client_logged_in.post(url, {"with_contents": "on"})
    assert response["Location"] == reverse("manuals:manualfolder_detail", args=[top.pk])
    assert not ManualFolder.objects.filter(pk=full.pk).exists()
    assert not Manual.objects.filter(title="中の手順").exists()
    response = client_logged_in.post(reverse("manuals:manualfolder_delete", args=[top.pk]), {})
    assert response["Location"] == reverse("manuals:manualfolder_list")


RED_MARK = "size-5 text-error shrink-0"
GRAY_MARK = "size-5 text-base-content/70 shrink-0"


def test_folder_delete_confirm_counts_and_protected(client_logged_in, user):
    """フォルダの削除の確かめは中の数を出し、空なら欄を外し、中に保護された物があれば押す前に黄で断る (C73)。"""
    empty = services.create_folder(None, "空", "gray", user)
    page = client_logged_in.get(reverse("manuals:manualfolder_delete", args=[empty.pk])).content.decode()
    assert "フォルダ「空」を削除します。中は空です。元に戻せません。" in page
    assert 'name="with_contents"' not in page
    assert "btn btn-error" in page
    assert RED_MARK in page
    # 子のフォルダ 1 つと、自分と子にマニュアルを 1 件ずつ。
    full = services.create_folder(None, "中身あり", "gray", user)
    child = services.create_folder(full, "子", "gray", user)
    services.create_manual(full, "上の手順", "steps", user)
    services.create_manual(child, "子の手順", "steps", user)
    page = client_logged_in.get(reverse("manuals:manualfolder_delete", args=[full.pk])).content.decode()
    assert "中にフォルダ 1 個とマニュアル 2 件があります。消すときは「中身も削除する」に印を付けます。" in page
    assert 'name="with_contents"' in page
    # マニュアルだけ 1 件。
    only = services.create_folder(None, "手順だけ", "gray", user)
    manual = services.create_manual(only, "段取りの手順", "steps", user)
    url = reverse("manuals:manualfolder_delete", args=[only.pk])
    page = client_logged_in.get(url).content.decode()
    assert "中にマニュアル 1 件があります。" in page
    assert "alert-warning" not in page
    # 中のマニュアルを保護すると、form を出さずに黄で断り、「キャンセル」はフォルダを開く口へ戻る。
    services.protect_manual(manual, True, user)
    page = client_logged_in.get(url).content.decode()
    assert "alert-warning" in page
    assert "削除できません。" in page
    assert "中に保護されたフォルダかマニュアルがあります。先に保護を解除してください。" in page
    assert "data-enter-guard" not in page
    assert "btn btn-error" not in page
    assert 'name="with_contents"' not in page
    folder_url = reverse("manuals:manualfolder_detail", args=[only.pk])
    assert f'class="btn btn-outline border-base-300" href="{folder_url}"' in page
    # 同じ状態で送っても services が断り、赤の知らせの字と黄の断りの両方が出る (赤の場所は見ない)。
    refused = client_logged_in.post(url, {"with_contents": "on"})
    assert refused.status_code == 200
    body = refused.content.decode()
    assert "中に保護されたフォルダかマニュアルがあります" in body
    assert "alert-warning" in body
    assert ManualFolder.objects.filter(pk=only.pk).exists()
    assert Manual.objects.filter(pk=manual.pk).exists()
    # 頭の印: 削除は赤の三角、保護は灰色の (i)。
    loose = services.create_manual(None, "消す手順", "steps", user)
    page = client_logged_in.get(reverse("manuals:manual_delete", args=[loose.pk])).content.decode()
    assert RED_MARK in page
    page = client_logged_in.get(reverse("manuals:manualfolder_protect", args=[empty.pk])).content.decode()
    assert GRAY_MARK in page
    assert RED_MARK not in page


def test_manual_move_protect_delete(client_logged_in, user):
    folder = services.create_folder(None, "置き場", "gray", user)
    manual = services.create_manual(None, "動かす手順", "steps", user)
    url = reverse("manuals:manual_move", args=[manual.pk])
    assert client_logged_in.get(url).status_code == 200
    response = client_logged_in.post(url, {"folder": folder.pk})
    assert response["Location"] == reverse("manuals:manual_detail", args=[manual.pk])
    manual.refresh_from_db()
    assert manual.folder == folder
    response = client_logged_in.post(reverse("manuals:manual_protect", args=[manual.pk]), {"on": "True"})
    assert response["Location"] == reverse("manuals:manual_detail", args=[manual.pk])
    client_logged_in.post(reverse("manuals:manual_protect", args=[manual.pk]), {"on": "False"})
    delete_url = reverse("manuals:manual_delete", args=[manual.pk])
    page = client_logged_in.get(delete_url).content.decode()
    assert "「動かす手順」を削除します。版と画像も削除し、元に戻せません。" in page
    response = client_logged_in.post(delete_url, {})
    assert response["Location"] == reverse("manuals:manualfolder_detail", args=[folder.pk])
    assert not Manual.objects.filter(pk=manual.pk).exists()
    root_manual = services.create_manual(None, "ルートの手順", "steps", user)
    response = client_logged_in.post(reverse("manuals:manual_delete", args=[root_manual.pk]), {})
    assert response["Location"] == reverse("manuals:manual_list")
