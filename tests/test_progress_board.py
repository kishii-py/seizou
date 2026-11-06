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
from seizou.progress import boards, services
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
    assert cells == ["3", "10", "7"]
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
    assert re.search(r'data-\[over=true\]:text-error" data-over="true">-2</td>', over_row)
    other_row = _row(body, other_step)
    assert 'data-over="true"' not in other_row
    assert ">0</td>" in other_row  # 残が 0 の工程も出す


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
    assert progress_urls.MENU == [("進捗", "progress:progress_list"), ("実績", "progress:record_list")]


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


# --- 2 段目: 自動の更新と、指示書の詳細の「進捗」のタブ ---

HX = {"HTTP_HX_REQUEST": "true"}


def _poll(client, **params):
    return client.get(reverse("progress:progress_list"), params, **HX)


def _poller_url(body):
    match = re.search(r'<div id="progress-poll" hidden hx-get="([^"]*)"', body)
    assert match is not None, "取りに行く要素がありません"
    return match.group(1)


@pytest.mark.django_db
def test_board_has_poller_every_10s(client_logged_in, issue, cut):
    step = issue(cut).steps.get()
    _record(step, 1)
    newest = _record(step, 2)

    body = _body(_board(client_logged_in))
    assert 'id="progress-poll"' in body
    assert 'hx-trigger="every 10s"' in body
    assert 'hx-swap="none"' in body
    assert _poller_url(body) == f"{reverse('progress:progress_list')}?after={newest.pk}"
    assert "hx-swap-oob" not in body  # 1 枚目は入れ替えの印を付けない


@pytest.mark.django_db
def test_poll_returns_only_changed_rows(client_logged_in, issue, cut, bend):
    workorder = issue(cut, bend)
    changed, other = workorder.steps.order_by("seq")
    _record(changed, 3)
    before = _record(other, 5)  # 前に見た時の一番新しい実績
    added = _record(changed, 4)

    body = _body(_poll(client_logged_in, after=before.pk))
    assert "<html" not in body
    assert "<template" in body
    row = _row(body, changed)
    assert 'hx-swap-oob="true"' in row
    cells = re.findall(r'<td class="whitespace-nowrap text-right tabular-nums[^"]*"[^>]*>([^<]*)</td>', row)
    assert cells == ["3", "10", "7"]  # 合計は前からの 3 を含む
    assert f'id="progress-step-{other.pk}"' not in body
    assert _poller_url(body) == f"{reverse('progress:progress_list')}?after={added.pk}"
    assert re.search(r'<div id="progress-poll"[^>]*hx-swap-oob="true"', body)


@pytest.mark.django_db
def test_poll_without_change_returns_only_poller(client_logged_in, issue, cut):
    step = issue(cut).steps.get()
    newest = _record(step, 3)
    expected = f"{reverse('progress:progress_list')}?after={newest.pk}"

    body = _body(_poll(client_logged_in, after=newest.pk))
    assert "<tr" not in body
    assert _poller_url(body) == expected

    # after が無いか読めない要求は、行を返さず、今の一番新しい実績を印にした取りに行く要素だけを返す。
    for params in ({}, {"after": "abc"}, {"after": "-1"}):
        body = _body(_poll(client_logged_in, **params))
        assert "<tr" not in body
        assert _poller_url(body) == expected


@pytest.mark.django_db
def test_poll_keeps_process(client_logged_in, issue, cut, bend):
    workorder = issue(cut, bend)
    cut_step, bend_step = workorder.steps.order_by("seq")
    before = _record(cut_step, 1)
    _record(cut_step, 2)
    added = _record(bend_step, 3)

    body = _body(_poll(client_logged_in, process=cut.pk, after=before.pk))
    assert f'id="progress-step-{cut_step.pk}"' in body
    assert f'id="progress-step-{bend_step.pk}"' not in body  # ほかの工程の変わった行は返らない
    assert _poller_url(body) == f"{reverse('progress:progress_list')}?process={cut.pk}&amp;after={added.pk}"


@pytest.mark.django_db
def test_full_page_ignores_after(client_logged_in, issue, cut, bend):
    workorder = issue(cut, bend)
    cut_step, bend_step = workorder.steps.order_by("seq")
    newest = _record(cut_step, 2)

    body = _body(_board(client_logged_in, after=newest.pk))
    assert "<html" in body
    assert f'id="progress-step-{cut_step.pk}"' in body
    assert f'id="progress-step-{bend_step.pk}"' in body


@pytest.mark.django_db
def test_record_list_htmx_uses_core_rows(client_logged_in, issue, cut, user):
    step = issue(cut).steps.get()
    record = _record(step, 3, user)

    body = _body(client_logged_in.get(reverse("progress:record_list"), {"q": ""}, **HX))
    assert "<html" not in body
    assert f'id="row-{record.pk}"' in body
    assert "progress-poll" not in body
    assert "progress-step-" not in body


