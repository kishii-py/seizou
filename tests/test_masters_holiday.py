"""会社の休日の services (masters.services の create_holiday・delete_holiday)。

日付は一意で、同じ日付・知らない種類・空の名前・知らない鍵は日本語の ValueError。作り物のデータだけを使う。
"""

from datetime import date

import pytest
from django.contrib.auth import get_user_model

from seizou.masters import services
from seizou.masters.models import Holiday

LABOR_DAY = date(2026, 11, 23)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


def _data(**values):
    return {"date": LABOR_DAY, "name": "勤労感謝の日", "kind": Holiday.KIND_PUBLIC, **values}


def test_create_holiday_writes_the_user(user):
    row = services.create_holiday(_data(), user)

    assert Holiday.objects.get() == row
    assert row.created_by == user
    assert row.kind == Holiday.KIND_PUBLIC and row.get_kind_display() == "祝日"
    assert str(row) == "2026-11-23 勤労感謝の日"


def test_create_holiday_refuses_the_same_date(user):
    services.create_holiday(_data(), user)

    with pytest.raises(ValueError) as error:
        services.create_holiday(_data(name="作り物の会社の休み", kind=Holiday.KIND_COMPANY), user)

    assert str(error.value) == "2026-11-23 の休日は既にあります (勤労感謝の日)。"
    assert Holiday.objects.count() == 1


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"kind": "臨時"}, "休日の種類は 祝日 か 会社の休み です。"),
        ({"kind": ""}, "休日の種類は 祝日 か 会社の休み です。"),
        ({"name": "  "}, "休日の名前を入れてください。"),
        ({"name": ""}, "休日の名前を入れてください。"),
    ],
)
def test_create_holiday_refuses_unknown_kind_and_blank_name(user, values, message):
    with pytest.raises(ValueError) as error:
        services.create_holiday(_data(**values), user)

    assert str(error.value) == message
    assert not Holiday.objects.exists()


def test_create_holiday_refuses_unknown_keys(user):
    with pytest.raises(ValueError) as error:
        services.create_holiday(_data(created_by=user), user)

    assert "created_by" in str(error.value) and "休日" in str(error.value)
    assert not Holiday.objects.exists()


def test_delete_holiday_twice_is_refused(user):
    row = services.create_holiday(_data(), user)

    assert services.delete_holiday(row, user) is None
    assert not Holiday.objects.exists()
    with pytest.raises(ValueError) as error:
        services.delete_holiday(row, user)
    assert str(error.value) == "この休日は既に消してあります。"
