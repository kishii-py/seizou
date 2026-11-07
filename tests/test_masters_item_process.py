"""部品の標準の工程の並びを置き換える関数 (masters.services.set_item_processes)。"""

import pytest
from django.contrib.auth import get_user_model

from seizou.masters import services
from seizou.masters.models import Item, Process


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def item(user):
    return services.create_item({"part_no": "P-1", "name": "作り物の軸"}, user)


@pytest.fixture
def processes(user):
    return [
        services.create_process({"code": code, "name": code, "sort_order": order}, user)
        for order, code in enumerate(["TURN", "MILL", "GRIND", "INSP"], start=1)
    ]


def codes(item):
    return [(row.seq, row.process.code) for row in item.item_processes.order_by("seq")]


@pytest.mark.django_db
def test_set_item_processes_in_order(user, item, processes):
    turn, mill, grind, _insp = processes

    made = services.set_item_processes(item, [grind, turn, mill], user)

    assert [row.seq for row in made] == [1, 2, 3]
    assert [row.process.code for row in made] == ["GRIND", "TURN", "MILL"]
    assert codes(item) == [(1, "GRIND"), (2, "TURN"), (3, "MILL")]


@pytest.mark.django_db
def test_set_item_processes_replaces(user, item, processes):
    turn, mill, grind, insp = processes
    services.set_item_processes(item, [turn, mill, grind], user)

    services.set_item_processes(item, [mill, insp], user)

    assert codes(item) == [(1, "MILL"), (2, "INSP")]
    assert item.item_processes.count() == 2


@pytest.mark.django_db
def test_empty_clears_all_rows(user, item, processes):
    # 決め C112-1 = a: 空の並びは断らず、全部の行を消して空の並びを返す (部品を作った直後と同じ形)
    turn, mill, _grind, _insp = processes
    services.set_item_processes(item, [turn, mill], user)

    made = services.set_item_processes(item, [], user)

    assert made == []
    assert codes(item) == []
    assert item.item_processes.count() == 0
    assert Process.objects.count() == 4


@pytest.mark.django_db
def test_same_process_twice_is_refused(user, item, processes):
    turn, mill, _grind, _insp = processes
    services.set_item_processes(item, [turn, mill], user)

    with pytest.raises(ValueError, match="MILL"):
        services.set_item_processes(item, [mill, turn, mill], user)

    assert codes(item) == [(1, "TURN"), (2, "MILL")]


@pytest.mark.django_db
def test_not_a_process_is_refused(user, item, processes):
    turn, _mill, _grind, _insp = processes
    services.set_item_processes(item, [turn], user)

    with pytest.raises(ValueError, match="工程でない物"):
        services.set_item_processes(item, [turn, "MILL"], user)

    assert codes(item) == [(1, "TURN")]
    assert Item.objects.count() == 1 and Process.objects.count() == 4


# ---------------------------------------------------------------- 足す・順を直す・外す (決め C19-3。C112 の その 1)


def pks(item):
    return [row.pk for row in item.item_processes.order_by("seq")]


@pytest.mark.django_db
def test_add_appends_in_given_order(user, item, processes):
    turn, mill, grind, _insp = processes

    returned = services.add_item_processes({"item": item, "processes": [grind, turn]}, user)

    assert isinstance(returned, Item) and returned.pk == item.pk
    assert codes(item) == [(1, "GRIND"), (2, "TURN")]

    services.add_item_processes({"item": item, "processes": [mill]}, user)

    assert codes(item) == [(1, "GRIND"), (2, "TURN"), (3, "MILL")]


@pytest.mark.django_db
def test_add_skips_processes_already_in_the_list(user, item, processes):
    turn, mill, _grind, _insp = processes
    services.set_item_processes(item, [turn], user)

    services.add_item_processes({"item": item, "processes": [turn, mill, mill]}, user)

    assert codes(item) == [(1, "TURN"), (2, "MILL")]

    # 全部が今ある工程なら何も変えない (行を作り直していない)
    before = pks(item)
    services.add_item_processes({"item": item, "processes": [mill, turn]}, user)
    assert pks(item) == before
    assert codes(item) == [(1, "TURN"), (2, "MILL")]


@pytest.mark.django_db
def test_add_with_nothing_changes_nothing(user, item, processes):
    turn, _mill, _grind, _insp = processes
    services.set_item_processes(item, [turn], user)
    before = pks(item)

    returned = services.add_item_processes({"item": item, "processes": []}, user)

    assert returned.pk == item.pk
    assert pks(item) == before
    with pytest.raises(ValueError, match="知らない項目"):
        services.add_item_processes({"item": item, "processes": [turn], "seq": 1}, user)


