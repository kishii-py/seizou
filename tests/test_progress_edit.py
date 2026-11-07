"""実績を直す・消す (候補 C103 の その 2。決め PROG-1d = b・PROG-1e = a)。

直す画面は良品の数・不良の数・完了の印・日時・備考を直し、工程と人は変えない。直すときも R02 を当て、R07 (予定を超えた知らせ) を
直す前の数を除いた合計で見る。消す画面は確かめてから消す。どちらも実績の一覧の行の ⋮ と右クリックから小窓で開く。
権限は実績を入れるのと同じ (誰でも)。在庫は動かさない。
"""

import json
import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse

from seizou.core import access
from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress import services
from seizou.progress.models import Progress
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
FIXED_AT = datetime(2026, 11, 4, 9, 15, tzinfo=ZoneInfo("Asia/Tokyo"))
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
HX = {"HTTP_HX_REQUEST": "true"}
DELETE_WARNING = "この実績を消すと戻せません。工程の合計から、この実績の数だけ減ります。"
MARK_WARNING = "この実績には完了の印が付いています。ほかに印の付いた実績が無ければ、この工程は完了でなくなります。"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def step(user):
    """予定 10 の工程 1 つ (切断) の指示書。"""
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    process = Process.objects.create(code="CUT", name="切断")
    workorder = workorders_services.issue_workorder(
        order.lines.get(), Decimal("10"), [{"process": process, "planned_qty": Decimal("10")}], user
    )
    return workorder.steps.get()


@pytest.fixture
def record(user, step):
    """良品 8・不良 1・印あり の実績。"""
    return services.record_progress(step, Decimal("8"), RECORDED_AT, user, defect_qty=Decimal("1"), completes_step=True, note="入れた")


def _edit_url(record, next_url=None):
    url = reverse("progress:progress_update", args=[record.pk])
    return f"{url}?next={next_url}" if next_url else url


def _delete_url(record, next_url=None):
    url = reverse("progress:progress_delete", args=[record.pk])
    return f"{url}?next={next_url}" if next_url else url


def _edit_data(**values):
    data = {"qty": "8", "defect_qty": "1", "completes_step": "on", "recorded_at": "2026-11-03T10:30", "note": "入れた"}
    data.update(values)
    return {key: value for key, value in data.items() if value is not None}


# ---------------------------------------------------------------- (a) 直す関数


@pytest.mark.django_db
def test_update_progress_changes_five_values(user, step, record):
    other = get_user_model().objects.create_user("other", password="pass-for-tests-only")
    result = services.update_progress(record, Decimal("6"), FIXED_AT, other, defect_qty=Decimal("2"), completes_step=False, note="直した")
    record.refresh_from_db()
    assert (record.qty, record.defect_qty, record.completes_step, record.recorded_at, record.note) == (
        Decimal("6"),
        Decimal("2"),
        False,
        FIXED_AT,
        "直した",
    )
    assert record.workorder_step == step
    assert record.recorded_by == user  # 直した人に替えない
    assert result.saved_message == f"{step.workorder.wo_no} の工程 1 切断 の実績を直しました。"
    assert services.step_completed(step) is False  # 間違えて付けた印を外せる


@pytest.mark.django_db
def test_update_progress_keeps_r02(user, record):
    for qty, defect in ((Decimal("0"), Decimal("0")), (Decimal("-1"), Decimal("5")), (Decimal("1"), Decimal("-1"))):
        with pytest.raises(RuleViolation) as caught:
            services.update_progress(record, qty, FIXED_AT, user, defect_qty=defect)
        assert caught.value.code == "R02"
        assert caught.value.message == services._R02_MESSAGE
    record.refresh_from_db()
    assert (record.qty, record.defect_qty) == (Decimal("8"), Decimal("1"))
    # 良品 0 で不良だけには直せる (C103-Q2 = a)
    services.update_progress(record, Decimal("0"), FIXED_AT, user, defect_qty=Decimal("9"))
    record.refresh_from_db()
    assert (record.qty, record.defect_qty) == (Decimal("0"), Decimal("9"))


# ---------------------------------------------------------------- (b) 消す関数


