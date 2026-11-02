"""進捗の盤 (02 の進捗の一覧「指示書ごとの工程の進み (予定・実績・残)。自動で更新」と 06 の E-11)。

盤の行は指示書の工程 1 つ。実績の入力 (services.record_progress) は今のまま。
今の実績の記録の一覧は progress:record_list (/progress/records/) へ移した。
"""

import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.shortcuts import resolve_url
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress import services
from seizou.progress import urls as progress_urls
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="切断", sort_order=1)


@pytest.fixture
def bend(db):
    return Process.objects.create(code="BND", name="曲げ", sort_order=2)


@pytest.fixture
def issue(user):
    """作り物の受注を 1 つ作り、その明細に工程の並びの指示書を発行する関数 (返す物は指示書)。"""
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )
    item = masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user
    )

    def _issue(*processes, planned=Decimal("10")):
        order = orders_services.create_order(
            customer,
            [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
            user,
            order_date=ORDER_DATE,
        )
        return workorders_services.issue_workorder(
            order.lines.get(),
            Decimal("10"),
            [{"process": process, "planned_qty": planned} for process in processes],
            user,
        )

    return _issue


def _record(step, qty, user=None):
    return services.record_progress(step, Decimal(str(qty)), RECORDED_AT, user)


def _body(response):
    assert response.status_code == 200
    return response.content.decode()


def _row(body, step):
    """盤の 1 行 (<tr id="progress-step-<pk>" から </tr> まで)。"""
    match = re.search(rf'<tr id="progress-step-{step.pk}".*?</tr>', body, re.S)
    assert match is not None, f"progress-step-{step.pk} の行がありません"
    return match.group(0)


def _board(client, **params):
    return client.get(reverse("progress:progress_list"), params)


# --- 1 段目: 盤の画面と URL の付け替え ---


@pytest.mark.django_db
def test_board_requires_login(client):
    response = _board(client)
    assert response.status_code == 302
    assert response["Location"].startswith(resolve_url(settings.LOGIN_URL))


@pytest.mark.django_db
def test_board_shows_planned_recorded_remaining(client_logged_in, issue, cut, user):
    workorder = issue(cut)
    step = workorder.steps.get()
    _record(step, 3, user)
    _record(step, 4, user)

    body = _body(_board(client_logged_in))
    row = _row(body, step)
    cells = re.findall(r'<td class="whitespace-nowrap text-right tabular-nums[^"]*"[^>]*>([^<]*)</td>', row)
    assert cells == ["10.00", "7.00", "3.00"]
    assert reverse("workorders:workorder_detail", args=[workorder.pk]) in row
    assert workorder.wo_no in row
    assert "T-100 作り物の軸" in row and reverse("masters:item_modal", args=[step.workorder.order_line.item.pk]) in row
    assert "1 切断" in row
    assert 'data-over="true"' not in row


@pytest.mark.django_db
def test_negative_remaining_is_red(client_logged_in, issue, cut, bend):
    workorder = issue(cut, bend)
    over_step, other_step = workorder.steps.order_by("seq")
    _record(over_step, 12)  # services は止めない (予定を超えた実数もそのまま記録する)
    _record(other_step, 10)

    body = _body(_board(client_logged_in))
    over_row = _row(body, over_step)
    assert 'data-over="true"' in over_row
    assert re.search(r'data-\[over=true\]:text-error" data-over="true">-2\.00</td>', over_row)
    other_row = _row(body, other_step)
    assert 'data-over="true"' not in other_row
    assert ">0.00</td>" in other_row  # 残が 0 の工程も出す


@pytest.mark.django_db
def test_cancelled_order_is_not_on_board(client_logged_in, issue, cut, user):
    kept = issue(cut).steps.get()
    cancelled_workorder = issue(cut)
    cancelled = cancelled_workorder.steps.get()
    orders_services.cancel_order(cancelled_workorder.order_line.order, user)

    body = _body(_board(client_logged_in))
    assert f'id="progress-step-{kept.pk}"' in body
    assert f'id="progress-step-{cancelled.pk}"' not in body


@pytest.mark.django_db
def test_pill_filters_by_process(client_logged_in, issue, cut, bend):
    workorder = issue(cut, bend)
    cut_step, bend_step = workorder.steps.order_by("seq")
    second_cut = issue(cut).steps.get()

    whole = _body(_board(client_logged_in))
    assert 'aria-current="page">すべて</a>' in whole
    assert whole.count(">切断</a>") == 1  # 同じ工程の予定が 2 つでも pill は 1 つ
    assert whole.index(">切断</a>") < whole.index(">曲げ</a>")  # 工程の並び順
    for step in (cut_step, bend_step, second_cut):
        assert f'id="progress-step-{step.pk}"' in whole

    body = _body(_board(client_logged_in, process=cut.pk))
    assert f'id="progress-step-{cut_step.pk}"' in body
    assert f'id="progress-step-{second_cut.pk}"' in body
    assert f'id="progress-step-{bend_step.pk}"' not in body
    assert ">すべて</a>" in body and ">切断</a>" in body and ">曲げ</a>" in body
    assert 'aria-current="page">切断</a>' in body
    assert 'aria-current="page">すべて</a>' not in body
    assert 'aria-current="page">曲げ</a>' not in body
    assert f'href="{reverse("progress:progress_list")}?process={bend.pk}"' in body


@pytest.mark.django_db
def test_unknown_process_shows_all(client_logged_in, issue, cut, bend):
    workorder = issue(cut)
    step = workorder.steps.get()
    unused = Process.objects.create(code="PNT", name="塗装", sort_order=3)  # 盤に予定の無い工程

    for raw in ("999", "abc", "0", "-1", str(unused.pk)):
        body = _body(_board(client_logged_in, process=raw))
        assert f'id="progress-step-{step.pk}"' in body
        assert 'aria-current="page">すべて</a>' in body
        assert ">塗装</a>" not in body and ">曲げ</a>" not in body


@pytest.mark.django_db
def test_board_links_create_and_record_list(client_logged_in):
    body = _body(_board(client_logged_in))
    assert f'href="{reverse("progress:progress_create")}"' in body and "新規登録" in body
    assert f'href="{reverse("progress:record_list")}"' in body and "実績の一覧" in body


def test_urls_and_menu():
    assert reverse("progress:progress_list") == "/progress/"
    assert reverse("progress:record_list") == "/progress/records/"
    assert reverse("progress:progress_create") == "/progress/new/"
    assert progress_urls.MENU == [("進捗", "progress:progress_list")]


@pytest.mark.django_db
def test_record_list_keeps_records(client_logged_in, issue, cut, user):
    step = issue(cut).steps.get()
    record = _record(step, 3, user)

    body = _body(client_logged_in.get(reverse("progress:record_list")))
    assert f'id="row-{record.pk}"' in body
    assert ">人<" in body
    assert "実績の一覧" in body


@pytest.mark.django_db
def test_empty_board_sentence(client_logged_in):
    body = _body(_board(client_logged_in))
    assert "指示書を発行すると、工程ごとの予定・実績・残がここに並びます。" in body
    assert "<table" not in body
