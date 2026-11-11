"""実績の「不良の数」と「この工程を完了にする」の印 (候補 C103 の その 1。決め C29-2 = b・PROG-1・PROG-1b = a・PROG-1c = a・C103-Q2 = a)。

実績の数 (qty) は良品の数のまま。不良の数は defect_qty に分けて持ち、印 (completes_step) は人が付ける。
良品 0 で不良だけの実績も記録できる (R02 の実績の形 = どちらも 0 以上で、合わせて 0 より大きい)。
印はいつも空で始まり、印の付いた工程を選ぶと「指示書の工程」の欄の下に字が出る (断らない)。
工程と指示書の完了の決め方は、この試験の範囲では変えない (その 3)。
"""

import csv
import io
import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db import IntegrityError, transaction
from django.urls import reverse

from seizou.core.exceptions import RuleViolation
from seizou.core.manual import input_rows
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress import services
from seizou.progress.forms import STEP_COMPLETED_HELP, ProgressRecordForm
from seizou.progress.models import Progress
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
R02_TEXT = "実績の数と不良の数は 0 以上で、合わせて 0 より大きい数にします。数を直してください。"
JS_PATH = Path(__file__).resolve().parent.parent / "src" / "seizou" / "progress" / "static" / "progress" / "progress-form.js"
COMPLETES_INPUT = re.compile(r'<input[^>]*id="id_completes_step"[^>]*>')
STEP_HELP = re.compile(r'<p id="id_workorder_step_helptext"[^>]*>(.*?)</p>', re.S)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def steps(user):
    """予定 10 の工程 2 つ (切断 → 曲げ) の指示書。"""
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    cut = Process.objects.create(code="CUT", name="切断")
    bend = Process.objects.create(code="BEND", name="曲げ")
    workorder = workorders_services.issue_workorder(
        order.lines.get(),
        Decimal("10"),
        [{"process": cut, "planned_qty": Decimal("10")}, {"process": bend, "planned_qty": Decimal("10")}],
        user,
    )
    return list(workorder.steps.order_by("seq"))


@pytest.fixture
def step(steps):
    return steps[0]


@pytest.fixture
def marked(user, step):
    """工程 1 (予定 10) に、完了の印の付いた実績 (良品 5) が 1 件ある。"""
    return services.record_progress(step, Decimal("5"), RECORDED_AT, user, completes_step=True)


def _post(client, step, qty, **extra):
    data = {"workorder_step": step.pk, "qty": qty, "recorded_at": "2026-11-03 15:00", "note": "", **extra}
    return client.post(reverse("progress:progress_create"), data)


def _completes_input(body):
    match = COMPLETES_INPUT.search(body)
    assert match, "「この工程を完了にする」の欄が無い"
    return match.group(0)


def _step_help(body):
    match = STEP_HELP.search(body)
    return match.group(1).strip() if match else None


# ---------------------------------------------------------------- (a) 関数


@pytest.mark.django_db
def test_record_keeps_defect_and_mark(user, steps):
    step, other = steps
    record = services.record_progress(step, Decimal("4"), RECORDED_AT, user, defect_qty=Decimal("1"), completes_step=True)
    record.refresh_from_db()
    assert record.qty == Decimal("4")
    assert record.defect_qty == Decimal("1")
    assert record.completes_step is True

    plain = services.record_progress(step, Decimal("2"), RECORDED_AT, user)
    plain.refresh_from_db()
    assert plain.defect_qty == Decimal("0")
    assert plain.completes_step is False

    assert services.recorded_qty(step) == Decimal("6")  # 良品だけ
    assert services.recorded_defect_qty(step) == Decimal("1")
    assert services.step_completed(step) is True

    # 記録の無い工程は 0・0・偽。印の無い実績だけの工程は偽。
    assert services.recorded_qty(other) == Decimal("0")
    assert services.recorded_defect_qty(other) == Decimal("0")
    assert services.step_completed(other) is False
    services.record_progress(other, Decimal("3"), RECORDED_AT, user)
    assert services.step_completed(other) is False


# ---------------------------------------------------------------- (b) R02 (C103-Q2 = a)


@pytest.mark.django_db
def test_r02_allows_defect_only_and_stops_empty_or_negative(user, step):
    record = services.record_progress(step, Decimal("0"), RECORDED_AT, user, defect_qty=Decimal("10"))
    record.refresh_from_db()
    assert (record.qty, record.defect_qty) == (Decimal("0"), Decimal("10"))

    for qty, defect in ((Decimal("0"), Decimal("0")), (Decimal("-1"), Decimal("5")), (Decimal("1"), Decimal("-1"))):
        with pytest.raises(RuleViolation) as caught:
            services.record_progress(step, qty, RECORDED_AT, user, defect_qty=defect)
        assert caught.value.code == "R02"
        assert caught.value.message == services._R02_MESSAGE
        assert R02_TEXT in caught.value.message
    assert Progress.objects.count() == 1


