"""添付ファイルの一覧が空 (検索も絞りも無い) のとき、空の枠に「添付ファイルは、受注や部品などの詳細の「添付ファイル」の枠から付けます。」を出し、
ボタンは出さない (UX の見直しの案 list-09。attachments/_rows.html)。
検索で 0 件のときと行があるときは core/_rows.html のまま。名前は作り物だけ。
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from seizou.attachments import services
from seizou.masters import services as masters_services

HX = {"HTTP_HX_REQUEST": "true"}
EMPTY_HINT = "添付ファイルは、受注や部品などの詳細の「添付ファイル」の枠から付けます。"


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def test_empty_list_says_where_to_attach(client_logged_in):
    body = client_logged_in.get(reverse("attachments:attachment_list")).content.decode()
    assert EMPTY_HINT in body
    assert "まだ 1 件もありません。" in body
    assert "新規登録" not in body
    assert 'class="btn btn-sm btn-outline mt-4' not in body


def test_htmx_empty_returns_attachments_frame(client_logged_in):
    body = client_logged_in.get(reverse("attachments:attachment_list"), **HX).content.decode()
    assert "<html" not in body
    assert EMPTY_HINT in body
    assert 'id="list-meta"' in body and 'hx-swap-oob="true"' in body


def test_search_miss_keeps_core_message(client_logged_in):
    body = client_logged_in.get(reverse("attachments:attachment_list"), {"q": "作り物の無い字"}).content.decode()
    assert "「作り物の無い字」に合う物はありません。" in body
    assert EMPTY_HINT not in body


def test_list_with_rows_keeps_core_table(user, client_logged_in):
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    services.add_attachment(item, SimpleUploadedFile("作り物の写真.png", b"png"), user, kind="photo")

    body = client_logged_in.get(reverse("attachments:attachment_list")).content.decode()
    assert "作り物の写真.png" in body
    assert EMPTY_HINT not in body
