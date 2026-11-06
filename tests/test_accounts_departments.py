"""部署の services と画面 (02 の 7 節の「第 5 波以降」の前倒し)。部署を直しても既にいる人の権限は変えない。

services は表の状態を、画面は Django の test client で HTML と表の状態を見る (判定は settings の fixture で 3 つとも on)。
"""

import json
import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services
from seizou.accounts.models import Access, AccessLog, Department, app_labels
from seizou.core.screens import all_screens

User = get_user_model()

PASSWORD = "kumo-tests-2611"
ALL_ON = {"department": "on", "write": "on", "money": "on"}


@pytest.fixture
def on(settings):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}


def account(login_id, *, departments=(), permissions=(), **flags):
    services.issue_account(login_id, PASSWORD, list(departments), list(permissions), flags, None)
    return User.objects.get(username=login_id)


@pytest.fixture
def manager(db):
    """アカウント管理の担当者。"""
    return account("soumu", manage_accounts=True)


@pytest.fixture
def seizou_dept(db):
    return services.create_department("製造", ["workorders", "progress", "masters"], ["workorders"], None)


@pytest.fixture
def sales_dept(db):
    return services.create_department("受注と出荷", ["orders", "shipping"], ["orders", "shipping"], None)


def fresh(dept):
    return Department.objects.get(pk=dept.pk)


def state(dept):
    """部署の今の様子 (断る変更で何も変わらないことを見る)。"""
    row = fresh(dept)
    return (row.name, list(row.apps), list(row.default_permissions), row.sort_order)


# ---------------------------------------------------------------- 表示の method


@pytest.mark.django_db
def test_display_helpers_use_menu_labels(manager, seizou_dept, sales_dept):
    labels = app_labels()
    assert labels["masters"] == "部品マスター・取引先・工程・休日"
    assert labels["purchasing"] == "発注・受入"
    assert "指示書" in labels["workorders"]
    assert "intake" not in labels

    shown = seizou_dept.get_apps_display()
    positions = [shown.index(word) for word in ("指示書", "進捗", "部品マスター・取引先・工程")]
    assert positions == sorted(positions), shown
    assert seizou_dept.get_default_permissions_display() == "指示書"
    assert sales_dept.get_default_permissions_display() == "受注・出荷と納品書"

    assert seizou_dept.member_count() == 0
    account("genba", departments=[seizou_dept])
    assert seizou_dept.member_count() == 1
    gone = account("taishoku", departments=[seizou_dept])
    services.delete_account(gone.seizou_access, manager)
    assert seizou_dept.member_count() == 1
    assert [a.user.get_username() for a in seizou_dept.members] == ["genba"]


# ---------------------------------------------------------------- 直す


@pytest.mark.django_db
def test_update_department_keeps_member_permissions(manager, seizou_dept):
    genba = account("genba", departments=[seizou_dept], permissions=["workorders"])
    logs = AccessLog.objects.count()

    result = services.update_department(
        seizou_dept,
        {"name": "製造部", "apps": ["stock", "workorders"], "default_permissions": ["slip_edit"], "sort_order": 9},
        manager,
    )

    assert isinstance(result, Department)
    assert result.pk == seizou_dept.pk
    row = fresh(seizou_dept)
    assert row.name == "製造部"
    assert row.apps == ["workorders", "stock"]
    assert row.default_permissions == ["slip_edit"]
    assert row.sort_order == 9
    member = Access.objects.get(user=genba)
    assert member.write_permissions == ["workorders"]
    assert list(member.departments.all()) == [row]
    assert AccessLog.objects.count() == logs

    services.update_department(row, {"sort_order": 3}, manager)
    assert state(row) == ("製造部", ["workorders", "stock"], ["slip_edit"], 3)


