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
    assert "保護されたフォルダは移動できません" in moved.content.decode()
    deleted = client_logged_in.post(reverse("manuals:manualfolder_delete", args=[folder.pk]), {})
    assert deleted.status_code == 200
    assert "保護されたフォルダは削除できません" in deleted.content.decode()
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
    assert refused.status_code == 200 and "保護されたマニュアルは削除できません" in refused.content.decode()
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
