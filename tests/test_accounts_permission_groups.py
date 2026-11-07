"""アカウントと部署の「権限」の check を、まとめごとの 2 つの群 (「マスター」「伝票」) に分ける (初見テスト R5 の候補 C90)。

権限の欄の選び (accounts.forms.permission_choices) は群の入れ子にし、群の先頭にまとめ、その下に中の種類 5 つを置く。
core の Checkboxes は、選択肢に群があるときだけ外の枠に CHECKBOX_GROUPS_CSS を付け、群ごとの枠を幅の広い画面で
横に並べる。群の無い check の欄 (アカウントの「部署」・部署の「開ける画面」など) は今の見た目のまま。
送る値と保存する並び (services.clean_permissions が access.PERMISSION_NAMES にそろえる) は変わらない。
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from seizou.accounts.forms import permission_choices
from seizou.accounts.models import Access, Department
from seizou.core import access
from seizou.core.forms import CHECKBOX_CSS, CHECKBOX_GROUPS_CSS, Checkboxes
from tests import test_accounts_views
from tests.test_accounts_departments import _values
from tests.test_accounts_views import _form_post, account

# アカウントの画面の試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
# on = 権限の判定を 3 つとも有効にする。manager = アカウント管理のできる人。seizou_dept = 部署「製造」。
on = test_accounts_views.on
manager = test_accounts_views.manager
seizou_dept = test_accounts_views.seizou_dept

#: 小窓の形で開くときの見出し (土台が小窓の箱の中身だけを返す)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}

#: 権限の欄の値の並び (群ごとに、まとめ → 中の種類 5 つ)
GROUPED_VALUES = [
    "master_edit",
    "items",
    "partners",
    "processes",
    "connections",
    "manuals",
    "slip_edit",
    "orders",
    "workorders",
    "shipping",
    "billing",
    "purchasing",
]


def _grouped(html, name):
    """name の外の枠に群の格子の class が付き、2 つの群の見出しがある。"""
    assert f'<div id="id_{name}" class="{CHECKBOX_GROUPS_CSS}">' in html
    assert "<label>マスター</label>" in html
    assert "<label>伝票</label>" in html


# ---------------------------------------------------------------- 権限の欄の選び (表は読まない)


def test_permission_choices_are_two_groups_in_group_order():
    choices = permission_choices()

    assert [heading for heading, _options in choices] == ["マスター", "伝票"]
    for (_heading, options), name in zip(choices, access.GROUPS, strict=True):
        assert [value for value, _label in options] == [name, *access.GROUPS[name][1]]
    flat = [value for _heading, options in choices for value, _label in options]
    assert len(flat) == 12
    assert set(flat) == set(access.PERMISSION_NAMES)


def test_permission_labels_are_unchanged():
    labels = {value: label for _heading, options in permission_choices() for value, label in options}

    assert labels["items"] == f"部品マスター ({access.KIND_NOTES['items']})"
    assert labels["partners"] == "取引先"
    assert labels["slip_edit"] == "伝票編集"
    assert labels["master_edit"] == "マスター編集"


# ---------------------------------------------------------------- core の Checkboxes (表は読まない)


def test_checkboxes_without_groups_keep_plain_outer_div():
    html = Checkboxes(attrs={"class": CHECKBOX_CSS}, choices=[("a", "A"), ("b", "B")]).render("x", [], {"id": "id_x"})

    assert '<div id="id_x">' in html
    assert '<div id="id_x" class=' not in html
    assert html.count('class="checkbox checkbox-sm"') == 2


def test_checkboxes_with_groups_put_groups_in_grid():
    widget = Checkboxes(attrs={"class": CHECKBOX_CSS}, choices=[("見出し1", [("a", "A")]), ("見出し2", [("b", "B")])])

    html = widget.render("x", [], {"id": "id_x"})

    assert f'<div id="id_x" class="{CHECKBOX_GROUPS_CSS}">' in html
    assert "<label>見出し1</label>" in html
    assert "<label>見出し2</label>" in html
    assert html.count('class="checkbox checkbox-sm"') == 2


# ---------------------------------------------------------------- 画面 (アカウントの発行と編集・部署の新規登録と編集)


@pytest.mark.django_db
def test_account_screens_show_permission_groups(on, client, manager, seizou_dept):
    jimu = account("jimu", departments=[seizou_dept], permissions=["slip_edit"])
    client.force_login(manager)

    for url in (
        reverse("accounts:access_create"),
        reverse("accounts:access_update", args=[jimu.seizou_access.pk]),
    ):
        page = client.get(url)

        assert page.status_code == 200, url
        html = page.content.decode()
        _grouped(html, "permissions")
        assert _values(html, "permissions") == GROUPED_VALUES
        assert '<div id="id_departments">' in html
        assert 'id="access-data"' in html


@pytest.mark.django_db
def test_department_screens_show_permission_groups(on, client, manager, seizou_dept):
    client.force_login(manager)
    create = reverse("accounts:department_create")

    for url, headers in (
        (create, {}),
        (create, MODAL),
        (reverse("accounts:department_update", args=[seizou_dept.pk]), {}),
    ):
        page = client.get(url, **headers)

        assert page.status_code == 200, (url, headers)
        html = page.content.decode()
        assert ("<!doctype html>" in html) == (not headers)  # 小窓の形は箱の中身だけ (画面の全体の頭が無い)
        _grouped(html, "default_permissions")
        assert _values(html, "default_permissions") == GROUPED_VALUES
        assert f'<div id="id_apps" class="{CHECKBOX_GROUPS_CSS}"' not in html


@pytest.mark.django_db
def test_saved_permission_order_is_unchanged(on, client, manager, seizou_dept):
    """画面の群の並びで送っても、保存する並びは access.PERMISSION_NAMES の順 (まとめ → 種類) のまま。

    まとめと、その中に入らない種類の組で送る (中に入る組は、画面では中の check が薄くなって送られないため)。
    """
    client.force_login(manager)

    department = client.post(
        reverse("accounts:department_update", args=[seizou_dept.pk]),
        {"name": "製造", "apps": ["workorders"], "default_permissions": ["workorders", "master_edit"], "sort_order": "0"},
    )

    assert department.status_code == 302
    assert Department.objects.get(pk=seizou_dept.pk).default_permissions == ["master_edit", "workorders"]

    row = account("jimu", departments=[seizou_dept], permissions=["slip_edit"]).seizou_access
    saved = client.post(
        reverse("accounts:access_update", args=[row.pk]),
        _form_post(row, password="", permissions=["workorders", "master_edit"]),
    )

    assert saved.status_code == 302
    assert Access.objects.get(pk=row.pk).write_permissions == ["master_edit", "workorders"]
