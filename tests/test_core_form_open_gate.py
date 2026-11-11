"""入力の画面を開いた時点の門: 保存できない人 (編集の権限か金額の閲覧が無い) には、開いた時点で「この画面は開けません」を出す。

本人の決め 11-12 07:2x (開いた時点で断る)。直す前は、URL を知っていれば入力の画面が開き、全部入れてから保存で断られていた
(精査の報告 2026-11-10 の D1。入口のボタンは出ないので、ブックマークや人からもらった link で開いた人だけが当たる)。
- 判定が on の会社: 開いた時点で 403 の画面。文は保存の時の断りと同じ (access.write_refusal)。
- 判定が記録だけ (log) と切 (off) の会社: 今のまま開く。開いただけでは「保存しようとした」の記録の行を書かない。
- 断る人には、ある pk も無い pk も同じ 403 を返す (記録の有無が分からない)。
- 直に POST した時の断り (赤の知らせ・何も保存しない) は今のまま。
利用者は tests/test_core_access.py の account で作る。会社と人と品番は作り物。
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core import access
from seizou.masters import services as masters_services
from seizou.masters.models import Item
from tests.test_core_access import ALL_ON, access_records, account, all_actions

User = get_user_model()
HEADING = "この画面は開けません"
ITEM_REFUSAL = "部品マスターの編集の権限がありません。"
ORDER_REFUSAL = "受注は金額が主な内容のため、金額の閲覧の権限が無い方は新規登録できません。"
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}


def _as(client, person):
    client.force_login(User.objects.get(pk=person.pk))
    return client


@pytest.fixture
def on(settings):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}


@pytest.fixture
def item(db):
    owner = User.objects.create_user("owner", password="pass-for-tests-only")
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, owner)


@pytest.mark.django_db
def test_open_is_refused_without_the_write_permission(client, on):
    """編集の権限の無い人が部品の新規登録を開くと 403。文は保存の時の断りと同じで、入力の欄は出ない。"""
    genba = account("genba")
    response = _as(client, genba).get(reverse("masters:item_create"))
    assert response.status_code == 403
    body = response.content.decode()
    assert HEADING in body and ITEM_REFUSAL in body
    assert 'name="part_no"' not in body
    # 小窓で開いても同じ
    assert _as(client, genba).get(reverse("masters:item_create"), **MODAL).status_code == 403

    # 権限のある人は開く
    master = account("master", permissions=["items"])
    opened = _as(client, master).get(reverse("masters:item_create"))
    assert opened.status_code == 200 and 'name="part_no"' in opened.content.decode()


@pytest.mark.django_db
def test_open_is_refused_for_a_money_action_without_money(client, on):
    """金額の閲覧の無い人が受注の新規登録を開くと 403 (編集の権限はある)。金額の閲覧のある人は開く。"""
    jimu = account("jimu", permissions=["orders"])
    response = _as(client, jimu).get(reverse("orders:order_create"))
    assert response.status_code == 403
    body = response.content.decode()
    assert HEADING in body and ORDER_REFUSAL in body

    keiri = account("keiri", permissions=["orders"], see_money=True)
    assert _as(client, keiri).get(reverse("orders:order_create")).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["log", "off"])
def test_open_is_not_refused_when_the_setting_is_not_on(settings, client, caplog, value):
    """判定が記録だけ (log) と切 (off) の会社は今のまま開き、開いただけでは「保存しようとした」の記録の行を書かない。"""
    settings.SEIZOU = {"access_control": {"department": "off", "write": value, "money": value}}
    genba = account("genba")
    with caplog.at_level("WARNING", logger="seizou.access"):
        assert _as(client, genba).get(reverse("masters:item_create")).status_code == 200
        assert _as(client, genba).get(reverse("orders:order_create")).status_code == 200
    # 開いただけでは「保存しようとした」の記録の行を書かない (金額の出る画面を見た記録は、今までどおり書く)
    assert [record.getMessage() for record in access_records(caplog) if "保存しようとした" in record.getMessage()] == []


@pytest.mark.django_db
def test_refused_people_get_the_same_answer_for_existing_and_missing_records(client, on, item):
    """断る人には、ある pk も無い pk も同じ 403 (記録の有無が分からない)。権限のある人は、無い pk で 404。"""
    genba = account("genba")
    missing = item.pk + 1000
    assert _as(client, genba).get(reverse("masters:item_update", args=[item.pk])).status_code == 403
    assert _as(client, genba).get(reverse("masters:item_update", args=[missing])).status_code == 403

    master = account("master", permissions=["items"])
    assert _as(client, master).get(reverse("masters:item_update", args=[item.pk])).status_code == 200
    assert _as(client, master).get(reverse("masters:item_update", args=[missing])).status_code == 404


@pytest.mark.django_db
def test_direct_post_is_still_refused_with_the_red_notice(client, on):
    """画面を開かずに直に送った時は、今のまま赤の知らせで断り、何も保存しない。"""
    genba = account("genba")
    response = _as(client, genba).post(reverse("masters:item_create"), {"part_no": "T-900", "name": "作り物の板", "unit": "KGM"})
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body and ITEM_REFUSAL in body
    assert not Item.objects.filter(part_no="T-900").exists()


@pytest.mark.django_db
def test_write_refusal_says_no_exactly_when_write_allowed_is_false(on):
    """開いた時点の門の文 (write_refusal) は、入口を出すかの判定 (write_allowed) が偽の時だけ出る (全部の操作と 4 人で見る)。"""
    people = [
        account("genba"),
        account("jimu", permissions=["slip_edit"]),
        account("keiri", permissions=["slip_edit", "master_edit"], see_money=True),
        account("zenken", full_control=True),
    ]
    for person in people:
        for action in sorted(all_actions()):
            refusal = access.write_refusal(person, action)
            assert (refusal == "") == access.write_allowed(person, action), (person.username, action)
