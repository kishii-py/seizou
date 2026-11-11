"""実績の入力と編集の不良の行の欄 (progress の ProgressRecordForm・ProgressUpdateForm。2 段目 N407b。見比べのまとめの M09)。

使用中の不良の区分が 1 つも無い会社では、今の「不良の数」の欄のまま (services が区分の無い 1 行を作る)。
使用中の区分がある会社では「不良の数」の代わりに 3 行ぶんの欄 (区分・数・原因) が「実績の数」の後に並び、行の合計が実績の不良の数になる。
欄の誤り (数の無い行・0 以下の数) は欄の下の赤い字、区分を選んでいない行はポカよけ R30 (会社の設定 progress_defect_kind)。
編集は今の行の全部と空の 3 組を出し、数を空にした行を消す。消した区分の写しだけの行は、区分を選び直さなければ写しのまま残る。
小窓と現品票の QR の道 (?workorder_step=) でも同じ欄が出る。削除の確かめは不良の行の件数を添える。
区分と実績は services で作る (工程の行だけは R07 の試験と同じく表に直に作る)。名前は作り物だけ。
"""

import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress import services
from seizou.progress.forms import ProgressRecordForm, ProgressUpdateForm
from seizou.progress.models import Progress, ProgressDefect
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
LINE_FIELDS = [f"defect_{n}_{part}" for n in (1, 2, 3) for part in ("kind", "qty", "cause")]
LINE_LABELS = [f"不良 {n} の{word}" for n in (1, 2, 3) for word in ("区分", "数", "原因")]
QTY_REQUIRED = "数を入れてください。"
QTY_POSITIVE = "数は 0 より大きい数にしてください。"
FIRST_QTY_HELP = "区分ごとに 1 行。4 行以上あるときは、保存した後に実績の編集で足します。"
DROP_HELP = "数を空にすると、この行を消します。"
R30_WARN_1 = "区分を選んでいない不良が 1 あります。不良の集計では「未設定」の行に入ります。このまま保存できます。"
COPIED_HELP = "消した区分「作り物の傷」です。区分を選び直さなければ、そのまま残ります。"
DELETE_NOTE_2 = "この実績の不良の行 2 件も一緒に消えます。"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def step(user):
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
def scratch(user):
    return masters_services.create_defect_kind({"name": "作り物の傷", "sort_order": 1}, user)


@pytest.fixture
def size(user):
    return masters_services.create_defect_kind({"name": "作り物の寸法", "sort_order": 2}, user)


@pytest.fixture
def rust(user):
    """使用停止の区分 (候補に出ない)。"""
    kind = masters_services.create_defect_kind({"name": "作り物の錆", "sort_order": 0}, user)
    return masters_services.update_defect_kind(kind, {"active": False}, user)


def _post(client, step, url=None, **extra):
    data = {"workorder_step": step.pk, "qty": "4", "recorded_at": "2026-11-03T15:00", "note": "", **extra}
    return client.post(url or reverse("progress:progress_create"), data)


def _edit_url(record):
    return reverse("progress:progress_update", args=[record.pk])


def _edit(client, record, **extra):
    data = {"qty": "2", "recorded_at": "2026-11-04T09:15", "note": "直した", **extra}
    return client.post(_edit_url(record), data)


def _messages(response):
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


def _field_errors(response, name):
    return [str(error) for error in response.context["form"].errors.get(name, [])]


def _lines(record):
    return [(line.kind_id, line.kind_name, line.qty, line.cause_note, line.countermeasure) for line in record.defects.order_by("pk")]


def _choice_texts(form, name):
    return [str(text) for _value, text in form.fields[name].choices]


# ---------------------------------------------------------------- (a) 区分が 0 件の会社は今のまま


def test_no_kinds_keeps_defect_qty_field(client_logged_in, step):
    body = client_logged_in.get(reverse("progress:progress_create")).content.decode()
    assert 'name="defect_qty"' in body
    assert 'name="defect_1_kind"' not in body

    response = _post(client_logged_in, step, defect_qty="4")
    assert response.status_code == 302
    record = Progress.objects.get()
    assert record.defect_qty == Decimal("4")
    assert _lines(record) == [(None, "", Decimal("4"), "", "")]


def test_inactive_kinds_only_keep_defect_qty_field(client_logged_in, rust):
    body = client_logged_in.get(reverse("progress:progress_create")).content.decode()
    assert 'name="defect_qty"' in body
    assert 'name="defect_1_kind"' not in body


