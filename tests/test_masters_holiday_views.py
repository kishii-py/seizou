"""会社の休日の画面 (masters の HolidayListView・HolidayCreateView・HolidayDetailView・HolidayDeleteView)。

一覧 (日付の順・年で絞る)・新規登録 (services を通り、同じ日付は赤の知らせ)・詳細の「削除」・削除の確かめと、左のメニューの「休日」を見る。
休日は services で作る。作り物のデータだけを使う。
"""

from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services
from seizou.masters.models import Holiday


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def _holiday(user, day, name, kind=Holiday.KIND_PUBLIC):
    return services.create_holiday({"date": day, "name": name, "kind": kind}, user)


def _names(response):
    return [row["obj"].name for row in response.context["rows"]]


def test_list_is_in_date_order_and_filters_by_year(user, logged_in):
    _holiday(user, date(2027, 1, 1), "作り物の元日")
    _holiday(user, date(2026, 11, 23), "勤労感謝の日")
    _holiday(user, date(2026, 8, 13), "作り物の夏の休み", Holiday.KIND_COMPANY)
    url = reverse("masters:holiday_list")

    response = logged_in.get(url)
    assert response.status_code == 200
    assert _names(response) == ["作り物の夏の休み", "勤労感謝の日", "作り物の元日"]
    body = response.content.decode()
    assert "会社の休み" in body and "祝日" in body
    this_year = timezone.localdate().year
    for year in (this_year - 1, this_year, this_year + 1):
        assert f'href="{url}?year={year}"' in body, year
    assert '<span class="btn btn-sm btn-active pointer-events-none" aria-current="true">すべて</span>' in body

    response = logged_in.get(url, {"year": "2026"})
    assert response.status_code == 200
    assert _names(response) == ["作り物の夏の休み", "勤労感謝の日"]
    current = logged_in.get(url, {"year": str(this_year)}).content.decode()
    assert f'<span class="btn btn-sm btn-active pointer-events-none" aria-current="true">{this_year}</span>' in current
    assert f'href="{url}"' in current  # 「すべて」は押せる link に戻る

    for bad in ("abc", "20266", "２０２６", ""):
        response = logged_in.get(url, {"year": bad})
        assert response.status_code == 200, bad
        assert len(_names(response)) == 3, bad


def test_create_goes_through_service_and_refuses_the_same_date(user, logged_in):
    url = reverse("masters:holiday_create")
    data = {"date": "2026-11-27", "name": "作り物の会社の休み", "kind": Holiday.KIND_COMPANY}

    response = logged_in.post(url, data)
    row = Holiday.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("masters:holiday_detail", args=[row.pk])
    assert row.date == date(2026, 11, 27) and row.created_by == user

    response = logged_in.post(url, {**data, "name": "別の名前"})
    assert response.status_code == 200
    body = response.content.decode()
    assert "2026-11-27 の休日は既にあります (作り物の会社の休み)。" in body
    assert Holiday.objects.count() == 1


def test_detail_shows_delete_and_delete_goes_back_to_list(user, logged_in):
    row = _holiday(user, date(2026, 11, 23), "勤労感謝の日")
    delete_url = reverse("masters:holiday_delete", args=[row.pk])

    response = logged_in.get(reverse("masters:holiday_detail", args=[row.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert f'href="{delete_url}"' in body
    assert response.context["edit_url"] is None  # 編集の画面は無い

    response = logged_in.get(delete_url)
    assert response.status_code == 200
    assert "2026-11-23 勤労感謝の日 を削除します。" in response.content.decode()

    response = logged_in.post(delete_url)
    assert response.status_code == 302
    assert response["Location"] == reverse("masters:holiday_list")
    assert not Holiday.objects.exists()
    assert logged_in.get(delete_url).status_code == 404


def test_menu_has_holiday_entry(logged_in):
    body = logged_in.get(reverse("core:home")).content.decode()

    assert "休日" in body
    assert f'href="{reverse("masters:holiday_list")}"' in body
