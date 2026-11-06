"""左のメニューの実績の一覧の入口「実績」(N159。本人の決め 11-05 13:3x の 6)。進捗の盤の入口は「進捗」のまま。

今の画面の印は、道の頭がいちばん長く合う入口に付く (core の mark_active)。実績の一覧 (/progress/records/) と
実績の新規登録 (/progress/records/new/) は「実績」、進捗の盤 (/progress/) は「進捗」。
"""

import re

import pytest
from django.urls import reverse

from seizou.core.context_processors import core_menu, mark_active, menu


def _active(path):
    items = [*core_menu(), *menu()]
    mark_active(items, path)
    return [item["label"] for item in items if item["active"]]


@pytest.mark.django_db
def test_menu_marks_records_entry_on_record_list():
    assert _active(reverse("progress:record_list")) == ["実績"]
    assert _active(reverse("progress:progress_list")) == ["進捗"]
    assert _active(reverse("progress:progress_create")) == ["実績"]


def test_sidebar_on_record_list_marks_records(client_logged_in):
    body = client_logged_in.get(reverse("progress:record_list")).content.decode()
    records, board = reverse("progress:record_list"), reverse("progress:progress_list")
    assert re.search(rf'<a href="{records}"[^>]*aria-current="page"', body)
    assert not re.search(rf'<a href="{board}"[^>]*aria-current="page"', body)


def test_sidebar_on_progress_create_marks_records(client_logged_in):
    """実績の新規登録は、盤から開いても実績の一覧から開いても、左のメニューの「実績」に印が付く (道が実績の一覧の下にある)。"""
    response = client_logged_in.get(reverse("progress:progress_create"))
    assert response.status_code == 200
    body = response.content.decode()
    records, board = reverse("progress:record_list"), reverse("progress:progress_list")
    assert re.search(rf'<a href="{records}"[^>]*aria-current="page"', body)
    assert not re.search(rf'<a href="{board}"[^>]*aria-current="page"', body)