# ---------------------------------------------------------------- (c) 表の決まり


@pytest.mark.django_db
def test_db_constraints_follow_r02(step):
    for qty, defect in ((0, 0), (-1, 5), (1, -1)):
        with pytest.raises(IntegrityError), transaction.atomic():
            Progress.objects.create(workorder_step=step, qty=qty, defect_qty=defect, recorded_at=RECORDED_AT)
    Progress.objects.create(workorder_step=step, qty=0, defect_qty=3, recorded_at=RECORDED_AT)
    assert Progress.objects.count() == 1


# ---------------------------------------------------------------- (d) 画面の保存


@pytest.mark.django_db
def test_screen_saves_defect_and_mark(client_logged_in, step):
    response = _post(client_logged_in, step, "4", defect_qty="1", completes_step="on")
    assert response.status_code == 302
    record = Progress.objects.get()
    assert (record.qty, record.defect_qty, record.completes_step) == (Decimal("4"), Decimal("1"), True)


@pytest.mark.django_db
def test_screen_without_new_fields_is_zero_and_unmarked(client_logged_in, step):
    assert _post(client_logged_in, step, "4").status_code == 302  # 今の試験の形 (2 つを送らない)
    assert _post(client_logged_in, step, "2", defect_qty="").status_code == 302  # 空の字
    assert [(r.defect_qty, r.completes_step) for r in Progress.objects.order_by("pk")] == [
        (Decimal("0"), False),
        (Decimal("0"), False),
    ]


@pytest.mark.django_db
def test_screen_negative_defect_is_field_error(client_logged_in, step):
    response = _post(client_logged_in, step, "4", defect_qty="-1")
    assert response.status_code == 200
    assert "不良の数は 0 以上の数にしてください。" in response.content.decode()
    assert Progress.objects.count() == 0


@pytest.mark.django_db
def test_screen_defect_only_saves_and_zero_zero_is_r02(client_logged_in, step):
    assert _post(client_logged_in, step, "0", defect_qty="10").status_code == 302
    assert Progress.objects.get().defect_qty == Decimal("10")

    response = _post(client_logged_in, step, "0")
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body and "R02" in body and R02_TEXT in body
    assert Progress.objects.count() == 1


# ---------------------------------------------------------------- (e) form


def test_form_fields_and_labels():
    fields = ProgressRecordForm.base_fields
    assert list(fields) == ["workorder_step", "staff_code", "staff_name", "qty", "defect_qty", "completes_step", "recorded_at", "note"]
    assert fields["defect_qty"].label == "不良の数"
    assert fields["defect_qty"].required is False
    assert fields["completes_step"].label == "この工程を完了にする"
    assert fields["completes_step"].required is False
    assert fields["completes_step"].initial is None  # いつも空で始める (PROG-1b = a)
    assert fields["qty"].help_text == "この工程を終えた良品の数 (不良を除く)。"
    assert fields["workorder_step"].help_text == ""  # PROG-1c の字は宣言に置かない


@pytest.mark.django_db
def test_qty_and_defect_share_a_row(client_logged_in):
    """「実績の数」を行の頭から始め、「不良の数」をその右に並べる (row_start_fields)。"""
    body = client_logged_in.get(reverse("progress:progress_create")).content.decode()
    box = re.search(r'<div class="([^"]*)">\s*<div class="mb-2 flex items-center gap-2">\s*<label for="id_qty"', body)
    assert box and "sm:col-start-1" in box.group(1)


# ---------------------------------------------------------------- (f) 印は空で始まる (PROG-1b = a)


@pytest.mark.django_db
def test_mark_starts_empty(client_logged_in, step, marked):
    url = reverse("progress:progress_create")
    for query in ({}, {"workorder_step": step.pk}, {"completes_step": "on"}, {"workorder_step": step.pk, "completes_step": "on"}):
        response = client_logged_in.get(url, query)
        assert response.status_code == 200
        assert "checked" not in _completes_input(response.content.decode()), query


# ---------------------------------------------------------------- (g) PROG-1c


@pytest.mark.django_db
def test_marked_step_shows_note_under_step_field(client_logged_in, steps, marked):
    step, other = steps
    url = reverse("progress:progress_create")
    assert _step_help(client_logged_in.get(url, {"workorder_step": step.pk}).content.decode()) == STEP_COMPLETED_HELP
    assert _step_help(client_logged_in.get(url, {"workorder_step": other.pk}).content.decode()) is None
    assert _step_help(client_logged_in.get(url).content.decode()) is None


@pytest.mark.django_db
def test_marked_step_still_takes_records(client_logged_in, step, marked):
    response = _post(client_logged_in, step, "2")
    assert response.status_code == 302
    assert Progress.objects.count() == 2
    assert services.step_completed(step) is True  # 印なしで足しても完了の印は残る
    assert services.recorded_qty(step) == Decimal("7")  # 足した数は工程の合計に入る