def test_declared_fields_do_not_change():
    """宣言 (base_fields) は変えない (「?」の「入れる物」の表と欄の並びの試験が宣言を読む)。"""
    assert list(ProgressRecordForm.base_fields) == [
        "workorder_step", "staff_code", "staff_name", "qty", "defect_qty", "completes_step", "recorded_at", "note",
    ]
    assert "defect_1_kind" not in ProgressUpdateForm.base_fields


# ---------------------------------------------------------------- (b) 使用中の区分がある会社の入力


def test_line_fields_replace_defect_qty(client_logged_in, scratch, size, rust):
    response = client_logged_in.get(reverse("progress:progress_create"))
    form = response.context["form"]
    assert list(form.fields) == ["workorder_step", "qty", *LINE_FIELDS, "completes_step", "recorded_at", "note"]
    assert [str(form.fields[name].label) for name in LINE_FIELDS] == LINE_LABELS
    for n in (1, 2, 3):
        assert _choice_texts(form, f"defect_{n}_kind") == ["区分なし", "作り物の傷", "作り物の寸法"]
        assert form.fields[f"defect_{n}_qty"].required is False
        assert form.fields[f"defect_{n}_cause"].max_length == 500
    assert form.fields["defect_1_qty"].help_text == FIRST_QTY_HELP
    body = response.content.decode()
    assert 'name="defect_qty"' not in body
    assert "作り物の錆" not in body
    assert FIRST_QTY_HELP in body
    assert "不良の区分がある会社では、不良を区分ごとの行で入れます。" in response.context["screen_help"]
    # 区分の欄は行の頭から始め、数をその右に並べる
    for n in (1, 2, 3):
        box = re.search(rf'<div class="([^"]*)">\s*<div class="mb-2 flex items-center gap-2">\s*<label for="id_defect_{n}_kind"', body)
        assert box and "sm:col-start-1" in box.group(1), n
    # 行を足す JS は読まない (小窓とスマホの画面で同じに動かすため)
    assert "defect-lines" not in body


def test_two_lines_make_two_rows_and_total(client_logged_in, step, scratch, size):
    response = _post(
        client_logged_in,
        step,
        defect_1_kind=scratch.pk,
        defect_1_qty="3",
        defect_1_cause="作り物の治具の当たり",
        defect_2_kind=size.pk,
        defect_2_qty="2",
    )
    assert response.status_code == 302
    record = Progress.objects.get()
    assert record.defect_qty == Decimal("5")
    assert _lines(record) == [
        (scratch.pk, "", Decimal("3"), "作り物の治具の当たり", ""),
        (size.pk, "", Decimal("2"), "", ""),
    ]
    [(level, text)] = _messages(response)
    assert level == "success" and "不良 5" in text


def test_kind_without_qty_and_zero_are_field_errors(client_logged_in, step, scratch):
    response = _post(client_logged_in, step, defect_1_kind=scratch.pk)
    assert response.status_code == 200
    assert _field_errors(response, "defect_1_qty") == [QTY_REQUIRED]
    assert 'data-notice="stop"' in response.content.decode()
    assert not Progress.objects.exists()

    response = _post(client_logged_in, step, defect_1_cause="作り物の原因", defect_2_kind=scratch.pk, defect_2_qty="0")
    assert response.status_code == 200
    assert _field_errors(response, "defect_1_qty") == [QTY_REQUIRED]
    assert _field_errors(response, "defect_2_qty") == [QTY_POSITIVE]
    assert QTY_POSITIVE in response.content.decode()
    assert not Progress.objects.exists()

    response = _post(client_logged_in, step, defect_1_kind=scratch.pk, defect_1_qty="-1")
    assert _field_errors(response, "defect_1_qty") == [QTY_POSITIVE]
    assert not Progress.objects.exists()


def test_empty_lines_save_without_defect(client_logged_in, step, scratch):
    response = _post(client_logged_in, step)
    assert response.status_code == 302
    record = Progress.objects.get()
    assert record.defect_qty == Decimal("0")
    assert _lines(record) == []


def test_line_without_kind_warns_r30_then_saves(client_logged_in, step, scratch):
    first = _post(client_logged_in, step, defect_1_qty="1")
    assert first.status_code == 200
    body = first.content.decode()
    assert 'data-notice="check"' in body and "R30" in body and "このまま保存" in body
    assert [warning.message for warning in first.context["warnings"]] == [R30_WARN_1]
    assert not Progress.objects.exists()

    second = _post(client_logged_in, step, defect_1_qty="1", confirmed="1")
    assert second.status_code == 302
    record = Progress.objects.get()
    assert _lines(record) == [(None, "", Decimal("1"), "", "")]


