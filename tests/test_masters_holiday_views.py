"""会社の休日の画面 (masters の HolidayListView・HolidayCreateView・HolidayDetailView・HolidayDeleteView)。

一覧 (日付の順・年で絞る)・新規登録 (services を通り、同じ日付は赤の知らせ)・詳細の「削除」・削除の確かめと、左のメニューの「休日」を見る。
休日は services で作る。作り物のデータだけを使う。
"""

from datetime import date
from urllib.parse import parse_qs

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


def _tab_params(tab):
    """絞りのタブの href の GET の値 (名前ごとに 1 つ)。"""
    return {key: values[0] for key, values in parse_qs(tab["href"].lstrip("?")).items()}


def test_list_is_in_date_order_and_filters_by_year(user, logged_in):
    """年の絞りは、カードの中の絞りのタブ (すべて・前の年・今年・次の年) と「絞り込み」の枠の「日付」(N266)。

    年は今日の年から作る (年が替わっても落ちない)。題の行に年の組は無く、?year= は読まない。
    """
    this_year = timezone.localdate().year
    _holiday(user, date(this_year + 1, 1, 1), "作り物の元日")
    _holiday(user, date(this_year, 11, 23), "勤労感謝の日")
    _holiday(user, date(this_year, 8, 13), "作り物の夏の休み", Holiday.KIND_COMPANY)
    url = reverse("masters:holiday_list")

    response = logged_in.get(url)
    assert response.status_code == 200
    assert _names(response) == ["作り物の夏の休み", "勤労感謝の日", "作り物の元日"]
    body = response.content.decode()
    assert "会社の休み" in body and "祝日" in body
    assert 'aria-label="年で絞る"' not in body
    assert "?year=" not in body

    tabs = response.context["presets"]
    assert [tab["label"] for tab in tabs] == ["すべて", "前の年", "今年", "次の年"]
    assert [tab["selected"] for tab in tabs] == [True, False, False, False]
    assert [tab["count"] for tab in tabs] == [3, 0, 2, 1]
    for tab, year in zip(tabs[1:], (this_year - 1, this_year, this_year + 1)):
        assert _tab_params(tab) == {"f_date_from": f"{year}-01-01", "f_date_to": f"{year}-12-31"}, tab["label"]

    this_year_range = {"f_date_from": f"{this_year}-01-01", "f_date_to": f"{this_year}-12-31"}
    response = logged_in.get(url, this_year_range)
    assert response.status_code == 200
    assert _names(response) == ["作り物の夏の休み", "勤労感謝の日"]
    assert [tab["label"] for tab in response.context["presets"] if tab["selected"]] == ["今年"]
    # キーワードで探しても年の絞りは外れない (キーワードの欄と絞りの欄が同じ form。土台の get_queryset が検索の後に絞る)
    response = logged_in.get(url, {**this_year_range, "q": "作り物"})
    assert _names(response) == ["作り物の夏の休み"]

    # 読めない値は絞らず全部を出す (土台の決まり)。全角の数字は、読めたら 0 件になる前の年の終わりの日で見る
    last_year_wide = f"{this_year - 1}-01-01".translate(str.maketrans("0123456789", "０１２３４５６７８９"))
    for key in ("f_date_from", "f_date_to"):
        for bad in ("abc", "20266-01-01", last_year_wide, ""):
            response = logged_in.get(url, {key: bad})
            assert response.status_code == 200, (key, bad)
            assert len(_names(response)) == 3, (key, bad)
    # 前の形の ?year= は読まない (止めずに全部を出す)
    response = logged_in.get(url, {"year": str(this_year)})
    assert response.status_code == 200
    assert len(_names(response)) == 3


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
