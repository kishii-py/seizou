"""指示書の一覧の製造向けの欄を、土台の絞り込みの枠の中へ移した形 (N128。本人の決め 11-04 17:3x)。

未着手だけ (f_unaccepted) と今日の計画だけ (f_today) は N122 の印 (flag)。工程 (f_process) は N119 の lookup。
古い別の form と古い GET (process・unaccepted・today) は無い。名前は作り物だけ。
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.workorders import services as workorders_services

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def board(user):
    """指示書 4 つ。発行日は全部今日なので、一覧の並びは作った逆の順 (cancelled → later → accepted → waiting)。

    waiting: 切断 → 曲げ。未着手。2 つの工程とも今日を含む予定 (今日の計画の join が 2 行になる)。
    accepted: 切断。受けた。今日の予定。
    later: 曲げ。未着手。予定は来週。
    cancelled: 切断。取り消した (受けていない)。予定なし。
    """
    today = timezone.localdate()
    cut = masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)
    bend = masters_services.create_process({"code": "BEND", "name": "曲げ", "sort_order": 2}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("50"), "unit_price": Decimal("100")}],
        user,
        order_date=today - timedelta(days=1),
    )
    line = order.lines.get()

    def issue(*processes):
        steps = [{"process": process, "planned_qty": Decimal("2")} for process in processes]
        return workorders_services.issue_workorder(line, Decimal("2"), steps, user)

    waiting, accepted, later, cancelled = issue(cut, bend), issue(cut), issue(bend), issue(cut)
    for step in waiting.steps.all():
        workorders_services.plan_step(step, today - timedelta(days=1), today + timedelta(days=1), user)
    workorders_services.accept_workorder(accepted, user)
    workorders_services.plan_step(accepted.steps.get(), today, today, user)
    workorders_services.plan_step(later.steps.get(), today + timedelta(days=7), today + timedelta(days=8), user)
    workorders_services.cancel_workorder(cancelled, user)
    return {"waiting": waiting, "accepted": accepted, "later": later, "cancelled": cancelled, "cut": cut, "bend": bend}


def listed(response) -> list:
    return [row["obj"].pk for row in response.context["rows"]]


def everything(board) -> list:
    return [board[name].pk for name in ("cancelled", "later", "accepted", "waiting")]


@pytest.mark.django_db
def test_unaccepted_flag_lists_issued_workorders_not_yet_accepted(client_logged_in, board):
    waiting, accepted, later, cancelled = (board[name] for name in ("waiting", "accepted", "later", "cancelled"))
    url = reverse("workorders:workorder_list")

    # 既定は切 (鍵が無いときと 0 は絞らない)。
    assert listed(client_logged_in.get(url)) == everything(board)
    assert listed(client_logged_in.get(url, {"f_unaccepted": "0"})) == everything(board)
    for headers in ({}, HTMX):
        response = client_logged_in.get(url, {"f_unaccepted": "1"}, **headers)
        assert listed(response) == [later.pk, waiting.pk], headers
        assert response.context["paginator"].count == 2, headers
    # 枠の欄から送ると、入のときは隠しの 0 とチェックの 1 が両方届く (後の 1 を読む。N122)。
    response = client_logged_in.get(url, {"f_unaccepted": ["0", "1"]})
    assert listed(response) == [later.pk, waiting.pk]
    assert response.context["filter_active"] is True
    csv_body = client_logged_in.get(url, {"f_unaccepted": "1", "format": "csv"}).content.decode()
    assert later.wo_no in csv_body and waiting.wo_no in csv_body
    assert accepted.wo_no not in csv_body and cancelled.wo_no not in csv_body


@pytest.mark.django_db
def test_today_flag_lists_each_workorder_once(client_logged_in, board):
    waiting, accepted = board["waiting"], board["accepted"]
    url = reverse("workorders:workorder_list")

    # waiting は今日を含む予定の工程を 2 つ持つ。工程の行の join で 2 行になっても、指示書は 1 回だけ出る。
    for headers in ({}, HTMX):
        response = client_logged_in.get(url, {"f_today": "1"}, **headers)
        assert listed(response) == [accepted.pk, waiting.pk], headers
        assert response.context["paginator"].count == 2, headers
    assert listed(client_logged_in.get(url, {"f_today": "1", "f_unaccepted": "1"})) == [waiting.pk]
    assert listed(client_logged_in.get(url, {"f_today": "0"})) == everything(board)


@pytest.mark.django_db
def test_flags_combine_with_process(client_logged_in, board):
    waiting, accepted, later = board["waiting"], board["accepted"], board["later"]
    cut, bend = board["cut"], board["bend"]
    url = reverse("workorders:workorder_list")

    # 工程と印はかけ合わせる (AND)。工程の行の join が 2 つ重なっても、指示書は 1 回だけ出る。
    response = client_logged_in.get(url, {"f_process": str(bend.pk), "f_today": "1"})
    assert listed(response) == [waiting.pk]
    assert response.context["paginator"].count == 1
    assert listed(client_logged_in.get(url, {"f_process": str(cut.pk), "f_today": "1"})) == [accepted.pk, waiting.pk]
    assert listed(client_logged_in.get(url, {"f_process": str(cut.pk), "f_unaccepted": "1"})) == [waiting.pk]
    response = client_logged_in.get(url, {"f_process": str(bend.pk), "f_unaccepted": "1"})
    assert listed(response) == [later.pk, waiting.pk]


@pytest.mark.django_db
def test_old_form_and_old_get_are_gone(client_logged_in, board):
    url = reverse("workorders:workorder_list")

    body = client_logged_in.get(url).content.decode()
    assert 'id="workorder-filter"' not in body
    assert 'aria-label="製造向けの絞り込み"' not in body
    # 2 つの印は、絞り込みの枠の中で発行日の後に並ぶ (隠しの 0 の後にチェックの 1)。
    frame = body[body.index('id="list-q"') : body.index('id="list-per"')]
    for name in ("f_unaccepted", "f_today"):
        assert f'<input type="hidden" name="{name}" value="0">' in frame
    assert frame.index('name="f_issued_to"') < frame.index('name="f_unaccepted"') < frame.index('name="f_today"')
    # 古い GET (process・unaccepted・today) は読まない (N128。別名を足さない)。
    for params in ({"process": board["cut"].pk}, {"unaccepted": "1"}, {"today": "1"}):
        response = client_logged_in.get(url, params)
        assert listed(response) == everything(board), params
        assert response.context["filter_active"] is False, params
