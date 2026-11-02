"""第 3 波の土台 2 つを確かめる。

1. context processor の seizou.parts (存在する部品の短い名前の並び)。テンプレートが
   {% if "attachments" in seizou.parts %} で、入っている部品の枠だけを include するための物。
2. 詳細の土台のファイルの URL の差し替え (model の file_url(field_name))。ログインした人にだけ返す口を持つ
   model が、core のテンプレートを触らずに URL を差し替えるための約束。
"""

import pytest
from django.urls import reverse

from seizou.core import context_processors
from seizou.core.views import file_info, file_url_for
from seizou.parts import PARTS


def test_part_names_are_short_names():
    names = context_processors.part_names()
    assert names[0] == "core"
    assert "masters" in names
    assert len(names) == len(PARTS)
    assert all("." not in name for name in names)


@pytest.mark.django_db
def test_login_page_context_has_parts(client):
    response = client.get(reverse("core:login"))
    assert response.status_code == 200
    assert "masters" in response.context["seizou"]["parts"]
    assert "core" in response.context["seizou"]["parts"]


class _WithHook:
    def file_url(self, field_name):
        return "/protected/" + field_name if field_name == "drawing" else ""


class _NoHook:
    pass


def test_file_url_for_uses_model_hook_only_when_it_answers():
    assert file_url_for(_WithHook(), "drawing") == "/protected/drawing"
    assert file_url_for(_WithHook(), "other") is None
    assert file_url_for(_NoHook(), "drawing") is None


class _File:
    name = "drawings/a.png"

    @property
    def url(self):
        return "/media/drawings/a.png"


def test_file_info_prefers_given_url():
    assert file_info(_File())["url"] == "/media/drawings/a.png"
    info = file_info(_File(), "/protected/x")
    assert info["url"] == "/protected/x"
    assert info["name"] == "a.png"
    assert info["is_image"] is True


def test_file_info_empty_is_none():
    class _Empty:
        name = ""

    assert file_info(_Empty()) is None
    assert file_info(_Empty(), "/protected/x") is None