@pytest.mark.django_db
def test_update_department_keeps_own_name(manager, seizou_dept):
    services.update_department(seizou_dept, {"name": "製造"}, manager)
    assert fresh(seizou_dept).name == "製造"

    services.update_department(seizou_dept, {"name": " 製造 "}, manager)
    assert fresh(seizou_dept).name == "製造"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("data", "words"),
    [
        pytest.param({"name": "受注と出荷"}, "既にあります", id="name-used"),
        pytest.param({"name": "  "}, "名前を入れてください", id="name-empty"),
        pytest.param({"apps": ["accounts"]}, "選べない名前", id="app-accounts"),
        pytest.param({"apps": ["core"]}, "選べない名前", id="app-core"),
        pytest.param({"apps": ["bogus"]}, "選べない名前", id="app-bogus"),
        pytest.param({"default_permissions": ["bogus"]}, "知らない権限の名前", id="permission-bogus"),
        pytest.param({"sort_order": -1}, "並び順は 0 以上", id="sort-negative"),
        pytest.param({"sort_order": "x"}, "並び順は 0 以上", id="sort-text"),
        pytest.param({"sort_order": True}, "並び順は 0 以上", id="sort-bool"),
    ],
)
def test_update_department_refuses_bad_input(manager, seizou_dept, sales_dept, data, words):
    before = state(seizou_dept)

    with pytest.raises(ValueError, match=words):
        services.update_department(seizou_dept, {"name": "製造部", **data}, manager)

    assert state(seizou_dept) == before


# ---------------------------------------------------------------- 消す


@pytest.mark.django_db
def test_delete_department_refuses_with_members_then_deletes(manager, seizou_dept, sales_dept):
    genba = account("genba", departments=[seizou_dept])

    with pytest.raises(ValueError) as refused:
        services.delete_department(seizou_dept, manager)
    message = str(refused.value)
    for word in ("製造", "1 人", "先にアカウント管理で"):
        assert word in message, word
    assert Department.objects.filter(pk=seizou_dept.pk).exists()

    gone = account("taishoku", departments=[seizou_dept])
    services.delete_account(gone.seizou_access, manager)
    with pytest.raises(ValueError, match="1 人"):
        services.delete_department(seizou_dept, manager)
    assert Department.objects.filter(pk=seizou_dept.pk).exists()

    services.update_account(genba.seizou_access, {"departments": []}, manager)
    logs = AccessLog.objects.count()
    assert services.delete_department(seizou_dept, manager) is None

    assert not Department.objects.filter(pk=seizou_dept.pk).exists()
    through = Access.departments.through.objects
    assert not through.filter(department_id=seizou_dept.pk).exists()
    assert Access.objects.filter(pk=gone.seizou_access.pk, deleted_at__isnull=False).exists()
    assert AccessLog.objects.filter(target=gone).exists()
    assert AccessLog.objects.count() == logs

    services.delete_department(sales_dept, manager)
    assert not Department.objects.filter(pk=sales_dept.pk).exists()


@pytest.mark.django_db
def test_delete_refusal_is_the_text_delete_department_raises(manager, seizou_dept, sales_dept):
    assert services.delete_refusal(seizou_dept) == ""

    account("genba", departments=[seizou_dept])
    refusal = services.delete_refusal(seizou_dept)
    assert refusal.startswith("削除できません。")
    for word in ("部署「製造」", "1 人", "先にアカウント管理で"):
        assert word in refusal, word
    with pytest.raises(ValueError) as refused:
        services.delete_department(seizou_dept, manager)
    assert str(refused.value) == refusal
    assert Department.objects.filter(pk=seizou_dept.pk).exists()

    # 削除した人だけの部署は消せる (空の字)
    gone = account("taishoku", departments=[sales_dept])
    services.delete_account(gone.seizou_access, manager)
    assert services.delete_refusal(sales_dept) == ""


# ---------------------------------------------------------------- 作る (今のまま) と開ける部品の確かめ


@pytest.mark.django_db
def test_create_department_is_unchanged(seizou_dept):
    made = services.create_department("総務", ["masters"], ["master_edit"], None, sort_order=4)
    row = fresh(made)
    assert (row.name, row.apps, row.default_permissions, row.sort_order) == ("総務", ["masters"], ["master_edit"], 4)
    assert fresh(seizou_dept).apps == ["workorders", "progress", "masters"]

    with pytest.raises(ValueError, match="既にあります"):
        services.create_department("総務", [], [], None)

    assert services.clean_apps(["masters", "orders", "masters"]) == ["masters", "orders"]
    with pytest.raises(ValueError, match="選べない名前"):
        services.clean_apps(["bogus"])


# ---------------------------------------------------------------- 画面


USECASE = re.compile(r"使う人: .+ / いつ: .+ / 何のために: .+ / 次にすること: .+")