@pytest.mark.django_db
def test_delete_progress_removes_and_refuses_twice(user, step, record):
    services.record_progress(step, Decimal("2"), RECORDED_AT, user)
    assert services.recorded_qty(step) == Decimal("10")
    stale = Progress.objects.get(pk=record.pk)
    result = services.delete_progress(record, user)
    assert result.saved_message == f"{step.workorder.wo_no} の工程 1 切断 の実績 8 を削除しました。"
    assert not Progress.objects.filter(pk=stale.pk).exists()
    assert services.recorded_qty(step) == Decimal("2")  # 消した数だけ減る
    assert services.step_completed(step) is False  # 印の付いた実績が無くなった

    with pytest.raises(ValueError) as caught:
        services.delete_progress(stale, user)
    assert str(caught.value) == "削除できません。この実績は既に消してあります。"


# ---------------------------------------------------------------- (c) 直す画面


@pytest.mark.django_db
def test_edit_modal_shows_current_values(client_logged_in, step, record):
    response = client_logged_in.get(_edit_url(record), **MODAL)
    assert response.status_code == 200
    body = response.content.decode()
    assert "<html" not in body
    assert 'name="workorder_step"' not in body  # 工程は直せない
    assert re.search(r'<input[^>]*name="qty"[^>]*value="8', body)
    assert re.search(r'<input[^>]*name="defect_qty"[^>]*value="1', body)
    assert 'value="2026-11-03T10:30"' in body
    completes = re.search(r'<input[^>]*id="id_completes_step"[^>]*>', body).group(0)
    assert "checked" in completes  # 印の付いた実績なら四角に印
    assert f"{step} の実績を直す" in body  # どの工程の実績かは保存のボタンの字
    assert "progress/progress-form.js" not in body


@pytest.mark.django_db
def test_edit_modal_post_returns_204(client_logged_in, step, record):
    response = client_logged_in.post(_edit_url(record), _edit_data(qty="7", completes_step=None), **MODAL)
    assert response.status_code == 204
    saved = json.loads(response["HX-Trigger"])["seizou:saved"]
    assert saved == {"message": f"{step.workorder.wo_no} の工程 1 切断 の実績を直しました。", "level": "success"}
    record.refresh_from_db()
    assert (record.qty, record.completes_step) == (Decimal("7"), False)


@pytest.mark.django_db
def test_edit_full_page_returns_to_next(client_logged_in, record):
    listing = reverse("progress:record_list")
    response = client_logged_in.post(_edit_url(record), _edit_data(note="直した", next=listing))
    assert response.status_code == 302
    assert response["Location"] == listing
    record.refresh_from_db()
    assert record.note == "直した"


@pytest.mark.django_db
def test_edit_screen_links_to_delete(client_logged_in, record):
    listing = reverse("progress:record_list")
    delete_url = f"{reverse('progress:progress_delete', args=[record.pk])}?next=/progress/records/"
    page = client_logged_in.get(_edit_url(record, listing)).content.decode()
    assert f'href="{delete_url}"' in page
    modal = client_logged_in.get(_edit_url(record, listing), **MODAL).content.decode()
    assert f'href="{delete_url}" data-modal-form="{delete_url}"' in modal


@pytest.mark.django_db
def test_edit_zero_zero_is_r02(client_logged_in, record):
    response = client_logged_in.post(_edit_url(record), _edit_data(qty="0", defect_qty=""))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body and "R02" in body
    record.refresh_from_db()
    assert record.qty == Decimal("8")


# ---------------------------------------------------------------- (d) 消す画面


@pytest.mark.django_db
def test_delete_modal_confirms_then_deletes(client_logged_in, step, record):
    response = client_logged_in.get(_delete_url(record), **MODAL)
    assert response.status_code == 200
    body = response.content.decode()
    assert DELETE_WARNING in body
    assert MARK_WARNING in body
    assert "btn btn-error" in body and "削除" in body

    deleted = client_logged_in.post(_delete_url(record), {}, **MODAL)
    assert deleted.status_code == 204
    saved = json.loads(deleted["HX-Trigger"])["seizou:saved"]
    assert saved == {"message": f"{step.workorder.wo_no} の工程 1 切断 の実績 8 を削除しました。", "level": "success"}
    assert Progress.objects.count() == 0