@pytest.mark.django_db
def test_step_completed_endpoint(client, client_logged_in, steps, marked):
    step, other = steps
    url = reverse("progress:step_completed", args=[step.pk])
    client_logged_in.logout()
    assert client.get(url).status_code == 302  # ログインの前
    user = get_user_model().objects.get(username="tester")
    client.force_login(user)

    response = client.get(url)
    assert response.status_code == 200
    assert json.loads(response.content) == {"completed": True}
    cache = response["Cache-Control"]
    assert "no-cache" in cache or "no-store" in cache or "max-age=0" in cache
    assert json.loads(client.get(reverse("progress:step_completed", args=[other.pk])).content) == {"completed": False}
    assert client.get(reverse("progress:step_completed", args=[other.pk + 1000])).status_code == 404
    assert client.post(url).status_code == 405  # 読むだけ


def test_js_uses_the_same_text():
    assert STEP_COMPLETED_HELP in JS_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------- (h) 「?」の入れる物の表


def test_manual_input_rows_have_new_fields():
    rows = {name: (required, note) for name, required, note in input_rows(ProgressRecordForm)}
    assert rows["不良の数"] == ("", "この工程で不良になった数。無ければ空のままにします。")
    assert rows["この工程を完了にする"] == ("", "この工程を終えたら付けます。最後の工程に付けると、指示書が「完了」になります。")
    assert STEP_COMPLETED_HELP not in rows["指示書の工程"][1]


# ---------------------------------------------------------------- (i) 実績の一覧と CSV


@pytest.mark.django_db
def test_record_list_shows_defect_and_mark(client_logged_in, user, steps):
    step, other = steps
    services.record_progress(step, Decimal("4"), RECORDED_AT, user, defect_qty=Decimal("1"), completes_step=True)
    services.record_progress(other, Decimal("3"), RECORDED_AT, user)
    url = reverse("progress:record_list")
    body = client_logged_in.get(url).content.decode()
    assert "不良の数" in body and "工程の完了" in body

    text = client_logged_in.get(url, {"format": "csv"}).content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[0]
    assert "不良の数" in header and "工程の完了" in header
    by_step = {row[0]: dict(zip(header, row)) for row in rows[1:]}
    assert by_step[str(step)]["不良の数"] == "1.00"
    assert by_step[str(step)]["工程の完了"] == "はい"
    assert by_step[str(other)]["工程の完了"] == "いいえ"


# ---------------------------------------------------------------- (j) 小窓


@pytest.mark.django_db
def test_modal_has_new_fields_and_script(client_logged_in, steps, marked):
    step, other = steps
    url = reverse("progress:progress_create")
    response = client_logged_in.get(url, {"workorder_step": other.pk}, **MODAL)
    assert response.status_code == 200
    body = response.content.decode()
    assert "<html" not in body
    assert 'name="defect_qty"' in body and 'name="completes_step"' in body
    assert "progress/progress-form.js" in body
    assert "checked" not in _completes_input(body)
    assert _step_help(body) is None

    marked_body = client_logged_in.get(url, {"workorder_step": step.pk}, **MODAL).content.decode()
    assert _step_help(marked_body) == STEP_COMPLETED_HELP
    assert "checked" not in _completes_input(marked_body)

    data = {"workorder_step": step.pk, "qty": "4", "defect_qty": "-1", "recorded_at": "2026-11-03 15:00", "note": ""}
    stopped = client_logged_in.post(url, data, **MODAL)
    assert stopped.status_code == 200
    assert "不良の数は 0 以上の数にしてください。" in stopped.content.decode()
    assert Progress.objects.count() == 1

    assert "progress/progress-form.js" in client_logged_in.get(url).content.decode()  # 画面の全体にも


# ---------------------------------------------------------------- (k) 知らせの字


def _saved_text(step, qty_text):
    return f"{step.workorder.wo_no} の工程 1 切断 に実績 {qty_text} を記録しました。"


@pytest.mark.django_db
def test_saved_message_mentions_defect_and_mark(user, step):
    with_defect = services.record_progress(step, Decimal("490"), RECORDED_AT, user, defect_qty=Decimal("10"))
    assert with_defect.saved_message == _saved_text(step, "490・不良 10")
    without = services.record_progress(step, Decimal("4"), RECORDED_AT, user)
    assert without.saved_message == _saved_text(step, "4")
    finished = services.record_progress(step, Decimal("1"), RECORDED_AT, user, defect_qty=Decimal("2.50"), completes_step=True)
    assert finished.saved_message == _saved_text(step, "1・不良 2.5") + "この工程に完了の印を付けました。"


@pytest.mark.django_db
def test_saved_message_on_screen(client_logged_in, step):
    # 予定 10 を超えるので R07 の黄の知らせが出る。「このまま保存」(confirmed=1) で保存する。
    response = _post(client_logged_in, step, "490", defect_qty="10", completes_step="on", confirmed="1")
    assert response.status_code == 302
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert messages == [_saved_text(step, "490・不良 10") + "この工程に完了の印を付けました。"]
