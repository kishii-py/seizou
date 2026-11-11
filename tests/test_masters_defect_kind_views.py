"""不良の区分の画面 (masters の DefectKindListView・DefectKindDetailView・DefectKindCreateView・DefectKindUpdateView・DefectKindDeleteView。2 段目 N407b)。

左のメニューの「不良の区分」・5 枚が開く事・新規登録 (services を通り、同じ名前は前後の空白つきでも赤の知らせ)・使用停止の区分の詳細の黄色の 1 行・
削除の確かめの件数の文と、消した後の知らせ・工程の編集の権限の無い人が登録で断られる事・5 枚のユースケースを見る。
区分と実績は services で作る (工程の行だけは R07 の試験と同じく表に直に作る)。名前は作り物だけ。
"""

import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse

from seizou.core import access
from seizou.core.screens import all_screens
from seizou.masters import services
from seizou.masters.models import DefectKind, Process
from seizou.orders import services as orders_services
from seizou.progress import services as progress_services
from seizou.progress.models import ProgressDefect
from seizou.workorders import services as workorders_services
from tests.test_core_access import account

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
USECASE = re.compile(r"使う人: .+ / いつ: .+ / 何のために: .+ / 次にすること: .+")
KIND_SCREENS = (
    "masters:defectkind_list",
    "masters:defectkind_detail",
    "masters:defectkind_create",
    "masters:defectkind_update",
    "masters:defectkind_delete",
)
DUPLICATE_SCRATCH = (
    "保存できません。不良の区分「作り物の傷」は既にあります。集計の行が分かれないように、今の区分を使うか、別の名前にしてください。"
)
INACTIVE_LINE = "使用停止の区分です。実績の入力の候補に出ません。今までの不良の行の区分はそのまま残ります。"
ACTIVE_HELP = "外すと、実績の入力の候補に出なくなります。今までの不良の行の区分はそのまま残ります。"
COPY_NOTE_2 = (
    "この区分の不良の行 2 件には、区分の名前を写して残します。不良の集計の区分の行では「未設定」に入ります。"
    "区分ごとの数を残したいときは、消さずに使用停止にしてください。"
)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def scratch(user):
    return services.create_defect_kind({"name": "作り物の傷", "sort_order": 2}, user)


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


def _names(response):
    return [row["obj"].name for row in response.context["rows"]]


def _messages(response):
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


# ---------------------------------------------------------------- (a) 入口と 5 枚が開く


def test_menu_has_defect_kind_entry_after_staff(logged_in, scratch):
    from seizou.masters.urls import MENU

    names = [url_name for _label, url_name in MENU]
    assert names.index("masters:defectkind_list") == names.index("masters:staff_list") + 1
    assert ("不良の区分", "masters:defectkind_list") in MENU
    body = logged_in.get(reverse("core:home")).content.decode()
    assert f'href="{reverse("masters:defectkind_list")}"' in body


def test_five_screens_open(logged_in, scratch):
    for url_name in ("masters:defectkind_list", "masters:defectkind_create"):
        assert logged_in.get(reverse(url_name)).status_code == 200, url_name
    for url_name in ("masters:defectkind_detail", "masters:defectkind_update", "masters:defectkind_delete"):
        assert logged_in.get(reverse(url_name, args=[scratch.pk])).status_code == 200, url_name


def test_list_columns_search_order_and_inactive(logged_in, user, scratch):
    dent = services.create_defect_kind({"name": "作り物の打痕", "sort_order": 1, "note": "角の当たり"}, user)
    stopped = services.create_defect_kind({"name": "作り物の錆", "sort_order": 0}, user)
    services.update_defect_kind(stopped, {"active": False}, user)
    url = reverse("masters:defectkind_list")

    response = logged_in.get(url)
    assert response.status_code == 200
    # 並び順の小さい順。使用停止の区分は既定で出ない
    assert _names(response) == ["作り物の打痕", "作り物の傷"]
    headers = [str(spec.label) for spec in response.context["columns"]]
    assert headers == ["名前", "並び順", "備考"]
    body = response.content.decode()
    assert "角の当たり" in body
    # 説明の 1 文は題の下に出さず、目録と説明書が読む screen_help にある (決め 11-03 14:1x)
    assert response.context["screen_help"] == "実績に入れる不良の区分。集計の行になるので、同じ名前は 2 つ作れません。並び順の小さい順に、実績の入力の候補に並びます。"
    assert _names(logged_in.get(url, {"f_inactive": "1"})) == ["作り物の錆", "作り物の打痕", "作り物の傷"]
    assert _names(logged_in.get(url, {"q": "打痕"})) == ["作り物の打痕"]
    assert dent.pk