def _urls(dept):
    """5 つの画面の URL (一覧・新規登録・詳細・編集・削除)。"""
    return [
        reverse("accounts:department_list"),
        reverse("accounts:department_create"),
        reverse("accounts:department_detail", args=[dept.pk]),
        reverse("accounts:department_update", args=[dept.pk]),
        reverse("accounts:department_delete", args=[dept.pk]),
    ]


def _form_post(**values):
    """部署の入力の POST の値 (check の欄は入れた物だけを送る)。"""
    return {"name": "", "apps": [], "default_permissions": [], "sort_order": "0", **values}


def _inputs(html, name):
    """name の checkbox の (value, checked) の並び。"""
    found = []
    for tag in re.findall(r"<input[^>]*>", html):
        if f'name="{name}"' not in tag or 'type="checkbox"' not in tag:
            continue
        value = re.search(r'value="([^"]*)"', tag).group(1)
        found.append((value, re.search(r"\schecked(\s|>|$)", tag) is not None))
    return found


def _values(html, name):
    return [value for value, _checked in _inputs(html, name)]


def _checked(html, name):
    return [value for value, checked in _inputs(html, name) if checked]


MORE_MENU = '<details class="dropdown dropdown-end row-menu" data-detail-more>'


def _main(html):
    """土台の本文 (小窓とログアウトの form は外)。"""
    start = html.index("<main")
    return html[start : html.index("</main>", start)]


def _more(html):
    """詳細の頭の「⋯」のメニュー (tests/test_detail_header.py と同じ切り出し)。"""
    start = html.index(MORE_MENU)
    return html[start : html.index("</details>", start)]


@pytest.mark.django_db
def test_department_screens_are_gated(on, settings, client, manager, seizou_dept):
    plain = User.objects.create_user("tester", password=PASSWORD)
    urls = _urls(seizou_dept)

    client.force_login(plain)
    for url in urls:
        response = client.get(url)
        assert response.status_code == 403, url
        html = response.content.decode()
        assert "この画面は開けません" in html
        assert "アカウント管理の権限が要ります" in html
        assert "no-store" in response["Cache-Control"]
    before = Department.objects.count()
    refused = client.post(reverse("accounts:department_create"), _form_post(name="品質保証"))
    assert refused.status_code == 403
    assert Department.objects.count() == before

    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "off"}}
    assert client.get(urls[0]).status_code == 403

    client.logout()
    anonymous = client.get(urls[0])
    assert anonymous.status_code == 302
    assert anonymous["Location"].startswith(reverse("core:login"))

    client.force_login(manager)
    for url in urls:
        assert client.get(url).status_code == 200, url


@pytest.mark.django_db
def test_list_shows_labels_counts_and_entrances(on, client, manager, seizou_dept, sales_dept):
    account("genba", departments=[seizou_dept])
    client.force_login(manager)
    url = reverse("accounts:department_list")

    response = client.get(url)

    assert response.status_code == 200
    html = response.content.decode()
    for word in ("製造", "指示書", "部品マスター・取引先・工程", "人数", "並び順"):
        assert word in html, word
    rows = {row["obj"].name: row for row in response.context["rows"]}
    assert {name: row["cells"][3]["value"] for name, row in rows.items()} == {"製造": 1, "受注と出荷": 0}
    for dept in (seizou_dept, sales_dept):
        assert rows[dept.name]["url"] == reverse("accounts:department_detail", args=[dept.pk])
    assert f'href="{reverse("accounts:department_create")}"' in html
    assert f'href="{reverse("accounts:access_list")}"' in html

    filtered = client.get(url + "?q=製")
    assert [row["obj"].name for row in filtered.context["rows"]] == ["製造"]

    csv_text = client.get(url + "?format=csv").content.decode("utf-8-sig")
    header, *lines = csv_text.splitlines()
    assert "開ける部品" in header
    assert any("指示書" in line for line in lines)

    accounts_page = client.get(reverse("accounts:access_list")).content.decode()
    assert f'href="{url}"' in accounts_page
    assert "部署" in accounts_page


