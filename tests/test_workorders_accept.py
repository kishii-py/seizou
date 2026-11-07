"""指示書の着手のボタンと印が無いこと (初見テスト R5 の候補 C27。状態は実績から出す) と、指示書の一覧の製造向けの絞り込み。

「未着手だけ」は実績の無い発行の指示書。一覧の「状態」の列は実績から出す字 (未着手・着手済・完了・取消)。
受注・指示書・予定は services で作る。作り物のデータだけを使う。
"""

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.progress import services as progress_services
from seizou.workorders import services

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


# --- 着手のボタンと口は無い (初見テスト R5 の候補 C27。本人の決め WO-1 = b・WO-2 = b) ---


@pytest.mark.django_db
def test_detail_has_no_accept_button_and_url_is_gone(client_logged_in, workorder):
    detail_url = reverse("workorders:workorder_detail", args=[workorder.pk])

    body = client_logged_in.get(detail_url).content.decode()
    assert "着手</button>" not in body
    assert f"/workorders/{workorder.pk}/accept/" not in body
    # 頭の右の「着手済 日時 人」の印も無い (状態は題の横の印)
    assert re.search(r"着手済 \d{4}-\d{2}-\d{2}", body) is None

    assert client_logged_in.post(f"/workorders/{workorder.pk}/accept/").status_code == 404


# --- 一覧の列と絞り込み ---


@pytest.mark.django_db
def test_list_column_shows_progress_state(user, client_logged_in, customer, item, cut):
    """一覧に「着手日時」の列は無く、「状態」の列が実績から出す字 (未着手・着手済) になる。CSV も同じ。"""
    line = _line(user, customer, item)
    waiting = _issue(user, line, cut, qty="4")
    started = _issue(user, line, cut, qty="6")
    progress_services.record_progress(started.steps.get(), Decimal("1"), timezone.now(), user)

    response = client_logged_in.get(reverse("workorders:workorder_list"))
    assert response.status_code == 200
    labels = [col.label for col in response.context["columns"]]
    assert "着手日時" not in labels
    # 「状態」の右に「工程の進み」の列 (本人の決め 11-07 18:2x〜18:4x。tests/test_workorders_list_progress.py)
    assert labels[-2:] == ["状態", "工程の進み"]
    index = labels.index("状態")
    values = {row["obj"].pk: row["cells"][index]["value"] for row in response.context["rows"]}
    assert values == {waiting.pk: "未着手", started.pk: "着手済"}

    csv_body = client_logged_in.get(reverse("workorders:workorder_list"), {"format": "csv"}).content.decode()
    header, *rows = csv_body.lstrip("\ufeff").splitlines()
    assert "着手日時" not in header
    assert header.split(",")[-2:] == ["状態", "工程の進み"]
    assert sorted(row.split(",")[-2] for row in rows) == ["未着手", "着手済"]


@pytest.mark.django_db
def test_list_order_line_cell_shows_order_no_and_line_no_only(user, client_logged_in, customer, item, cut):
    """一覧の「受注の明細」の欄の字は「受注番号-行番号」だけで、title に今の全文 (初見テスト R5 の候補 C34 の本 A)。
    context の値と CSV は今のまま全文。部品の品番と品名は同じ行の「部品」の欄にある。"""
    line = _line(user, customer, item)
    workorder = _issue(user, line, cut)
    full = str(line)
    short = f"{line.order.order_no}-{line.line_no}"
    assert full != short and item.part_no in full  # 今の全文は品番と品名を含む

    url = reverse("workorders:workorder_list")
    for headers in ({}, {"HTTP_HX_REQUEST": "true"}):
        response = client_logged_in.get(url, **headers)
        assert response.status_code == 200, headers
        names = [col.name for col in response.context["columns"]]
        index = names.index("order_line")
        assert [str(row["cells"][index]["value"]) for row in response.context["rows"]] == [full], headers

        body = response.content.decode()
        start = body.index(f'<tr id="row-{workorder.pk}"')
        cells = re.findall(r"<td\b.*?</td>", body[start : body.index("</tr>", start)], re.S)
        assert len(cells) == len(names), headers
        cell = cells[index]
        span = re.search(r'<span class="block max-w-xs truncate" title="([^"]*)">([^<]*)</span>', cell)
        assert span is not None, cell
        assert span.group(1) == full, headers
        assert span.group(2) == short, headers
        assert item.part_no not in span.group(2) and item.name not in span.group(2), headers
        # 部品の欄には品番と品名がある (行の全体で「品番が無い」は見ない)
        assert item.name in cells[names.index("order_line__item")], headers

    csv_body = client_logged_in.get(url, {"format": "csv"}).content.decode()
    header, *rows = csv_body.lstrip("\ufeff").splitlines()
    assert len(rows) == 1
    assert rows[0].split(",")[header.split(",").index("受注の明細")] == full


@pytest.mark.django_db
def test_list_filters_process_unaccepted_today(user, client_logged_in, customer, item, cut, bend):
    today = timezone.localdate()
    line = _line(user, customer, item)
    # 1 枚目: 切断と曲げ。実績あり (着手済)。曲げの予定は来週 (今日の計画ではない)。
    first = _issue(user, line, cut, bend, qty="4")
    progress_services.record_progress(first.steps.get(seq=1), Decimal("1"), timezone.now(), user)
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
def test_detail_and_modal_have_no_accepted_fields(user, client_logged_in, workorder):
    detail = client_logged_in.get(reverse("workorders:workorder_detail", args=[workorder.pk]))
    labels = [field["label"] for field in detail.context["fields"]]
    assert "着手日時" not in labels and "着手者" not in labels

    modal = client_logged_in.get(
        reverse("workorders:workorder_modal", args=[workorder.pk]), HTTP_HX_REQUEST="true"
    )
    assert modal.status_code == 200
    body = modal.content.decode()
    assert "着手日時" not in body and "着手者" not in body
    labels = [field["label"] for field in modal.context["fields"]]
    assert "着手日時" not in labels and "着手者" not in labels


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