def test_line_without_kind_stops_when_setting_is_stop(settings, client_logged_in, step, scratch):
    settings.SEIZOU = {"progress_defect_kind": "stop"}
    response = _post(client_logged_in, step, defect_1_qty="1", confirmed="1")
    assert response.status_code == 200
    assert 'data-notice="stop"' in response.content.decode()
    assert not Progress.objects.exists()


# ---------------------------------------------------------------- (c) 実績の編集


@pytest.fixture
def split_record(user, step, scratch, size):
    """区分のある行を 2 つ持つ実績 (傷 2 に対策あり・寸法 1)。"""
    record = services.record_progress(
        step,
        Decimal("4"),
        RECORDED_AT,
        user,
        defects=[{"kind": scratch, "qty": Decimal("2"), "cause_note": "作り物の原因"}, {"kind": size, "qty": Decimal("1")}],
    )
    first = record.defects.order_by("pk").first()
    services.update_defect_note(first, "作り物の原因", "作り物の対策", user)
    return record


def test_edit_shows_current_lines_and_three_empty(client_logged_in, split_record, scratch, size):
    first, second = split_record.defects.order_by("pk")
    response = client_logged_in.get(_edit_url(split_record))
    form = response.context["form"]
    names = [name for name in form.fields if name.startswith("defect_")]
    assert names == [f"defect_{n}_{part}" for n in range(1, 6) for part in (("pk", "kind", "qty", "cause") if n <= 2 else ("kind", "qty", "cause"))]
    assert "defect_qty" not in form.fields
    assert (form["defect_1_pk"].value(), form["defect_1_kind"].value(), form["defect_1_qty"].value(), form["defect_1_cause"].value()) == (
        first.pk, scratch.pk, Decimal("2"), "作り物の原因",
    )
    assert (form["defect_2_pk"].value(), form["defect_2_kind"].value(), form["defect_2_qty"].value()) == (second.pk, size.pk, Decimal("1"))
    for n in (3, 4, 5):
        assert form["defect_{}_kind".format(n)].value() in (None, "")
        assert form["defect_{}_qty".format(n)].value() in (None, "")
    assert form.fields["defect_1_qty"].help_text == DROP_HELP
    body = response.content.decode()
    assert 'name="defect_qty"' not in body
    assert f'name="defect_1_pk" value="{first.pk}"' in body
    assert "不良の区分がある会社では、不良の行を直します。" in response.context["screen_help"]


def test_edit_changes_drops_and_adds_lines(client_logged_in, split_record, scratch, size):
    first, second = split_record.defects.order_by("pk")
    response = _edit(
        client_logged_in,
        split_record,
        defect_1_pk=first.pk,
        defect_1_kind=size.pk,
        defect_1_qty="2",
        defect_1_cause="作り物の原因",
        defect_2_pk=second.pk,
        defect_2_kind=size.pk,
        defect_2_qty="",
        defect_3_kind=scratch.pk,
        defect_3_qty="4",
    )
    assert response.status_code == 302
    split_record.refresh_from_db()
    assert split_record.defect_qty == Decimal("6")
    lines = list(split_record.defects.order_by("pk"))
    assert [line.pk for line in lines][0] == first.pk
    assert not ProgressDefect.objects.filter(pk=second.pk).exists()
    assert _lines(split_record) == [
        (size.pk, "", Decimal("2"), "作り物の原因", "作り物の対策"),
        (scratch.pk, "", Decimal("4"), "", ""),
    ]


def test_edit_blank_kind_on_kind_line_clears_kind(client_logged_in, split_record, scratch, size):
    first, second = split_record.defects.order_by("pk")
    response = _edit(
        client_logged_in,
        split_record,
        confirmed="1",
        defect_1_pk=first.pk,
        defect_1_kind="",
        defect_1_qty="2",
        defect_2_pk=second.pk,
        defect_2_kind=size.pk,
        defect_2_qty="1",
    )
    assert response.status_code == 302
    assert _lines(split_record)[0][:3] == (None, "", Decimal("2"))


def test_edit_line_errors_are_field_errors(client_logged_in, split_record, scratch, size):
    first, second = split_record.defects.order_by("pk")
    response = _edit(
        client_logged_in,
        split_record,
        defect_1_pk=first.pk,
        defect_1_kind=scratch.pk,
        defect_1_qty="0",
        defect_2_pk=second.pk,
        defect_2_kind=size.pk,
        defect_2_qty="1",
        defect_3_kind=scratch.pk,
    )
    assert response.status_code == 200
    assert _field_errors(response, "defect_1_qty") == [QTY_POSITIVE]
    assert _field_errors(response, "defect_3_qty") == [QTY_REQUIRED]
    split_record.refresh_from_db()
    assert split_record.defect_qty == Decimal("3")