def test_list_opens_create_in_modal(logged_in):
    body = logged_in.get(reverse("masters:defectkind_list")).content.decode()
    create_url = reverse("masters:defectkind_create")
    assert f'href="{create_url}" data-modal-form="{create_url}"' in body


# ---------------------------------------------------------------- (b) 新規登録と名前の重複


def test_create_goes_through_service_and_refuses_the_same_name(logged_in):
    url = reverse("masters:defectkind_create")
    body = logged_in.get(url).content.decode()
    assert 'name="name"' in body and 'name="sort_order"' in body and 'name="note"' in body
    assert 'name="active"' not in body

    response = logged_in.post(url, {"name": "作り物の傷", "sort_order": "3", "note": ""})
    row = DefectKind.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("masters:defectkind_detail", args=[row.pk])
    assert (row.name, row.sort_order, row.active) == ("作り物の傷", 3, True)
    assert _messages(response) == [("success", "不良の区分「作り物の傷」を登録しました。")]

    response = logged_in.post(url, {"name": "　作り物の傷 ", "sort_order": "0", "note": ""})
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body
    assert response.context["notice"].message == DUPLICATE_SCRATCH
    assert DUPLICATE_SCRATCH.removeprefix("保存できません。") in body
    assert DefectKind.objects.count() == 1


# ---------------------------------------------------------------- (c) 編集の「使用中」と使用停止の詳細


def test_update_unchecks_active_and_detail_shows_inactive_line(logged_in, scratch):
    url = reverse("masters:defectkind_update", args=[scratch.pk])
    body = logged_in.get(url).content.decode()
    assert 'name="active"' in body
    assert ACTIVE_HELP in body

    detail_url = reverse("masters:defectkind_detail", args=[scratch.pk])
    assert INACTIVE_LINE not in logged_in.get(detail_url).content.decode()

    response = logged_in.post(url, {"name": "作り物の傷", "sort_order": "2", "note": "使わない"})
    assert response.status_code == 302
    scratch.refresh_from_db()
    assert scratch.active is False and scratch.note == "使わない"

    detail = logged_in.get(detail_url).content.decode()
    assert INACTIVE_LINE in detail
    assert 'role="status"' in detail
    assert _names(logged_in.get(reverse("masters:defectkind_list"))) == []


# ---------------------------------------------------------------- (d) 削除の確かめの件数と消した後


def test_delete_confirm_shows_line_count(logged_in, user, step, scratch):
    other = services.create_defect_kind({"name": "作り物の打痕"}, user)
    for _ in range(2):
        progress_services.record_progress(step, Decimal("1"), RECORDED_AT, user, defects=[{"kind": scratch, "qty": Decimal("1")}])

    body = logged_in.get(reverse("masters:defectkind_delete", args=[scratch.pk])).content.decode()
    assert "作り物の傷 を削除します。" in body
    assert COPY_NOTE_2 in body
    assert f'href="{reverse("masters:defectkind_detail", args=[scratch.pk])}"' in body  # キャンセルは詳細へ

    body = logged_in.get(reverse("masters:defectkind_delete", args=[other.pk])).content.decode()
    assert "作り物の打痕 を削除します。" in body
    assert "この区分の不良の行" not in body


