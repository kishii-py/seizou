"""社員の画面 (masters の StaffListView・StaffDetailView・StaffCreateView・StaffUpdateView・StaffDeleteView。2 段目 N401b)。

左のメニューの「社員」・新規登録 (services を通り、先頭の 0 の数が違う番号は赤の知らせ)・編集の「使用中」・新規登録に「使用中」が無い事・
工程の編集の権限の無い人にボタンを出さない事・5 枚のユースケース・削除の確かめの件数の文・削除の後の知らせと実績の写しを見る。
社員と実績は services で作る (工程の行だけは R07 の試験と同じく表に直に作る)。名前は作り物だけ。
"""

import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse

from seizou.core.models import ChangeLog
from seizou.core.screens import all_screens
from seizou.core.views import hidden_detail_fields
from seizou.masters import services
from seizou.masters.models import Process, Staff
from seizou.orders import services as orders_services
from seizou.progress import services as progress_services
from seizou.progress.models import Progress
from seizou.workorders import services as workorders_services
from tests.test_core_access import account

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
USECASE = re.compile(r"使う人: .+ / いつ: .+ / 何のために: .+ / 次にすること: .+")
STAFF_SCREENS = ("masters:staff_list", "masters:staff_detail", "masters:staff_create", "masters:staff_update", "masters:staff_delete")
DUPLICATE_12 = (
    "保存できません。社員番号 12 は 0012 作り物 一郎 と同じ番号です "
    "(先頭の 0 の数が違う番号も同じ番号と見ます)。別の番号にしてください。"
)
COPY_NOTE_2 = "この社員の実績 2 件には、名前と番号を写して残します。"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def ichiro(user):
    return services.create_staff({"code": "0012", "name": "作り物 一郎"}, user)


@pytest.fixture
def step(user):
    customer = services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    process = Process.objects.create(code="CUT", name="切断")
    workorder = workorders_services.issue_workorder(
        order.lines.get(), Decimal("10"), [{"process": process, "planned_qty": Decimal("10")}], user
    )
    return workorder.steps.get()


def _codes(response):
    return [row["obj"].code for row in response.context["rows"]]


# ---------------------------------------------------------------- (a) 入口と一覧


def test_menu_has_staff_entry_and_list_opens(logged_in, ichiro):
    body = logged_in.get(reverse("core:home")).content.decode()
    assert "社員" in body
    assert f'href="{reverse("masters:staff_list")}"' in body

    response = logged_in.get(reverse("masters:staff_list"))
    assert response.status_code == 200
    assert _codes(response) == ["0012"]
    body = response.content.decode()
    for label in ("社員番号", "名前", "備考"):
        assert label in body
    # 番号と名前の字で探せる
    assert _codes(logged_in.get(reverse("masters:staff_list"), {"q": "一郎"})) == ["0012"]
    assert _codes(logged_in.get(reverse("masters:staff_list"), {"q": "0012"})) == ["0012"]
    assert _codes(logged_in.get(reverse("masters:staff_list"), {"q": "作り物の二郎"})) == []


def test_menu_puts_staff_after_holiday():
    from seizou.masters.urls import MENU

    names = [url_name for _label, url_name in MENU]
    assert names.index("masters:staff_list") == names.index("masters:holiday_list") + 1
    assert ("社員", "masters:staff_list") in MENU


# ---------------------------------------------------------------- (b) 新規登録と番号の重複


def test_create_goes_through_service_and_refuses_the_same_number(logged_in):
    url = reverse("masters:staff_create")

    response = logged_in.post(url, {"code": "0012", "name": "作り物 一郎", "note": ""})
    row = Staff.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("masters:staff_detail", args=[row.pk])
    assert (row.code, row.code_key, row.name, row.active) == ("0012", "12", "作り物 一郎", True)

    response = logged_in.post(url, {"code": "12", "name": "作り物 二郎", "note": ""})
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body
    # 赤の知らせは 1 文目 (「保存できません。」) を太字に分けて描く
    assert response.context["notice"].message == DUPLICATE_12
    assert DUPLICATE_12.removeprefix("保存できません。") in body
    assert Staff.objects.count() == 1