@pytest.mark.django_db
def test_move_puts_row_at_seq(user, item, processes):
    turn, mill, grind, _insp = processes
    services.set_item_processes(item, [turn, mill, grind], user)

    grind_row = item.item_processes.get(process=grind)
    returned = services.move_item_process(grind_row, {"seq": 1}, user)
    assert returned.pk == item.pk
    assert codes(item) == [(1, "GRIND"), (2, "TURN"), (3, "MILL")]

    turn_row = item.item_processes.get(process=turn)
    services.move_item_process(turn_row, {"seq": 3}, user)
    assert codes(item) == [(1, "GRIND"), (2, "MILL"), (3, "TURN")]

    # 行の数の外は断らずに端へ置く
    services.move_item_process(item.item_processes.get(process=turn), {"seq": 0}, user)
    assert codes(item) == [(1, "TURN"), (2, "GRIND"), (3, "MILL")]
    services.move_item_process(item.item_processes.get(process=turn), {"seq": 9}, user)
    assert codes(item) == [(1, "GRIND"), (2, "MILL"), (3, "TURN")]

    # 今と同じ位置なら書かない (全部の行の pk が変わらない)
    before = pks(item)
    services.move_item_process(item.item_processes.get(process=mill), {"seq": 2}, user)
    assert pks(item) == before

    with pytest.raises(ValueError, match="知らない項目"):
        services.move_item_process(item.item_processes.get(process=mill), {"seq": 1, "item": item}, user)


@pytest.mark.django_db
def test_move_and_remove_refuse_a_row_that_is_gone(user, item, processes):
    turn, mill, grind, _insp = processes
    services.set_item_processes(item, [turn, mill], user)
    stale = item.item_processes.get(process=mill)
    # ほかの人が並びを直すと、行は消して作り直される (古い行の pk は無くなる)
    services.set_item_processes(item, [turn, mill, grind], user)
    before = codes(item)

    with pytest.raises(ValueError) as moved:
        services.move_item_process(stale, {"seq": 1}, user)
    assert str(moved.value).startswith("保存できません。この工程は、もう標準の工程の並びにありません。")
    with pytest.raises(ValueError) as removed:
        services.remove_item_process(stale, user)
    assert str(removed.value).startswith("削除できません。この工程は、もう標準の工程の並びにありません。")
    assert codes(item) == before


@pytest.mark.django_db
def test_remove_closes_the_gap_and_keeps_processes(user, item, processes):
    turn, mill, grind, _insp = processes
    services.set_item_processes(item, [turn, mill, grind], user)

    returned = services.remove_item_process(item.item_processes.get(process=mill), user)

    assert codes(item) == [(1, "TURN"), (2, "GRIND")]
    assert Process.objects.count() == 4
    assert returned.pk == item.pk
    assert returned.saved_message == "MILL MILL を標準の工程から外しました。"


@pytest.mark.django_db
def test_inactive_process_row_stays(user, item, processes):
    turn, _mill, grind, insp = processes
    old = services.create_process({"code": "OLD", "name": "作り物の古い工程", "sort_order": 9}, user)
    services.update_process(old, {"active": False}, user)
    services.set_item_processes(item, [turn, old, grind], user)

    services.remove_item_process(item.item_processes.get(process=grind), user)
    assert codes(item) == [(1, "TURN"), (2, "OLD")]

    services.move_item_process(item.item_processes.get(process=turn), {"seq": 2}, user)
    assert codes(item) == [(1, "OLD"), (2, "TURN")]

    services.add_item_processes({"item": item, "processes": [insp]}, user)
    assert codes(item) == [(1, "OLD"), (2, "TURN"), (3, "INSP")]


@pytest.mark.django_db
def test_remove_last_row_leaves_empty_list(user, item, processes):
    # 決め C112-1 = a: 最後の 1 行も外せ、並びは部品を作った直後と同じ空になる
    turn, mill, _grind, _insp = processes
    services.set_item_processes(item, [turn], user)

    returned = services.remove_item_process(item.item_processes.get(process=turn), user)

    assert codes(item) == []
    assert item.item_processes.count() == 0
    assert returned.pk == item.pk
    assert returned.saved_message == "TURN TURN を標準の工程から外しました。"
    assert Process.objects.count() == 4

    services.add_item_processes({"item": item, "processes": [mill]}, user)
    assert codes(item) == [(1, "MILL")]
