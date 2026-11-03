"""一覧の列 (columns) の書き方の起動の検査 (core/system_checks.py。Django の system check)。

誤りのある画面は試験の関数の中で作る (URL の木に無いので、ほかの試験の run_checks() に漏れない)。
作り物の名前だけを使う。
"""

import pytest
from django.apps import apps
from django.core import checks as django_checks
from django.core.checks.registry import registry

from seizou.core import system_checks
from seizou.core.system_checks import check_list_columns, list_column_errors
from seizou.core.views import BaseListView
from seizou.masters.models import Item
from seizou.orders.views import OrderLineListView
from seizou.purchasing.views import PurchaseOrderListView


def test_list_column_errors_find_mistakes():
    class _MistakeList(BaseListView):
        model = Item
        screen_name = "作り物の誤りの一覧"
        columns = (
            {"name": "x_total", "label": "X", "kind": "numbr"},
            {"name": "y_total", "label": "Y", "sortable": "True"},
            {"name": "z_total", "label": "Z", "totl": True},
            {"name": "w__v", "label": "W", "sort_key": ""},
            "part_no",
            ("name", "品名"),
        )

    errors = list_column_errors([_MistakeList])
    assert [error.id for error in errors] == ["seizou.E003", "seizou.E002", "seizou.E001", "seizou.E002"]
    for error, name in zip(errors, ("x_total", "y_total", "z_total", "w__v"), strict=True):
        assert name in error.msg, error.id
        assert f"{_MistakeList.__module__}._MistakeList" in error.msg, error.id
        assert error.obj is _MistakeList
        assert error.hint
        assert error.level == django_checks.ERROR
    assert "numbr" in errors[0].msg
    assert "totl" in errors[2].msg


def test_list_column_errors_pass_good_forms():
    class _GoodList(BaseListView):
        model = Item
        screen_name = "作り物の正しい一覧"
        columns = (
            "part_no",
            ("name", "品名"),
            {"name": "standard_price", "label": "単価", "total": True},
            {"name": "x_total", "label": "X", "kind": "number", "sortable": True, "total": False},
            {"name": "x__y", "label": "Y", "kind": "date"},
            {"name": "board__days_left", "label": "納期まで", "kind": "number", "sort_key": "due_date"},
        )

    assert list_column_errors([_GoodList]) == []
    # 一覧の画面でない class は見ない
    assert list_column_errors([object]) == []


def test_real_screens_have_no_column_errors(monkeypatch):
    assert check_list_columns(None) == []

    # URL の木の一覧の画面を、本当に見ている (空の並びで [] になっていない)
    seen = []
    monkeypatch.setattr(system_checks, "list_column_errors", lambda view_classes: seen.extend(view_classes) or [])
    check_list_columns(None)
    assert OrderLineListView in seen and PurchaseOrderListView in seen
    assert len(seen) == len(set(seen))

    # app_configs を渡すと、その app の画面だけ
    seen.clear()
    check_list_columns([apps.get_app_config("purchasing")])
    assert PurchaseOrderListView in seen and OrderLineListView not in seen


@pytest.mark.django_db
def test_check_is_registered():
    # run_checks() は Django 自身の検査 (JSONField の対応の確かめ) で DB に触るので db の印が要る
    assert check_list_columns in registry.get_checks()
    assert [message.id for message in django_checks.run_checks() if (message.id or "").startswith("seizou.")] == []