@pytest.mark.django_db
def test_delete_page_without_mark_and_back_to_next(client_logged_in, user, step):
    plain = services.record_progress(step, Decimal("3"), RECORDED_AT, user)
    body = client_logged_in.get(_delete_url(plain)).content.decode()
    assert DELETE_WARNING in body
    assert MARK_WARNING not in body
    listing = reverse("progress:record_list")
    response = client_logged_in.post(_delete_url(plain), {"next": listing})
    assert response.status_code == 302
    assert response["Location"] == listing
    assert [str(m) for m in get_messages(response.wsgi_request)] == [f"{step.workorder.wo_no} の工程 1 切断 の実績 3 を削除しました。"]
    assert Progress.objects.count() == 0


# ---------------------------------------------------------------- (e) 実績の一覧の行の ⋮ と右クリック


def _row_menu_links(body, record):
    row = re.search(rf'<tr id="row-{record.pk}"[^>]*>(.*?)</tr>', body, re.S)
    assert row, "実績の行が無い"
    assert "data-row-menu" in re.search(rf'<tr id="row-{record.pk}"[^>]*>', body).group(0)
    assert "row-menu" in row.group(1)
    return dict(
        (label, (href, modal))
        for href, modal, label in re.findall(r'<a[^>]*href="([^"]*)"[^>]*data-modal-form="([^"]*)"[^>]*>.*?(編集|削除)</a>', row.group(1), re.S)
    )


@pytest.mark.django_db
def test_record_list_rows_have_menu(client_logged_in, record):
    url = reverse("progress:record_list")
    for response in (client_logged_in.get(url), client_logged_in.get(url, {"q": ""}, **HX)):
        body = response.content.decode()
        links = _row_menu_links(body, record)
        assert set(links) == {"編集", "削除"}
        for label, path in (("編集", reverse("progress:progress_update", args=[record.pk])), ("削除", reverse("progress:progress_delete", args=[record.pk]))):
            href, modal = links[label]
            assert href == modal
            assert href.startswith(f"{path}?next=/progress/records/")
        headers = re.findall(r"<th[^>]*>\s*(.*?)\s*</th>", re.search(r"<thead>(.*?)</thead>", body, re.S).group(1), re.S)
        assert re.sub(r"<[^>]+>", "", headers[-1]).strip() == "操作"


# ---------------------------------------------------------------- (f) R07 を直す画面にも掛ける


@pytest.mark.django_db
def test_r07_on_edit_counts_without_the_old_qty(settings, client_logged_in, step, record):
    settings.SEIZOU = {"over_workorder": "warn"}
    over = client_logged_in.post(_edit_url(record), _edit_data(qty="12"))
    assert over.status_code == 200
    body = over.content.decode()
    assert 'data-notice="check"' in body and "R07" in body and "このまま保存" in body
    assert "この実績に入れられる数は 10 までです。" in body
    record.refresh_from_db()
    assert record.qty == Decimal("8")

    assert client_logged_in.post(_edit_url(record), _edit_data(qty="9")).status_code == 302  # 直す前の 8 を引く
    record.refresh_from_db()
    assert record.qty == Decimal("9")
    assert client_logged_in.post(_edit_url(record), _edit_data(qty="9", defect_qty="50")).status_code == 302  # 不良は比べない

    confirmed = client_logged_in.post(_edit_url(record), _edit_data(qty="12", confirmed="1"))
    assert confirmed.status_code == 302
    record.refresh_from_db()
    assert record.qty == Decimal("12")


# ---------------------------------------------------------------- (g) 権限


@pytest.mark.django_db
def test_anyone_can_edit_and_delete(settings, client, step):
    settings.SEIZOU = {"access_control": {"department": "off", "write": "on", "money": "off"}}
    plain = get_user_model().objects.create_user("plain", password="pass-for-tests-only")
    for action in ("progress.update_progress", "progress.delete_progress"):
        assert action in access.FREE_ACTIONS
        access.require_write(plain, action)
    client.force_login(plain)
    record = services.record_progress(step, Decimal("4"), RECORDED_AT, plain)
    assert client.post(_edit_url(record), _edit_data(qty="5", defect_qty="", completes_step=None)).status_code == 302
    assert client.post(_delete_url(record), {}).status_code == 302
    assert Progress.objects.count() == 0