def test_edit_plain_record_without_kinds_keeps_defect_qty(client_logged_in, user, step):
    record = services.record_progress(step, Decimal("4"), RECORDED_AT, user, defect_qty=Decimal("1"))
    response = client_logged_in.get(_edit_url(record))
    assert "defect_qty" in response.context["form"].fields
    assert "defect_1_kind" not in response.context["form"].fields

    response = _edit(client_logged_in, record, defect_qty="3")
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.defect_qty == Decimal("3")
    assert _lines(record) == [(None, "", Decimal("3"), "", "")]


def test_edit_copied_line_keeps_copy_without_choosing(client_logged_in, user, step, scratch):
    record = services.record_progress(step, Decimal("4"), RECORDED_AT, user, defects=[{"kind": scratch, "qty": Decimal("2")}])
    masters_services.delete_defect_kind(scratch, user)
    line = record.defects.get()
    assert (line.kind_id, line.kind_name) == (None, "作り物の傷")

    response = client_logged_in.get(_edit_url(record))
    form = response.context["form"]
    # 使用中の区分は 0 件だが、写しの行があるので欄を出す
    assert "defect_qty" not in form.fields
    assert form["defect_1_kind"].value() in (None, "")
    assert form.fields["defect_1_kind"].help_text == COPIED_HELP
    assert COPIED_HELP in response.content.decode()

    response = _edit(client_logged_in, record, defect_1_pk=line.pk, defect_1_kind="", defect_1_qty="3")
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.defect_qty == Decimal("3")
    assert _lines(record) == [(None, "作り物の傷", Decimal("3"), "", "")]


def test_edit_copied_line_takes_new_kind_when_chosen(client_logged_in, user, step, scratch, size):
    record = services.record_progress(step, Decimal("4"), RECORDED_AT, user, defects=[{"kind": scratch, "qty": Decimal("2")}])
    masters_services.delete_defect_kind(scratch, user)
    line = record.defects.get()
    response = _edit(client_logged_in, record, defect_1_pk=line.pk, defect_1_kind=size.pk, defect_1_qty="2")
    assert response.status_code == 302
    assert _lines(record) == [(size.pk, "", Decimal("2"), "", "")]


def test_edit_keeps_inactive_kind_of_current_line_in_choices(client_logged_in, user, split_record, scratch):
    masters_services.update_defect_kind(scratch, {"active": False}, user)
    form = client_logged_in.get(_edit_url(split_record)).context["form"]
    assert _choice_texts(form, "defect_1_kind") == ["区分なし", "作り物の傷", "作り物の寸法"]
    assert _choice_texts(form, "defect_3_kind") == ["区分なし", "作り物の寸法"]


# ---------------------------------------------------------------- (d) 小窓と現品票の QR の道


def test_modal_and_qr_path_show_line_fields(client_logged_in, step, split_record):
    for response in (
        client_logged_in.get(reverse("progress:progress_create"), **MODAL),
        client_logged_in.get(reverse("progress:progress_create"), {"workorder_step": step.pk}),
        client_logged_in.get(_edit_url(split_record), **MODAL),
    ):
        assert response.status_code == 200
        body = response.content.decode()
        assert 'name="defect_1_kind"' in body and 'name="defect_3_cause"' in body
        assert 'name="defect_qty"' not in body


def test_modal_post_saves_lines(client_logged_in, step, scratch):
    url = reverse("progress:progress_create")
    data = {"workorder_step": step.pk, "qty": "4", "recorded_at": "2026-11-03T15:00", "defect_1_kind": scratch.pk, "defect_1_qty": "2"}
    response = client_logged_in.post(url, data, **MODAL)
    assert response.status_code == 204
    assert _lines(Progress.objects.get()) == [(scratch.pk, "", Decimal("2"), "", "")]


# ---------------------------------------------------------------- (e) 削除の確かめ


def test_delete_confirm_mentions_lines(client_logged_in, user, step, split_record):
    url = reverse("progress:progress_delete", args=[split_record.pk])
    for response in (client_logged_in.get(url), client_logged_in.get(url, **MODAL)):
        body = response.content.decode()
        assert DELETE_NOTE_2 in body
        assert "この実績を消すと戻せません。" in body

    plain = services.record_progress(step, Decimal("1"), RECORDED_AT, user)
    body = client_logged_in.get(reverse("progress:progress_delete", args=[plain.pk])).content.decode()
    assert "この実績の不良の行" not in body