@pytest.mark.django_db
def test_create_department_from_screen(on, client, manager):
    client.force_login(manager)
    url = reverse("accounts:department_create")

    page = client.get(url)

    assert page.status_code == 200
    html = page.content.decode()
    apps = _values(html, "apps")
    for app in ("workorders", "intake", "stock"):
        assert app in apps, app
    for app in ("bookmarks", "accounts", "core", "assistant"):
        assert app not in apps, app
    permissions = _values(html, "default_permissions")
    assert "slip_edit" in permissions
    assert "items" in permissions
    assert "checkbox checkbox-sm" in html
    assert re.search(rf'href="{reverse("accounts:department_list")}">[^<]*<svg[^>]*>.*?</svg>キャンセル', html, re.S)
    assert html.count("btn-primary") == 1

    saved = client.post(
        url,
        _form_post(name="品質保証", apps=["workorders", "progress"], default_permissions=["workorders"], sort_order="5"),
    )

    row = Department.objects.get(name="品質保証")
    assert saved.status_code == 302
    assert saved["Location"] == reverse("accounts:department_detail", args=[row.pk])
    assert row.apps == ["workorders", "progress"]
    assert row.default_permissions == ["workorders"]
    assert row.sort_order == 5
    assert "保存しました。" in client.get(saved["Location"]).content.decode()

    again = client.post(url, _form_post(name="品質保証", sort_order="1"))
    assert again.status_code == 200
    again_html = again.content.decode()
    assert "alert-error" in again_html
    assert "既にあります" in again_html
    assert Department.objects.filter(name="品質保証").count() == 1

    empty = client.post(url, _form_post(name=""))
    assert empty.status_code == 200
    assert "name" in empty.context["form"].errors
    assert Department.objects.count() == 1


