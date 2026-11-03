"""工程を作る関数 (masters.services.create_process)。"""

import pytest
from django.contrib.auth import get_user_model

from seizou.masters import services
from seizou.masters.models import Process


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.mark.django_db
def test_create_process(user):
    process = services.create_process(
        {"code": "P10", "name": "旋削", "is_outsourced": False, "sort_order": 10}, user
    )

    assert isinstance(process, Process)
    assert process.pk
    assert (process.code, process.name, process.is_outsourced, process.sort_order) == (
        "P10",
        "旋削",
        False,
        10,
    )
    outsourced = services.create_process({"code": "P90", "name": "協力企業の仕上げ", "is_outsourced": True}, user)
    assert outsourced.is_outsourced is True
    assert outsourced.sort_order == 0


@pytest.mark.django_db
def test_duplicate_code_is_refused(user):
    services.create_process({"code": "P10", "name": "旋削"}, user)

    with pytest.raises(ValueError, match="工程コード P10 は既にあります"):
        services.create_process({"code": "P10", "name": "別の名前"}, user)
    assert Process.objects.count() == 1


@pytest.mark.django_db
def test_unknown_key_is_refused(user):
    with pytest.raises(ValueError, match="知らない項目 \\(color\\)"):
        services.create_process({"code": "P10", "name": "旋削", "color": "赤"}, user)
    assert Process.objects.count() == 0
