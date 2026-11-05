"""絵を撮る命令 (manage.py seizou_manual_shots) の、ブラウザを使わない部分 (撮る画面の並びと、Playwright が無いとき)。

ブラウザは動かさない。作り物の部品は「作り物の〜」の名前だけ。
"""

import sys

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from seizou.core.management.commands.seizou_manual_shots import PLAYWRIGHT_MISSING, shot_targets
from seizou.core.management.commands.seizou_manual_shots import Command
from seizou.core.screens import all_screens
from seizou.masters.models import Item


def by_name(targets):
    return {target[0]: target for target in targets}


@pytest.mark.django_db
def test_targets_follow_screen_catalog():
    targets = shot_targets()
    assert [target[0] for target in targets] == [screen.url_name.replace(":", "_") for screen in all_screens()]
    found = by_name(targets)
    assert found["orders_order_list"][2] == reverse("orders:order_list")
    assert found["orders_order_create"][2] == reverse("orders:order_create")
    assert found["workorders_plan_board"][2] is None
    assert found["workorders_plan_board"][3] == "道の引数が pk でない"


@pytest.mark.django_db
def test_detail_opens_first_row_or_is_skipped():
    assert by_name(shot_targets("masters:item_detail"))["masters_item_detail"][2:] == (None, "1 件も無い")
    first = Item.objects.create(part_no="P-1001", name="作り物のブラケット")
    Item.objects.create(part_no="P-1002", name="作り物のプレート")
    targets = shot_targets("masters:item_detail")
    assert len(targets) == 1
    assert targets[0][2:] == (reverse("masters:item_detail", kwargs={"pk": first.pk}), "")


def test_command_stops_without_playwright(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with pytest.raises(CommandError) as raised:
        call_command("seizou_manual_shots")
    assert str(raised.value) == PLAYWRIGHT_MISSING
    assert raised.value.returncode == 1


@pytest.mark.django_db
def test_manual_screen_itself_is_skipped():
    reason = "使い方の画面そのもの"
    assert by_name(shot_targets())["core_manual"][2:] == (None, reason)
    assert [target[0] for target in shot_targets() if target[3] == reason] == ["core_manual"]
    assert shot_targets("core:manual") == [("core_manual", "使い方", None, reason)]


def test_full_page_option_in_help():
    parser = Command().create_parser("manage.py", "seizou_manual_shots")
    assert "--full-page" in parser.format_help()
    assert parser.parse_args([]).full_page is False
    assert parser.parse_args(["--full-page"]).full_page is True
