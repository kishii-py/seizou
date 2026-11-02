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
def test_empty_is_refused(user, item, processes):
    turn, mill, _grind, _insp = processes
    services.set_item_processes(item, [turn, mill], user)

    with pytest.raises(ValueError, match="工程がありません"):
        services.set_item_processes(item, [], user)

    assert codes(item) == [(1, "TURN"), (2, "MILL")]


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