# ---------------------------------------------------------------- (c) 編集の「使用中」と (d) 新規登録に「使用中」が無い


def test_update_unchecks_active_and_list_hides_it(logged_in, ichiro):
    """「使用中」の説明の字は、本人の決め 11-11 13:1x による読み替え (使用停止の社員を断らず、R28 で知らせる)。"""
    url = reverse("masters:staff_update", args=[ichiro.pk])
    body = logged_in.get(url).content.decode()
    assert 'name="active"' in body
    assert (
        "外すと、実績の入力でこの社員番号を入れたときに知らせが出ます (止めるかは設定で選べます)。"
        "今までの実績の作業した人はそのまま残ります。"
    ) in body

    response = logged_in.post(url, {"code": "0012", "name": "作り物 一郎", "note": "辞めた"})
    assert response.status_code == 302
    ichiro.refresh_from_db()
    assert ichiro.active is False and ichiro.note == "辞めた"

    list_url = reverse("masters:staff_list")
    assert _codes(logged_in.get(list_url)) == []
    assert _codes(logged_in.get(list_url, {"f_inactive": "1"})) == ["0012"]
    # 詳細の上に使用停止の黄色の 1 行 (段 5 の状態)
    detail = logged_in.get(reverse("masters:staff_detail", args=[ichiro.pk])).content.decode()
    assert "使用停止の社員です。" in detail


def test_create_has_no_active_field(logged_in):
    body = logged_in.get(reverse("masters:staff_create")).content.decode()
    assert 'name="code"' in body and 'name="name"' in body and 'name="note"' in body
    assert 'name="active"' not in body
    assert "札のバーコードと同じ番号。先頭の 0 は省いても同じ番号と見ます。" in body


def test_list_opens_create_in_modal(logged_in):
    body = logged_in.get(reverse("masters:staff_list")).content.decode()
    create_url = reverse("masters:staff_create")
    assert f'href="{create_url}" data-modal-form="{create_url}"' in body


# ---------------------------------------------------------------- (e) 工程の編集の権限


def test_buttons_follow_process_permission(settings, client, ichiro):
    settings.SEIZOU = {"access_control": {"department": "off", "write": "on", "money": "off"}}
    genba = account("genba", permissions=["workorders"])
    master = account("master", permissions=["master_edit"])
    koutei = account("koutei", permissions=["processes"])
    create_href = f'href="{reverse("masters:staff_create")}"'
    delete_href = f'href="{reverse("masters:staff_delete", args=[ichiro.pk])}"'
    edit_href = f'href="{reverse("masters:staff_update", args=[ichiro.pk])}?next='

    def page(person, url):
        client.force_login(get_user_model().objects.get(pk=person.pk))
        response = client.get(url)
        assert response.status_code == 200, url
        return response.content.decode()

    list_url = reverse("masters:staff_list")
    detail_url = reverse("masters:staff_detail", args=[ichiro.pk])
    body = page(genba, list_url)
    assert create_href not in body
    body = page(genba, detail_url)
    assert delete_href not in body and edit_href not in body
    for person in (master, koutei):
        assert create_href in page(person, list_url), person.username
        body = page(person, detail_url)
        assert delete_href in body and edit_href in body, person.username


# ---------------------------------------------------------------- (f) ユースケース


def test_five_screens_have_usecase():
    by_url_name = {screen.url_name: screen for screen in all_screens()}
    for url_name in STAFF_SCREENS:
        assert url_name in by_url_name, url_name
        usecase = by_url_name[url_name].usecase
        assert USECASE.fullmatch(usecase), url_name
        assert usecase.startswith("使う人: 総務・生産管理 / いつ: "), url_name
    assert by_url_name["masters:staff_delete"].usecase == (
        "使う人: 総務・生産管理 / いつ: 間違えて足した社員や、残さなくてよい社員を消すとき / "
        "何のために: 実績に残す作業した人を、社員番号で引けるようにする / 次にすること: 社員の一覧で確かめる"
    )
    names = {url_name: by_url_name[url_name].name for url_name in STAFF_SCREENS}
    assert names == {
        "masters:staff_list": "社員",
        "masters:staff_detail": "社員の詳細",
        "masters:staff_create": "社員の新規登録",
        "masters:staff_update": "社員の編集",
        "masters:staff_delete": "社員の削除",
    }


