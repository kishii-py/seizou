"""添付ファイルを、付けた先の記録を開ける人にだけ開かせる・消させる・一覧に出す (Claude Security の走査の候補 11-07 の 7・8・9)。

- フィードバックの画面の絵は、書いた本人と世話役 (アカウント管理の権限) だけ (feedback の一覧と詳細の絞りと同じ)。ほかの人には 404。
- 部署の判定が on の会社では、付けた先の部品を部署で開けない人には、開く・消す・一覧に出すをしない (404)。
作り物のデータだけ。storage は tmp_path の下。本物の API は呼ばない (フィードバックは services で作る)。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.attachments import services
from seizou.attachments.models import Attachment
from seizou.feedback import services as feedback_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services

PASSWORD = "kumo-tests-2611"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
PDF_BYTES = b"%PDF-1.4\n% made for tests\n"
SECRET_TITLE = "作り物の下書きの題-ひみつ"
SECRET_FILE = "himitsu-gamen.png"
#: 部署の判定だけを効かせる (編集の権限と金額の閲覧は見ない)
DEPARTMENT_ON = {"department": "on", "write": "off", "money": "off"}


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def _account(login_id, departments=(), **flags):
    accounts_services.issue_account(login_id, PASSWORD, list(departments), [], flags, None)
    return get_user_model().objects.get(username=login_id)


def _client(person) -> Client:
    client = Client()
    client.force_login(person)
    return client


@pytest.fixture
def author(db):
    return _account("kaku")


@pytest.fixture
def other(db):
    return _account("hoka")


@pytest.fixture
def manager(db):
    return _account("sewa", manage_accounts=True)


@pytest.fixture
def screenshot(author):
    """author の下書きのフィードバックに付けた画面の絵 (添付ファイルの行)。"""
    data = {
        "kind": "bug",
        "screen": "orders:order_list",
        "title": SECRET_TITLE,
        "problem": "作り物の困りごと。",
        "situation": "",
        "wish": "",
        "screens": "",
    }
    row = feedback_services.create_feedback(data, author, screenshots=[SimpleUploadedFile(SECRET_FILE, PNG_BYTES)])
    return Attachment.objects.get(object_id=row.pk, original_name=SECRET_FILE)


@pytest.fixture
def order_file(db):
    """受注に付けた PDF (付けた人は作り物の利用者)。"""
    maker = get_user_model().objects.create_user("tester", password="pass-for-tests-only")
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, maker)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, maker)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}], maker, order_date=date(2026, 11, 2)
    )
    return services.add_attachment(order, SimpleUploadedFile("juchu-shiyou.pdf", PDF_BYTES), maker, kind="other")


def _download(attachment):
    return reverse("attachments:attachment_download", args=[attachment.pk])


def _delete(attachment):
    return reverse("attachments:attachment_delete", args=[attachment.pk])


def _list_body(person) -> str:
    response = _client(person).get(reverse("attachments:attachment_list"))
    assert response.status_code == 200
    return response.content.decode()


# ---------------------------------------------------------------- 候補 7: 開く


@pytest.mark.django_db
def test_feedback_screenshot_opens_only_for_author_and_manager(screenshot, author, other, manager):
    assert _client(other).get(_download(screenshot)).status_code == 404
    for person in (author, manager):
        response = _client(person).get(_download(screenshot))
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == PNG_BYTES


# ---------------------------------------------------------------- 候補 8: 消す


@pytest.mark.django_db
def test_feedback_screenshot_cannot_be_deleted_by_others(screenshot, author, other):
    stranger = _client(other)
    assert stranger.get(_delete(screenshot)).status_code == 404
    assert stranger.post(_delete(screenshot)).status_code == 404
    assert Attachment.objects.filter(pk=screenshot.pk).exists()

    response = _client(author).post(_delete(screenshot))
    assert response.status_code == 302
    assert not Attachment.objects.filter(pk=screenshot.pk).exists()


@pytest.mark.django_db
def test_manager_can_delete_feedback_screenshot(screenshot, manager):
    assert _client(manager).get(_delete(screenshot)).status_code == 200
    assert _client(manager).post(_delete(screenshot)).status_code == 302
    assert not Attachment.objects.filter(pk=screenshot.pk).exists()


# ---------------------------------------------------------------- 候補 9: 一覧と移るだけの口


@pytest.mark.django_db
def test_list_shows_feedback_screenshot_only_to_author_and_manager(screenshot, author, other, manager):
    hidden = _list_body(other)
    assert SECRET_TITLE not in hidden
    assert SECRET_FILE not in hidden
    for person in (author, manager):
        shown = _list_body(person)
        assert SECRET_TITLE in shown and SECRET_FILE in shown

    detail = reverse("attachments:attachment_detail", args=[screenshot.pk])
    assert _client(other).get(detail).status_code == 404
    assert _client(author).get(detail).status_code == 302


# ---------------------------------------------------------------- 部署の判定 (on) の会社


@pytest.mark.django_db
def test_department_outsider_cannot_open_delete_or_list(settings, order_file):
    settings.SEIZOU = {"access_control": dict(DEPARTMENT_ON)}
    seizo = accounts_services.create_department("製造", ["workorders", "progress", "stock", "masters"], [], None)
    sales = accounts_services.create_department("受注と出荷", ["orders", "shipping", "masters"], [], None)
    outsider = _account("seizo-hito", departments=[seizo])
    insider = _account("eigyo-hito", departments=[sales])

    stranger = _client(outsider)
    assert stranger.get(_download(order_file)).status_code == 404
    assert stranger.get(_delete(order_file)).status_code == 404
    assert stranger.post(_delete(order_file)).status_code == 404
    assert "juchu-shiyou.pdf" not in _list_body(outsider)
    assert Attachment.objects.filter(pk=order_file.pk).exists()

    assert _client(insider).get(_download(order_file)).status_code == 200
    assert "juchu-shiyou.pdf" in _list_body(insider)


@pytest.mark.django_db
def test_department_off_keeps_files_open_to_everyone(order_file, other):
    """部署の判定が off (試験の既定) なら、今までどおりログインした人は誰でも受注の添付を開ける。"""
    assert _client(other).get(_download(order_file)).status_code == 200
    assert "juchu-shiyou.pdf" in _list_body(other)