@pytest.mark.django_db
def test_workorder_detail_has_progress_tab(client_logged_in, issue, cut, user):
    workorder = issue(cut)
    step = workorder.steps.get()
    first = _record(step, 3, user)
    _record(step, 4, user)
    first.refresh_from_db()

    assert str(first) == f"{step} 実績 3 (2026-11-03 10:00)"
    assert "object (" not in str(first)

    body = _body(client_logged_in.get(reverse("workorders:workorder_detail", args=[workorder.pk])))
    assert re.search(r'aria-label="進捗">進捗<span class="badge badge-sm tabular-nums">2</span>', body)
    assert str(first) in body


@pytest.mark.django_db
def test_progress_text_drops_trailing_zeros(client_logged_in, issue, cut, bend, user):
    """進捗のタブの実績の字は末尾の 0 を落とす (500.00 → 500・2.50 → 2.5。実績の一覧と進捗の盤と同じ字。C16)。"""
    workorder = issue(cut, bend)
    cut_step, bend_step = workorder.steps.order_by("seq")
    whole = _record(cut_step, 500, user)
    half = _record(bend_step, "2.5", user)
    whole.refresh_from_db()
    half.refresh_from_db()

    assert str(whole) == f"{cut_step} 実績 500 (2026-11-03 10:00)"
    assert str(half) == f"{bend_step} 実績 2.5 (2026-11-03 10:00)"

    body = _body(client_logged_in.get(reverse("workorders:workorder_detail", args=[workorder.pk])))
    assert str(whole) in body and str(half) in body
    assert "500.00" not in body and "2.50" not in body


# --- 3 段目: 列の並びと完了の札 ---

DONE_BADGE = '<span class="badge badge-success ml-2">完了</span>'


def _cells(row):
    """盤の 1 行の欄 (<td> から </td> まで) を左から順に。"""
    return re.findall(r"<td[^>]*>.*?</td>", row, re.S)


@pytest.mark.django_db
def test_board_headers_put_remaining_after_process(client_logged_in, issue, cut):
    """見出しは左から 指示書番号・工程・残・予定の数・実績の合計・部品 (残を工程のすぐ右、長い部品の名前を右端へ。C71)。"""
    issue(cut)

    body = _body(_board(client_logged_in))
    thead = re.search(r"<thead>(.*?)</thead>", body, re.S).group(1)
    headers = re.findall(r'<th class="[^"]*">([^<]*)</th>', thead)
    assert headers == ["指示書番号", "工程", "残", "予定の数", "実績の合計", "部品"]


@pytest.mark.django_db
def test_board_cells_follow_header_order(client_logged_in, issue, cut, user):
    workorder = issue(cut)
    step = workorder.steps.get()
    _record(step, 3, user)

    cells = _cells(_row(_body(_board(client_logged_in)), step))
    assert len(cells) == 6
    assert workorder.wo_no in cells[0]
    assert reverse("workorders:workorder_detail", args=[workorder.pk]) in cells[0]
    assert "1 切断" in cells[1]
    assert cells[2].endswith(">7</td>")
    item_pk = step.workorder.order_line.item.pk
    assert "T-100 作り物の軸" in cells[-1]
    assert reverse("masters:item_modal", args=[item_pk]) in cells[-1]


@pytest.mark.django_db
def test_no_done_badge_while_remaining(client_logged_in, issue, cut, user):
    step = issue(cut).steps.get()
    _record(step, 3, user)

    row = _row(_body(_board(client_logged_in)), step)
    assert "完了" not in row


@pytest.mark.django_db
def test_done_badge_when_remaining_zero_or_below(client_logged_in, issue, cut, bend):
    workorder = issue(cut, bend)
    over_step, even_step = workorder.steps.order_by("seq")
    _record(over_step, 12)  # 残 -2
    _record(even_step, 10)  # 残 0

    body = _body(_board(client_logged_in))
    over_row = _row(body, over_step)
    even_row = _row(body, even_step)
    assert DONE_BADGE in over_row
    assert DONE_BADGE in even_row
    assert 'data-over="true"' in over_row  # 予定を超えた残は今どおり赤
    assert 'data-over="true"' not in even_row


@pytest.mark.django_db
def test_poll_row_has_done_badge(client_logged_in, issue, cut):
    step = issue(cut).steps.get()
    before = _record(step, 3)
    _record(step, 7)

    row = _row(_body(_poll(client_logged_in, after=before.pk)), step)
    assert 'hx-swap-oob="true"' in row
    assert DONE_BADGE in row


@pytest.mark.django_db
def test_build_board_done_key(issue, cut, bend):
    workorder = issue(cut, bend)
    over_step, even_step = workorder.steps.order_by("seq")
    _record(over_step, 12)  # 残 -2
    _record(even_step, 10)  # 残 0
    open_step = issue(cut).steps.get()
    _record(open_step, 7)  # 残 3

    rows = {row["step"]: row for row in boards.build_board(None)["rows"]}
    assert rows[open_step.pk]["remaining"] == Decimal("3")
    assert rows[open_step.pk]["done"] is False
    assert rows[even_step.pk]["done"] is True
    assert rows[over_step.pk]["done"] is True