# ---------------------------------------------------------------- (g) 削除の確かめの件数と (h) 削除の後


def test_delete_confirm_shows_progress_count(logged_in, user, step, ichiro):
    other = services.create_staff({"code": "0034", "name": "作り物 三郎"}, user)
    for _ in range(2):
        progress_services.record_progress(step, Decimal("1"), RECORDED_AT, user, staff=ichiro)

    body = logged_in.get(reverse("masters:staff_delete", args=[ichiro.pk])).content.decode()
    assert "0012 作り物 一郎 を削除します。" in body
    assert COPY_NOTE_2 in body
    assert f'href="{reverse("masters:staff_detail", args=[ichiro.pk])}"' in body  # キャンセルは詳細へ

    body = logged_in.get(reverse("masters:staff_delete", args=[other.pk])).content.decode()
    assert "0034 作り物 三郎 を削除します。" in body
    assert "この社員の実績" not in body


def test_detail_shows_delete_and_delete_copies_to_progress(logged_in, user, step, ichiro):
    for _ in range(2):
        progress_services.record_progress(step, Decimal("1"), RECORDED_AT, user, staff=ichiro)
    delete_url = reverse("masters:staff_delete", args=[ichiro.pk])
    body = logged_in.get(reverse("masters:staff_detail", args=[ichiro.pk])).content.decode()
    assert f'href="{delete_url}"' in body

    response = logged_in.post(delete_url)
    assert response.status_code == 302
    assert response["Location"] == reverse("masters:staff_list")
    messages = [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]
    assert messages == [("success", "社員 0012 作り物 一郎 を削除しました。実績 2 件に、名前と番号を写して残しました。")]
    assert not Staff.objects.exists()
    assert logged_in.get(delete_url).status_code == 404

    assert list(Progress.objects.values_list("staff", "staff_code", "staff_name")) == [(None, "0012", "作り物 一郎")] * 2
    listing = logged_in.get(reverse("progress:record_list"))
    assert [row["obj"].get_staff_name_display() for row in listing.context["rows"]] == ["0012 作り物 一郎"] * 2
    assert "0012 作り物 一郎" in listing.content.decode()


def test_delete_without_progress_says_only_deleted(logged_in, ichiro):
    response = logged_in.post(reverse("masters:staff_delete", args=[ichiro.pk]))
    assert response.status_code == 302
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert messages == ["社員 0012 作り物 一郎 を削除しました。"]


# ---------------------------------------------------------------- (h) 照合の字は詳細と履歴に出さない


def test_detail_and_history_hide_code_key(logged_in, user, ichiro):
    """照合の字 (code_key) は仕組みのための列なので、詳細の「基本情報」と変更の履歴に出さない (N401b の要望 4)。

    社員番号を直したことは「社員番号」の行に残る (先頭の 0 だけを変える直しでも、照合の字の行は足さない)。
    """
    assert hidden_detail_fields(ichiro) == frozenset({"code_key"})
    body = logged_in.get(reverse("masters:staff_detail", args=[ichiro.pk])).content.decode()
    assert "照合の字" not in body
    assert "社員番号" in body

    services.update_staff(ichiro, {"code": "012", "name": "作り物 一郎"}, user)
    rows = list(ChangeLog.objects.filter(model="masters.staff", object_pk=ichiro.pk).order_by("pk"))
    assert {"field": "code", "label": "社員番号", "type": "text", "before": "", "after": "0012"} in rows[0].changes
    assert rows[1].changes == [{"field": "code", "label": "社員番号", "type": "text", "before": "0012", "after": "012"}]
    assert all(change["field"] != "code_key" for row in rows for change in row.changes)
