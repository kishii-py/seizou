"""指示書の「受けた」(製造部の着手の印) と、指示書の一覧の製造向けの絞り込み。

受けた印を書くのは services.accept_workorder だけ。画面の口は関数の view (POST だけ)。
受注・指示書・予定は services で作る。作り物のデータだけを使う。
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.workorders import services
from seizou.workorders.models import WorkOrder

ORDER_DATE_OFFSET = 1


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user("tester2", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def cut(user):
    return masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)


@pytest.fixture
def bend(user):
    return masters_services.create_process({"code": "BEND", "name": "曲げ", "sort_order": 2}, user)


def _line(user, customer, item):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=timezone.localdate() - timedelta(days=ORDER_DATE_OFFSET),
    )
    return order.lines.get()


def _issue(user, line, *processes, qty="10"):
    return services.issue_workorder(
        line, Decimal(qty), [{"process": p, "planned_qty": Decimal(qty)} for p in processes], user
    )


@pytest.fixture
def workorder(user, customer, item, cut):
    return _issue(user, _line(user, customer, item), cut)


def _row_objects(response):
    return [row["obj"] for row in response.context["rows"]]


# --- services.accept_workorder ---


@pytest.mark.django_db
def test_accept_sets_time_and_user_once(user, other_user, workorder):
    before = timezone.now()
    first = services.accept_workorder(workorder, user)
    assert first.accepted_at is not None and first.accepted_at >= before
    assert first.accepted_by == user

    second = services.accept_workorder(WorkOrder.objects.get(pk=workorder.pk), other_user)
    stored = WorkOrder.objects.get(pk=workorder.pk)
    assert second.accepted_at == first.accepted_at == stored.accepted_at
    assert stored.accepted_by == user


@pytest.mark.django_db
def test_accept_refuses_cancelled(user, workorder):
    services.cancel_workorder(workorder, user, reason="作り物の取消")

    with pytest.raises(ValueError, match="取り消した指示書"):
        services.accept_workorder(workorder, user)

    stored = WorkOrder.objects.get(pk=workorder.pk)
    assert stored.accepted_at is None and stored.accepted_by is None


# --- 受ける口と詳細のボタン ---


@pytest.mark.django_db
def test_accept_button_and_post(client, user, client_logged_in, workorder):
    detail_url = reverse("workorders:workorder_detail", args=[workorder.pk])
    accept_url = reverse("workorders:workorder_accept", args=[workorder.pk])
    assert accept_url == f"/workorders/{workorder.pk}/accept/"

    body = client_logged_in.get(detail_url).content.decode()
    assert f'<form method="post" action="{accept_url}">' in body
    assert "着手</button>" in body

    response = client_logged_in.post(accept_url)
    assert response.status_code == 302
    assert response["Location"] == detail_url

    stored = WorkOrder.objects.get(pk=workorder.pk)
    assert stored.accepted_by == user
    body = client_logged_in.get(detail_url).content.decode()
    assert "badge-success" in body
    assert stored.accepted_label() in body
    assert str(user) in body
    assert f'action="{accept_url}"' not in body
    assert "受けました。" in body

    assert client_logged_in.get(accept_url).status_code == 405

    client.logout()
    anonymous = client.post(accept_url)
    assert anonymous.status_code == 302
    assert reverse("core:login") in anonymous["Location"]


@pytest.mark.django_db
def test_accept_cancelled_from_screen_is_error_toast(user, client_logged_in, workorder):
    services.cancel_workorder(workorder, user, reason="作り物の取消")
    detail_url = reverse("workorders:workorder_detail", args=[workorder.pk])
    accept_url = reverse("workorders:workorder_accept", args=[workorder.pk])

    # 取消の指示書の詳細には、着手のボタンも印も出ない。
    assert f'action="{accept_url}"' not in client_logged_in.get(detail_url).content.decode()

    response = client_logged_in.post(accept_url)
    assert response.status_code == 302
    assert response["Location"] == detail_url
    body = client_logged_in.get(detail_url).content.decode()
    assert "失敗しました" in body
    assert "取り消した指示書です。" in body
    stored = WorkOrder.objects.get(pk=workorder.pk)
    assert stored.accepted_at is None and stored.accepted_by is None


# --- 一覧の列と絞り込み ---


@pytest.mark.django_db
def test_list_column_shows_accepted(user, client_logged_in, customer, item, cut):
    line = _line(user, customer, item)
    waiting = _issue(user, line, cut, qty="4")
    accepted = services.accept_workorder(_issue(user, line, cut, qty="6"), user)

    response = client_logged_in.get(reverse("workorders:workorder_list"))
    assert response.status_code == 200
    labels = [col.label for col in response.context["columns"]]
    assert labels[labels.index("状態") + 1] == "着手日時"
    index = labels.index("着手日時")
    values = {row["obj"].pk: row["cells"][index]["value"] for row in response.context["rows"]}
    assert values[waiting.pk] == "未"
    assert values[accepted.pk] == timezone.localtime(accepted.accepted_at).strftime("%Y-%m-%d %H:%M")
    body = response.content.decode()
    assert values[accepted.pk] in body

    csv_body = client_logged_in.get(reverse("workorders:workorder_list"), {"format": "csv"}).content.decode()
    header, *rows = csv_body.lstrip("﻿").splitlines()
    assert header.split(",")[-1] == "着手日時"
    assert any(row.endswith(",未") for row in rows)


@pytest.mark.django_db
def test_list_filters_process_unaccepted_today(user, client_logged_in, customer, item, cut, bend):
    today = timezone.localdate()
    line = _line(user, customer, item)
    # 1 枚目: 切断と曲げ。受けた。曲げの予定は来週 (今日の計画ではない)。
    first = _issue(user, line, cut, bend, qty="4")
    services.accept_workorder(first, user)
    services.plan_step(first.steps.get(seq=2), today + timedelta(days=7), today + timedelta(days=8), user)
    # 2 枚目: 曲げだけ。未着手。今日を含む予定。
    second = _issue(user, line, bend, qty="6")
    services.plan_step(second.steps.get(), today - timedelta(days=1), today + timedelta(days=1), user)

    url = reverse("workorders:workorder_list")
    cases = [
        ({}, [first, second]),
        ({"f_process": cut.pk}, [first]),
        ({"f_process": bend.pk}, [first, second]),
        ({"f_unaccepted": "1"}, [second]),
        ({"f_today": "1"}, [second]),
        ({"f_process": cut.pk, "f_unaccepted": "1"}, []),
        ({"f_process": "abc"}, [first, second]),
        ({"f_process": "0"}, [first, second]),
    ]
    for params, expected in cases:
        for headers in ({}, {"HTTP_HX_REQUEST": "true"}):
            response = client_logged_in.get(url, params, **headers)
            assert response.status_code == 200, (params, headers)
            rows = _row_objects(response)
            # 工程の join で行が重ならない。
            assert len(rows) == len({row.pk for row in rows}), (params, headers)
            assert {row.pk for row in rows} == {w.pk for w in expected}, (params, headers)


@pytest.mark.django_db
def test_detail_and_modal_show_accepted_fields(user, client_logged_in, workorder):
    services.accept_workorder(workorder, user)

    detail = client_logged_in.get(reverse("workorders:workorder_detail", args=[workorder.pk])).content.decode()
    assert "着手日時" in detail and "着手者" in detail

    modal = client_logged_in.get(
        reverse("workorders:workorder_modal", args=[workorder.pk]), HTTP_HX_REQUEST="true"
    )
    assert modal.status_code == 200
    body = modal.content.decode()
    assert "着手日時" in body and "着手者" in body
    assert [field["label"] for field in modal.context["fields"]][-2:] == ["着手日時", "着手者"]


# --- 2 段目: 一覧の絞り込みの欄 (N128 から土台の絞り込みの枠の中) ---


@pytest.mark.django_db
def test_list_filter_form(user, client_logged_in, customer, item, cut, bend):
    _issue(user, _line(user, customer, item), cut)
    url = reverse("workorders:workorder_list")

    body = client_logged_in.get(url).content.decode()
    assert 'id="workorder-filter"' not in body
    # 幅は候補と「すべて」のうちいちばん長い字 (「すべて」の 6。「切断」と「曲げ」は 4) で決める (候補 C31 の 3)
    assert (
        '<select id="list-f-process" style="width: calc(6ch + 2.75rem); max-width: 100%" class="select select-sm mt-2 w-auto" name="f_process" data-list-filter>'
        in body
    )
    assert '<option value="">すべて</option>' in body
    assert f'<option value="{cut.pk}">{cut.name}</option>' in body  # 工程の候補の字は名前だけ (N142)
    assert f'<option value="{bend.pk}">{bend.name}</option>' in body
    assert body.index(f">{cut.name}<") < body.index(f">{bend.name}<")
    assert 'name="f_unaccepted" value="1" class="checkbox checkbox-sm" data-list-filter>未着手だけ</label>' in body
    assert 'name="f_today" value="1" class="checkbox checkbox-sm" data-list-filter>今日の計画だけ</label>' in body
    assert 'name="process"' not in body and 'name="unaccepted"' not in body and 'name="today"' not in body
    assert 'hx-include="#workorder-filter"' not in body

    chosen = client_logged_in.get(url, {"f_process": cut.pk, "f_unaccepted": "1"}).content.decode()
    assert f'<option value="{cut.pk}" selected>{cut.name}</option>' in chosen
    assert f'<option value="{bend.pk}">{bend.name}</option>' in chosen
    assert 'name="f_unaccepted" value="1" class="checkbox checkbox-sm" checked data-list-filter>' in chosen
    assert 'name="f_today" value="1" class="checkbox checkbox-sm" data-list-filter>' in chosen