@pytest.mark.django_db
def test_detail_lists_living_members(on, client, manager, seizou_dept):
    genba = account("genba", departments=[seizou_dept])
    gone = account("taishoku", departments=[seizou_dept])
    services.delete_account(gone.seizou_access, manager)
    client.force_login(manager)

    response = client.get(reverse("accounts:department_detail", args=[seizou_dept.pk]))

    assert response.status_code == 200
    html = response.content.decode()
    for word in ("基本情報", "所属する人", "genba"):
        assert word in html, word
    shown = next(field["value"] for field in response.context["fields"] if field["label"] == "開ける部品")
    positions = [shown.index(word) for word in ("指示書", "進捗", "部品マスター・取引先・工程")]
    assert positions == sorted(positions), shown
    assert shown in html
    assert f'href="{reverse("accounts:access_detail", args=[genba.seizou_access.pk])}"' in html
    assert f'href="{reverse("accounts:department_update", args=[seizou_dept.pk])}"' in html
    assert f'href="{reverse("accounts:department_list")}"' in html
    assert "taishoku" not in html
    assert response.context["tabs"][0]["count"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize("with_member", [True, False], ids=["with-member", "no-member"])
def test_detail_more_menu_ends_with_delete(on, client, manager, seizou_dept, with_member):
    if with_member:
        account("genba", departments=[seizou_dept])
    client.force_login(manager)
    url = reverse("accounts:department_delete", args=[seizou_dept.pk])

    html = client.get(reverse("accounts:department_detail", args=[seizou_dept.pk])).content.decode()

    menu = _more(html)
    last = menu.rsplit("<li>", 1)[1]
    assert f'href="{url}"' in last and "text-error" in last and "削除</a>" in last
    assert "data-modal-form" not in menu
    assert "btn-error" not in html
    # 赤い操作はボタンの列に出さない (「⋯」の外に同じ URL の link が無い)
    assert html.count(f'href="{url}"') == 1


@pytest.mark.django_db
def test_update_keeps_member_permissions(on, client, manager, seizou_dept):
    genba = account("genba", departments=[seizou_dept], permissions=["workorders"])
    url = reverse("accounts:department_update", args=[seizou_dept.pk])
    client.force_login(manager)

    page = client.get(url)

    assert page.status_code == 200
    html = page.content.decode()
    assert 'value="製造"' in html
    assert "workorders" in _checked(html, "apps")
    assert _checked(html, "default_permissions") == ["workorders"]
    assert f'href="{reverse("accounts:department_delete", args=[seizou_dept.pk])}"' in html

    saved = client.post(
        url,
        _form_post(name="製造部", apps=["workorders", "stock"], default_permissions=["slip_edit"], sort_order="9"),
    )

    assert saved.status_code == 302
    assert saved["Location"] == reverse("accounts:department_detail", args=[seizou_dept.pk])
    assert state(seizou_dept) == ("製造部", ["workorders", "stock"], ["slip_edit"], 9)
    member = Access.objects.get(user=genba)
    assert member.write_permissions == ["workorders"]
    assert [d.name for d in member.departments.all()] == ["製造部"]

    issue = client.get(reverse("accounts:access_create")).content.decode()
    found = re.search(r'<script id="access-data" type="application/json">(.*?)</script>', issue, re.S)
    assert json.loads(found.group(1))["departments"][str(seizou_dept.pk)] == ["slip_edit"]


@pytest.mark.django_db
def test_delete_confirm_refuse_then_delete(on, client, manager, seizou_dept):
    genba = account("genba", departments=[seizou_dept])
    url = reverse("accounts:department_delete", args=[seizou_dept.pk])
    client.force_login(manager)

    page = client.get(url)

    assert page.status_code == 200
    html = page.content.decode()
    for word in ("部署「", "製造", "1 人", "先にアカウント管理で"):
        assert word in html, word
    assert 'class="btn btn-error"' not in html
    detail = reverse("accounts:department_detail", args=[seizou_dept.pk])
    assert re.search(rf'href="{detail}">[^<]*<svg[^>]*>.*?</svg>キャンセル', html, re.S)

    refused = client.post(url, {})
    assert refused.status_code == 200
    refused_html = refused.content.decode()
    assert "alert-error" in refused_html
    assert "1 人のアカウントが付いています" in refused_html
    assert "先にアカウント管理で" in refused_html
    assert Department.objects.filter(pk=seizou_dept.pk).exists()

    services.update_account(genba.seizou_access, {"departments": []}, manager)
    deleted = client.post(url, {})
    assert deleted.status_code == 302
    assert deleted["Location"] == reverse("accounts:department_list")
    assert not Department.objects.filter(pk=seizou_dept.pk).exists()
    assert client.get(url).status_code == 404


@pytest.mark.django_db
def test_delete_confirm_with_members_shows_refusal_members_and_accounts(on, client, manager, seizou_dept):
    genba = account("genba", departments=[seizou_dept])
    gone = account("taishoku", departments=[seizou_dept])
    services.delete_account(gone.seizou_access, manager)
    client.force_login(manager)

    page = client.get(reverse("accounts:department_delete", args=[seizou_dept.pk]))

    assert page.status_code == 200
    main = _main(page.content.decode())
    assert 'class="btn btn-error"' not in main
    assert "削除</button>" not in main
    assert "<form" not in main
    assert "削除します" not in main
    alert = re.search(r'<div class="alert alert-warning alert-soft[^"]*" role="alert">.*?</div>', main, re.S).group(0)
    assert '<span class="font-semibold">削除できません。</span>' in alert
    for word in ("部署「製造」には 1 人のアカウントが付いています", "先にアカウント管理で"):
        assert word in alert, word
    assert "所属する人" in main
    assert f'href="{reverse("accounts:access_detail", args=[genba.seizou_access.pk])}"' in main
    assert "genba" in main
    assert "taishoku" not in main
    assert re.search(rf'href="{reverse("accounts:access_list")}">[^<]*<svg[^>]*>.*?</svg>アカウント管理', main, re.S)
    detail = reverse("accounts:department_detail", args=[seizou_dept.pk])
    assert re.search(rf'href="{detail}">[^<]*<svg[^>]*>.*?</svg>キャンセル', main, re.S)


def test_catalog_has_department_screens_with_usecase():
    screens = {screen.url_name: screen for screen in all_screens()}
    names = {
        "accounts:department_list": "部署",
        "accounts:department_create": "部署の新規登録",
        "accounts:department_detail": "部署の詳細",
        "accounts:department_update": "部署の編集",
        "accounts:department_delete": "部署の削除",
    }
    for url_name, name in names.items():
        assert url_name in screens, url_name
        screen = screens[url_name]
        assert screen.name == name
        assert USECASE.fullmatch(screen.usecase), screen.usecase
        assert screen.usecase.startswith("使う人: 総務 / いつ: "), screen.usecase
