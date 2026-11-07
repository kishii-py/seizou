"""入力の土台は、ログインと部署の門を通ってから URL の pk の記録を引く (Claude Security の走査の候補 11-07 = 記録の有無の手がかり)。

ログインの前に記録を引くと、ある pk はログインへ 302、無い pk は 404 になり、ログインしていない人にも記録の有無と数が分かる。
部署の外の人も、ある pk は 403、無い pk は 404 で分かる。門を先にすると、どちらも同じ答え (302 か 403) になる。
人と部署は accounts の services で作る。名前と品番は作り物。
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.masters import services as masters_services

User = get_user_model()

PASSWORD = "kumo-tests-2611"
ALL_ON = {"department": "on", "write": "on", "money": "on"}


@pytest.fixture
def admin(db):
    return User.objects.create_superuser("admin", password=PASSWORD)


@pytest.fixture
def shaft(admin):
    return masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個", "standard_price": Decimal("1234")}, admin
    )


def account(login_id, *, departments=(), permissions=(), **flags):
    accounts_services.issue_account(login_id, PASSWORD, list(departments), list(permissions), flags, None)
    return User.objects.get(username=login_id)


def _edit(pk):
    return reverse("masters:item_update", args=[pk])


def test_anonymous_gets_login_for_existing_and_missing_pk(client, shaft):
    missing = shaft.pk + 1000

    for pk in (shaft.pk, missing):
        response = client.get(_edit(pk))
        assert response.status_code == 302, pk
        assert response["Location"].startswith(reverse("core:login"))
        # POST も同じ (保存の口からも有無が分からない)
        assert client.post(_edit(pk), {}).status_code == 302, pk


def test_department_outsider_gets_403_for_existing_and_missing_pk(settings, client, shaft):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}
    sales = accounts_services.create_department("受注と出荷", ["orders", "shipping"], ["orders"], None)
    jimu = account("jimu", departments=[sales], permissions=["master_edit"])
    client.force_login(jimu)

    assert client.get(_edit(shaft.pk)).status_code == 403
    assert client.get(_edit(shaft.pk + 1000)).status_code == 403


def test_insider_still_gets_404_for_missing_pk_and_form_for_existing(settings, client, shaft):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}
    seizo = accounts_services.create_department("製造", ["workorders", "masters"], ["workorders"], None)
    genba = account("genba", departments=[seizo], permissions=["master_edit"])
    client.force_login(genba)

    assert client.get(_edit(shaft.pk + 1000)).status_code == 404
    page = client.get(_edit(shaft.pk))
    assert page.status_code == 200
    assert page.context["object"] == shaft
