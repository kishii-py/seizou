"""部署の変更の履歴 (決め HIST-1 の「アカウントと部署」のうち詳細の画面のある部署。C106 の その 9)。

部署の書く関数 (create_department・update_department・delete_department) が、部署の詳細の「履歴」に作った・直した・消したを
残すこと、開ける部品と既定の権限が画面の字で残ること、何も変えなければ増えないこと、アカウントの発行と変更は今の AccessLog に
残り変更の履歴に二重に書かないこと (決め C106-2 = a)、詳細の問い合わせの数が履歴の行の数で増えないことを見る。データは作り物だけ。
"""

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.accounts import services
from seizou.accounts.models import AccessLog, Department
from seizou.core.models import ChangeLog
from seizou.core.services import record_history

PASSWORD = "kumo-tests-2611"


def _rows(dept_pk):
    return list(ChangeLog.objects.filter(owner_model="accounts.department", owner_pk=dept_pk))


def _apps_text(apps):
    """開ける部品の画面の字 (Department.get_apps_display と同じ字)。"""
    return Department(apps=apps).get_apps_display()


@pytest.fixture
def dept(user):
    return services.create_department("製造", ["workorders", "progress"], ["workorders"], user)


@pytest.fixture
def manager_client(client, db):
    """部署の画面を開けるアカウント管理の担当者でログインした client。"""
    services.issue_account("soumu", PASSWORD, [], [], {"manage_accounts": True}, None)
    client.force_login(get_user_model().objects.get(username="soumu"))
    return client


@pytest.mark.django_db
def test_create_records_created_with_display_text(dept):
    rows = _rows(dept.pk)
    assert [(row.action, row.actor_label) for row in rows] == [("created", "tester")]
    assert {"field": "apps", "label": "開ける画面", "type": "text", "before": "", "after": _apps_text(["workorders", "progress"])} in rows[0].changes


@pytest.mark.django_db
def test_update_apps_before_after_and_no_change(manager_client, user, dept):
    services.update_department(dept, {"apps": ["workorders", "progress", "masters"]}, user)

    row = _rows(dept.pk)[0]
    assert row.action == "updated"
    before = _apps_text(["workorders", "progress"])
    after = Department.objects.get(pk=dept.pk).get_apps_display()  # 開ける部品は部品の並びの順にそろえて残る (clean_apps)
    assert after.startswith("部品マスター") and after != before
    assert row.changes == [{"field": "apps", "label": "開ける画面", "type": "text", "before": before, "after": after}]
    body = manager_client.get(reverse("accounts:department_detail", args=[dept.pk])).content.decode()
    part = body[body.index('aria-label="履歴"') : body.index("data-last-change")]
    assert f"開ける画面: {before} → {after}" in part
    assert "最後に直した" in body

    count = ChangeLog.objects.count()
    services.update_department(dept, {"apps": ["workorders", "progress", "masters"], "name": "製造"}, user)
    assert ChangeLog.objects.count() == count


@pytest.mark.django_db
def test_delete_records_deleted_and_keeps_name(user, dept):
    pk = dept.pk

    services.delete_department(dept, user)

    assert not Department.objects.filter(pk=pk).exists()
    row = _rows(pk)[0]
    assert (row.action, row.object_text) == ("deleted", "製造")
    assert {"field": "name", "label": "部署", "type": "text", "before": "製造", "after": ""} in row.changes


@pytest.mark.django_db
def test_refused_delete_leaves_no_history(user, dept):
    services.issue_account("genba", PASSWORD, [dept], [], {}, user)
    count = ChangeLog.objects.count()

    with pytest.raises(ValueError):
        services.delete_department(dept, user)

    assert ChangeLog.objects.count() == count


@pytest.mark.django_db
def test_accounts_stay_in_access_log_only(user, dept):
    """アカウントの発行と変更は AccessLog に今までどおり 1 行ずつ増え、変更の履歴は増えない (決め C106-2 = a)。"""
    changes, logs = ChangeLog.objects.count(), AccessLog.objects.count()

    services.issue_account("genba", PASSWORD, [], [], {}, user)
    assert (ChangeLog.objects.count(), AccessLog.objects.count()) == (changes, logs + 1)

    account = get_user_model().objects.get(username="genba")
    services.update_account(account.seizou_access, {"departments": [dept]}, user)
    assert (ChangeLog.objects.count(), AccessLog.objects.count()) == (changes, logs + 2)


def _queries(client, url) -> int:
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200
    return len(ctx.captured_queries)


@pytest.mark.django_db
def test_detail_queries_do_not_grow_with_history_rows(manager_client, user, dept):
    many = services.create_department("検査", ["workorders", "progress"], ["workorders"], user)
    for n in range(10):  # 中身を変えずに、履歴の行だけを増やす
        record_history(many, user, changes=[{"field": "sort_order", "label": "並び順", "type": "number", "before": str(n), "after": str(n + 1)}])
    assert len(_rows(many.pk)) > len(_rows(dept.pk)) + 5

    few_url = reverse("accounts:department_detail", args=[dept.pk])
    many_url = reverse("accounts:department_detail", args=[many.pk])
    for url in (few_url, many_url):  # 最初の 1 回の控えを外す
        assert manager_client.get(url).status_code == 200

    assert _queries(manager_client, many_url) == _queries(manager_client, few_url)