def test_detail_shows_delete_and_delete_copies_name(logged_in, user, step, scratch):
    for _ in range(2):
        progress_services.record_progress(step, Decimal("1"), RECORDED_AT, user, defects=[{"kind": scratch, "qty": Decimal("1")}])
    delete_url = reverse("masters:defectkind_delete", args=[scratch.pk])
    body = logged_in.get(reverse("masters:defectkind_detail", args=[scratch.pk])).content.decode()
    assert f'href="{delete_url}"' in body

    response = logged_in.post(delete_url)
    assert response.status_code == 302
    assert response["Location"] == reverse("masters:defectkind_list")
    assert _messages(response) == [
        ("success", "不良の区分「作り物の傷」を削除しました。不良の行 2 件に、区分の名前を写して残しました。")
    ]
    assert not DefectKind.objects.exists()
    assert list(ProgressDefect.objects.values_list("kind", "kind_name")) == [(None, "作り物の傷")] * 2


def test_delete_without_lines_says_only_deleted(logged_in, scratch):
    response = logged_in.post(reverse("masters:defectkind_delete", args=[scratch.pk]))
    assert response.status_code == 302
    assert [text for _level, text in _messages(response)] == ["不良の区分「作り物の傷」を削除しました。"]


# ---------------------------------------------------------------- (e) 工程の編集の権限


def test_kind_actions_follow_process_permission_without_money():
    for action in ("masters.create_defect_kind", "masters.update_defect_kind", "masters.delete_defect_kind"):
        assert access.ACTION_KINDS[action] == "processes"
        assert action not in access.MONEY_ACTIONS
    assert access.KIND_NOTES["processes"] == "工程と会社の休日と社員と不良の区分"


def test_create_is_refused_without_process_permission(settings, client, scratch):
    settings.SEIZOU = {"access_control": {"department": "off", "write": "on", "money": "off"}}
    genba = account("genba", permissions=["workorders"])
    koutei = account("koutei", permissions=["processes"])
    create_href = f'href="{reverse("masters:defectkind_create")}"'
    delete_href = f'href="{reverse("masters:defectkind_delete", args=[scratch.pk])}"'

    client.force_login(get_user_model().objects.get(pk=genba.pk))
    assert create_href not in client.get(reverse("masters:defectkind_list")).content.decode()
    assert delete_href not in client.get(reverse("masters:defectkind_detail", args=[scratch.pk])).content.decode()
    response = client.post(reverse("masters:defectkind_create"), {"name": "作り物の打痕", "sort_order": "0", "note": ""})
    assert response.status_code == 200
    assert 'data-notice="stop"' in response.content.decode()
    assert response.context["notice"].message.startswith("工程の編集の権限がありません。")
    assert DefectKind.objects.count() == 1

    client.force_login(get_user_model().objects.get(pk=koutei.pk))
    assert create_href in client.get(reverse("masters:defectkind_list")).content.decode()
    assert delete_href in client.get(reverse("masters:defectkind_detail", args=[scratch.pk])).content.decode()
    response = client.post(reverse("masters:defectkind_create"), {"name": "作り物の打痕", "sort_order": "0", "note": ""})
    assert response.status_code == 302
    assert DefectKind.objects.count() == 2


# ---------------------------------------------------------------- (f) ユースケース


def test_five_screens_have_usecase():
    by_url_name = {screen.url_name: screen for screen in all_screens()}
    for url_name in KIND_SCREENS:
        assert url_name in by_url_name, url_name
        usecase = by_url_name[url_name].usecase
        assert USECASE.fullmatch(usecase), url_name
        assert usecase.startswith("使う人: 品質・生産管理 / いつ: "), url_name
        assert " / 何のために: 実績の不良を区分ごとに数えられるようにする / " in usecase, url_name
    names = {url_name: by_url_name[url_name].name for url_name in KIND_SCREENS}
    assert names == {
        "masters:defectkind_list": "不良の区分",
        "masters:defectkind_detail": "不良の区分の詳細",
        "masters:defectkind_create": "不良の区分の新規登録",
        "masters:defectkind_update": "不良の区分の編集",
        "masters:defectkind_delete": "不良の区分の削除",
    }
