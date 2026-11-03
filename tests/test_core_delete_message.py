"""削除の後の知らせ (N77 の 7): 入力の土台が、service の名前が delete_ か remove_ で始まる画面では「削除しました。」を出す。

部署と添付の削除は Django の test client で移った先の HTML を見る (形は tests/test_accounts_departments.py と
tests/test_attachments_views.py を写した)。土台の分かれ目は、試験の中で BaseFormView を継いだ画面を RequestFactory で
直に呼んで見る。名前は作り物だけ。
"""

from decimal import Decimal

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.messages.storage.cookie import CookieStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.accounts.models import Department
from seizou.attachments import services as attachments_services
from seizou.attachments.models import Attachment
from seizou.core.views import BaseFormView
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services

User = get_user_model()

PASSWORD = "kumo-tests-2611"
DELETED = "削除しました。"
SAVED = "保存しました。"


@pytest.fixture
def on(settings):
    settings.SEIZOU = {"access_control": {"department": "on", "write": "on", "money": "on"}}


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture
def manager(db):
    """アカウント管理の担当者。"""
    accounts_services.issue_account("soumu", PASSWORD, [], [], {"manage_accounts": True}, None)
    return User.objects.get(username="soumu")


@pytest.fixture
def user(db):
    return User.objects.create_user("tester", password=PASSWORD)


def page_after(client, response) -> str:
    assert response.status_code == 302
    return client.get(response["Location"]).content.decode()


# ---------------------------------------------------------------- 画面


@pytest.mark.django_db
def test_department_delete_says_deleted_and_create_says_saved(on, client, manager):
    client.force_login(manager)

    created = client.post(
        reverse("accounts:department_create"),
        {"name": "作り物の部署", "apps": ["orders"], "default_permissions": [], "sort_order": "1"},
    )
    html = page_after(client, created)
    assert SAVED in html and DELETED not in html

    dept = Department.objects.get(name="作り物の部署")
    deleted = client.post(reverse("accounts:department_delete", args=[dept.pk]))
    html = page_after(client, deleted)
    assert not Department.objects.filter(pk=dept.pk).exists()
    assert DELETED in html and SAVED not in html


@pytest.mark.django_db
def test_attachment_delete_says_deleted(client, user):
    client.force_login(user)
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("1"), "unit_price": Decimal("100")}], user, order_date="2026-11-04"
    )
    attachment = attachments_services.add_attachment(order, SimpleUploadedFile("作り物の写真.png", b"png"), user, kind="photo")

    deleted = client.post(reverse("attachments:attachment_delete", args=[attachment.pk]))
    html = page_after(client, deleted)
    assert Attachment.objects.count() == 0
    assert DELETED in html and SAVED not in html


# ---------------------------------------------------------------- 土台の分かれ目


def remove_x(user, data):
    """作り物の削除の service (名前が remove_ で始まる)。"""
    return None


def delete_x(user, data):
    """作り物の削除の service (名前が delete_ で始まる)。"""
    return None


def make_x(user, data):
    """作り物の登録の service。"""
    return None


def cancel_x(user, data):
    """作り物の取消の service (取消は「保存しました。」のまま)。"""
    return None


class _EmptyForm(forms.Form):
    pass


def _view(service):
    return type(
        "_TrialFormView",
        (BaseFormView,),
        {"form_class": _EmptyForm, "service": staticmethod(service), "success_url": "/", "screen_name": "作り物の入力"},
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("service", "expected"),
    [(remove_x, DELETED), (delete_x, DELETED), (make_x, SAVED), (cancel_x, SAVED)],
)
def test_base_form_view_message_follows_service_name(user, service, expected):
    request = RequestFactory().post("/", {})
    request.user = user
    request._messages = CookieStorage(request)

    response = _view(service).as_view()(request)

    assert response.status_code == 302
    assert [str(message) for message in get_messages(request)] == [expected]
    assert BaseFormView.DELETE_SERVICE_PREFIXES == ("delete_", "remove_")
