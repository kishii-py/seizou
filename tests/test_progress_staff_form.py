"""実績の入力と編集の「社員番号」と「社員の一覧に居ない人の名前」の欄、実績の一覧の「作業した人」(2 段目 N401b。本人の決め 11-11 09:0x)。

使用中の社員が 0 人の会社では欄が出ない。社員が居ると、現品票の QR から開く道 (?workorder_step=) と小窓の両方に 2 つの欄が出る。
番号は照合の字 (先頭の 0 を省ける) で社員を引き、一覧に無い番号と、番号と名前の両方は欄の誤りで止める。
使用停止の番号は欄の誤りにせず、R28 の知らせを出す (本人の決め 11-11 13:1x による読み替え。使用停止の社員を断らず、R28 で知らせる)。
社員を消した後の写しを持つ実績の編集では欄を出さず、写しを変えない。見せる字は Progress.get_staff_name_display の 1 つだけ。
社員と実績は services で作る (工程の行だけは R07 の試験と同じく表に直に作る)。名前は作り物だけ。
"""

import csv
import io
import json
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress import services
from seizou.progress.forms import ProgressRecordForm, ProgressUpdateForm
from seizou.progress.models import Progress
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
RECORDED_AT = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
FIELDS = ('name="staff_code"', 'name="staff_name"')
UNKNOWN_99 = "社員番号 99 は社員の一覧にありません。番号を確かめるか、社員の一覧に足してください。"
R28_TARO = "社員番号 0012 (作り物の太郎) は使用停止の社員です。番号に間違いが無ければ、このまま保存できます。"
BOTH = "社員番号と名前のどちらか一方を入れます。社員番号を入れたときは、名前を空にしてください。"
R26_MESSAGE = "作業した人が入っていません。社員番号か、社員の一覧に居ない人の名前を入れてください。"


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
def taro(user):
    return masters_services.create_staff({"code": "0012", "name": "作り物の太郎"}, user)


@pytest.fixture
def jiro(user):
    return masters_services.create_staff({"code": "0034", "name": "作り物の次郎"}, user)


def _record(step, user, **kwargs):
    return services.record_progress(step, Decimal("1"), RECORDED_AT, user, **kwargs)


def _post(client, step, **extra):
    data = {"workorder_step": step.pk, "qty": "4", "recorded_at": "2026-11-03T15:00", "note": "", **extra}
    return client.post(reverse("progress:progress_create"), data)


def _edit(client, record, **extra):
    data = {"qty": "2", "defect_qty": "", "recorded_at": "2026-11-04T09:15", "note": "直した", **extra}
    return client.post(reverse("progress:progress_update", args=[record.pk]), data)


def _messages(response):
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


def _field_errors(response, name):
    return [str(error) for error in response.context["form"].errors.get(name, [])]


def _step_text(step):
    return f"{step.workorder.wo_no} の工程 1 切断"


# ---------------------------------------------------------------- (a) 社員が 0 人の会社


def test_no_fields_without_active_staff(client_logged_in, user, step):
    record = _record(step, user)
    create = client_logged_in.get(reverse("progress:progress_create"), {"workorder_step": step.pk}).content.decode()
    edit = client_logged_in.get(reverse("progress:progress_update", args=[record.pk])).content.decode()
    modal = client_logged_in.get(reverse("progress:progress_create"), **MODAL).content.decode()
    for body in (create, edit, modal):
        for field in FIELDS:
            assert field not in body
    # 使用停止の社員しか居ない会社も同じ
    staff = masters_services.create_staff({"code": "0012", "name": "作り物の太郎"}, user)
    masters_services.update_staff(staff, {"active": False}, user)
    body = client_logged_in.get(reverse("progress:progress_create")).content.decode()
    assert not [field for field in FIELDS if field in body]
    # 欄の無い form でも、保存すると作業した人は空のまま (R26 は社員が居ないので出ない)
    response = _post(client_logged_in, step)
    assert response.status_code == 302
    assert Progress.objects.filter(staff__isnull=True, staff_name="", staff_code="").count() == 2


def test_help_table_reads_declared_fields_only():
    """「?」の「入れる物」の表は宣言の欄 (base_fields) を読む。2 つの欄は宣言にあり、外すのは __init__ (社員が 0 人の会社)。"""
    for form_class in (ProgressRecordForm, ProgressUpdateForm):
        assert "staff_code" in form_class.base_fields and "staff_name" in form_class.base_fields
        assert form_class.base_fields["staff_code"].max_length == 10
        assert form_class.base_fields["staff_name"].max_length == 100
        assert form_class.base_fields["staff_code"].required is False
        assert form_class.base_fields["staff_name"].required is False


# ---------------------------------------------------------------- (b) QR から開く道と小窓


def test_fields_appear_on_qr_path_and_modal(client_logged_in, step, taro):
    url = reverse("progress:progress_create")
    qr = client_logged_in.get(url, {"workorder_step": step.pk})
    modal = client_logged_in.get(url, {"workorder_step": step.pk}, **MODAL)
    for response in (qr, modal):
        assert response.status_code == 200
        body = response.content.decode()
        for field in FIELDS:
            assert field in body
        # 置き場は「指示書の工程」の次で、番号と名前を同じ行の左右に並べる (番号が行の頭)
        positions = [body.index(f'name="{name}"') for name in ("workorder_step", "staff_code", "staff_name", "qty")]
        assert positions == sorted(positions)
        assert "社員の札のバーコードを読むか、番号を打ちます。先頭の 0 は省けます。" in body
        assert "応援の人など、社員の一覧に居ない人が作業したときだけ入れます。" in body
    assert "<html" not in modal.content.decode()
    form = qr.context["form"]
    assert list(form.fields)[:4] == ["workorder_step", "staff_code", "staff_name", "qty"]
    assert "staff_code" in form.row_start_fields and "qty" in form.row_start_fields


# ---------------------------------------------------------------- (c) 番号で社員を引く


def test_short_number_finds_staff(client_logged_in, step, taro):
    response = _post(client_logged_in, step, staff_code="12")
    assert response.status_code == 302
    record = Progress.objects.get()
    assert record.staff == taro and record.staff_name == "" and record.staff_code == ""
    assert _messages(response) == [("success", f"{_step_text(step)} に実績 4 を記録しました。作業した人は 0012 作り物の太郎 です。")]


def test_short_number_in_modal(client_logged_in, step, taro):
    data = {"workorder_step": step.pk, "qty": "4", "recorded_at": "2026-11-03T15:00", "note": "", "staff_code": "１２"}
    response = client_logged_in.post(reverse("progress:progress_create"), data, **MODAL)
    assert response.status_code == 204
    saved = json.loads(response["HX-Trigger"])["seizou:saved"]
    assert saved["message"].endswith("作業した人は 0012 作り物の太郎 です。")
    assert Progress.objects.get().staff == taro


# ---------------------------------------------------------------- (d) 欄の誤り


def test_unknown_and_both_are_field_errors_and_inactive_warns_r28(client_logged_in, user, step, taro, jiro):
    """一覧に無い番号と、番号と名前の両方は欄の誤り。使用停止の番号は欄の誤りにせず、R28 の黄色の知らせと「このまま保存」を出す。

    使用停止の番号の所は、本人の決め 11-11 13:1x による読み替え (使用停止の社員を断らず、R28 で知らせる)。
    """
    response = _post(client_logged_in, step, staff_code="99")
    assert response.status_code == 200
    assert _field_errors(response, "staff_code") == [UNKNOWN_99]
    body = response.content.decode()
    assert UNKNOWN_99 in body and 'data-notice="stop"' in body

    response = _post(client_logged_in, step, staff_code="12", staff_name="応援の作り物の花子")
    assert response.status_code == 200
    assert _field_errors(response, "staff_name") == [BOTH]
    assert _field_errors(response, "staff_code") == []

    # 使用中の社員 (次郎) がほかに居るので欄は出たまま
    masters_services.update_staff(taro, {"active": False}, user)
    response = _post(client_logged_in, step, staff_code="12")
    assert response.status_code == 200
    assert _field_errors(response, "staff_code") == []
    body = response.content.decode()
    assert 'data-notice="check"' in body and "R28" in body and "このまま保存" in body
    assert [warning.message for warning in response.context["warnings"]] == [R28_TARO]
    assert not Progress.objects.exists()

    response = _post(client_logged_in, step, staff_code="12", confirmed="1")
    assert response.status_code == 302
    record = Progress.objects.get()
    assert record.staff == taro and record.staff_name == "" and record.staff_code == ""


def test_too_long_values_are_field_errors(client_logged_in, step, taro):
    response = _post(client_logged_in, step, staff_code="1" * 11)
    assert response.status_code == 200 and _field_errors(response, "staff_code")
    response = _post(client_logged_in, step, staff_name="長" * 101)
    assert response.status_code == 200 and _field_errors(response, "staff_name")
    assert not Progress.objects.exists()


# ---------------------------------------------------------------- (e) 名前だけ


def test_name_only_writes_copy(client_logged_in, step, taro):
    response = _post(client_logged_in, step, staff_name=" 応援の作り物の花子 ")
    assert response.status_code == 302
    record = Progress.objects.get()
    assert record.staff is None and record.staff_name == "応援の作り物の花子" and record.staff_code == ""
    assert _messages(response)[0][1].endswith("作業した人は 応援の作り物の花子 です。")


# ---------------------------------------------------------------- (f) 両方が空 (R26)


def test_both_empty_warns_r26_then_saves(client_logged_in, step, taro):
    first = _post(client_logged_in, step)
    assert first.status_code == 200
    body = first.content.decode()
    assert 'data-notice="check"' in body and "R26" in body and "このまま保存" in body
    assert [warning.message for warning in first.context["warnings"]] == [R26_MESSAGE]
    assert not Progress.objects.exists()

    second = _post(client_logged_in, step, confirmed="1")
    assert second.status_code == 302
    record = Progress.objects.get()
    assert record.staff is None and record.staff_name == "" and record.staff_code == ""


def test_both_empty_stops_when_setting_is_stop(settings, client_logged_in, step, taro):
    settings.SEIZOU = {"progress_staff": "stop"}
    response = _post(client_logged_in, step, confirmed="1")
    assert response.status_code == 200
    assert 'data-notice="stop"' in response.content.decode()
    assert not Progress.objects.exists()


# ---------------------------------------------------------------- (g) 名前を直す・使用停止の後の編集


def test_renamed_staff_shows_in_list_and_edit_keeps_inactive_staff(client_logged_in, user, step, taro):
    record = _record(step, user, staff=taro)
    masters_services.update_staff(taro, {"name": "作り物の太郎 (直した)"}, user)
    listing = client_logged_in.get(reverse("progress:record_list"))
    assert "0012 作り物の太郎 (直した)" in listing.content.decode()

    masters_services.update_staff(taro, {"active": False}, user)
    edit_url = reverse("progress:progress_update", args=[record.pk])
    body = client_logged_in.get(edit_url).content.decode()
    assert 'name="staff_code"' in body and 'value="0012"' in body  # 初めの値は社員の今の番号
    response = _edit(client_logged_in, record, staff_code="0012")
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.staff == taro and record.qty == Decimal("2") and record.note == "直した"


def test_edit_changes_staff_and_warns_other_inactive_r28(client_logged_in, user, step, taro, jiro):
    """編集で別の社員に直せる。別の使用停止の社員に直すと、R28 の黄色の知らせが出て、「このまま保存」で直る。

    使用停止の社員の所は、本人の決め 11-11 13:1x による読み替え (使用停止の社員を断らず、R28 で知らせる)。
    """
    record = _record(step, user, staff=taro)
    response = _edit(client_logged_in, record, staff_code="34")
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.staff == jiro

    masters_services.update_staff(taro, {"active": False}, user)
    response = _edit(client_logged_in, record, staff_code="12")
    assert response.status_code == 200
    assert _field_errors(response, "staff_code") == []
    body = response.content.decode()
    assert 'data-notice="check"' in body and "R28" in body and "このまま保存" in body
    assert [warning.message for warning in response.context["warnings"]] == [R28_TARO]
    record.refresh_from_db()
    assert record.staff == jiro

    response = _edit(client_logged_in, record, staff_code="12", confirmed="1")
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.staff == taro and record.qty == Decimal("2")


def test_edit_initial_name_for_helper_and_switch_to_name(client_logged_in, user, step, taro):
    helper = _record(step, user, staff_name="応援の作り物の花子")
    body = client_logged_in.get(reverse("progress:progress_update", args=[helper.pk])).content.decode()
    assert 'value="応援の作り物の花子"' in body

    record = _record(step, user, staff=taro)
    response = _edit(client_logged_in, record, staff_name="応援の作り物の花子")
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.staff is None and record.staff_name == "応援の作り物の花子"


def test_edit_keeps_fields_for_record_with_worker_after_all_staff_inactive(client_logged_in, user, step, taro):
    """使用中の社員が 0 人になっても、社員か名前の写しが入っている実績の編集には 2 つの欄を出す (消さないため)。"""
    record = _record(step, user, staff=taro)
    helper = _record(step, user, staff_name="応援の作り物の花子")
    bare = _record(step, user)
    masters_services.update_staff(taro, {"active": False}, user)
    for row in (record, helper):
        body = client_logged_in.get(reverse("progress:progress_update", args=[row.pk])).content.decode()
        assert all(field in body for field in FIELDS), row.pk
    body = client_logged_in.get(reverse("progress:progress_update", args=[bare.pk])).content.decode()
    assert not [field for field in FIELDS if field in body]
    response = _edit(client_logged_in, record, staff_code="0012")
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.staff == taro


def test_edit_without_fields_keeps_worker(client_logged_in, user, step):
    """欄の無い編集 (社員が 0 人) は作業した人を渡さない (services の既定 KEEP で変えない)。"""
    bare = _record(step, user)
    response = _edit(client_logged_in, bare, staff_code="12", staff_name="送っても使わない名前")
    assert response.status_code == 302
    bare.refresh_from_db()
    assert (bare.staff, bare.staff_name, bare.staff_code) == (None, "", "")


# ---------------------------------------------------------------- (h) 社員を消した後の写し


def test_deleted_copy_is_not_editable(client_logged_in, user, step, taro, jiro):
    record = _record(step, user, staff=jiro)
    masters_services.delete_staff(jiro, user)
    record.refresh_from_db()
    assert (record.staff, record.staff_code, record.staff_name) == (None, "0034", "作り物の次郎")

    edit_url = reverse("progress:progress_update", args=[record.pk])
    for response in (client_logged_in.get(edit_url), client_logged_in.get(edit_url, **MODAL)):
        body = response.content.decode()
        assert not [field for field in FIELDS if field in body]
    response = _edit(client_logged_in, record, staff_code="12", staff_name="書き換える名前")
    assert response.status_code == 302
    record.refresh_from_db()
    assert (record.staff, record.staff_code, record.staff_name) == (None, "0034", "作り物の次郎")
    assert record.qty == Decimal("2") and record.note == "直した"


# ---------------------------------------------------------------- (i) 実績の一覧の列・絞り・検索・CSV


@pytest.fixture
def three_records(user, step, taro, jiro):
    """社員を指す実績 (太郎)・消した社員の写しの実績 (次郎)・社員の一覧に居ない人の実績 (花子)。"""
    linked = _record(step, user, staff=taro)
    copied = _record(step, user, staff=jiro)
    masters_services.delete_staff(jiro, user)
    helper = _record(step, user, staff_name="応援の作り物の花子")
    return linked, copied, helper


def _pks(response):
    return {row["pk"] for row in response.context["rows"]}


def test_record_list_column_filter_search_and_csv(client_logged_in, taro, three_records):
    linked, copied, helper = three_records
    url = reverse("progress:record_list")
    response = client_logged_in.get(url)
    body = response.content.decode()
    assert "作業した人" in body
    assert 'name="f_staff"' in body and ">作業した社員</label>" in body
    assert f'<option value="{taro.pk}">{taro}</option>' in body
    assert "人" in body and "tester" in body  # ログインした人の列は今のまま
    for text in ("0012 作り物の太郎", "0034 作り物の次郎", "応援の作り物の花子"):
        assert text in body

    assert _pks(client_logged_in.get(url, {"f_staff": str(taro.pk)})) == {linked.pk}
    # 番号か名前で探すと、社員を指す実績 (社員の番号と名前) と写しだけの実績 (番号と名前の写し) の両方が出る
    assert _pks(client_logged_in.get(url, {"q": "作り物の"})) == {linked.pk, copied.pk, helper.pk}
    assert _pks(client_logged_in.get(url, {"q": "0012"})) == {linked.pk}
    assert _pks(client_logged_in.get(url, {"q": "0034"})) == {copied.pk}
    assert _pks(client_logged_in.get(url, {"q": "太郎"})) == {linked.pk}
    assert _pks(client_logged_in.get(url, {"q": "次郎"})) == {copied.pk}
    assert _pks(client_logged_in.get(url, {"q": "花子"})) == {helper.pk}

    text = client_logged_in.get(url, {"format": "csv"}).content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[0]
    assert "作業した人" in header and "人" in header
    column = header.index("作業した人")
    assert sorted(row[column] for row in rows[1:]) == sorted(["0012 作り物の太郎", "0034 作り物の次郎", "応援の作り物の花子"])


def test_record_list_sorts_by_staff_code(client_logged_in, three_records):
    linked, copied, helper = three_records
    response = client_logged_in.get(reverse("progress:record_list"), {"sort": "staff_name"})
    assert response.status_code == 200
    assert response.context["sort"] == "staff_name"  # 見出しの「作業した人」で並び替えられる (鍵は社員の番号)
    pks = [row["pk"] for row in response.context["rows"]]
    assert linked.pk in (pks[0], pks[-1])  # 社員の無い 2 件は一続き (空の値の置き場は DB で違う)


def test_record_list_reads_staff_in_one_query(client_logged_in, user, step, taro, jiro):
    """行ごとに社員を引かない (見せる字の関数が社員を読むので、一覧は社員を行と一緒に読む)。

    同じ 3 行の実績で、作業した人が空のときと、3 人の別の社員を指すときの問い合わせの数を比べる (行の数を変えない)。
    """
    url = reverse("progress:record_list")
    third = masters_services.create_staff({"code": "0056", "name": "作り物の三郎"}, user)
    records = [_record(step, user) for _ in range(3)]
    client_logged_in.get(url)
    with CaptureQueriesContext(connection) as bare:
        client_logged_in.get(url)
    for record, staff in zip(records, (taro, jiro, third)):
        services.update_progress(record, Decimal("1"), RECORDED_AT, user, staff=staff)
    with CaptureQueriesContext(connection) as linked:
        response = client_logged_in.get(url)
    assert {row["obj"].get_staff_name_display() for row in response.context["rows"]} == {
        "0012 作り物の太郎",
        "0034 作り物の次郎",
        "0056 作り物の三郎",
    }
    assert len(linked) == len(bare)


# ---------------------------------------------------------------- (j) 指示書の詳細の「進捗」のタブ


def test_workorder_detail_progress_tab_shows_worker(client_logged_in, user, step, taro):
    with_worker = _record(step, user, staff=taro)
    without = services.record_progress(step, Decimal("2"), datetime(2026, 11, 3, 11, 0, tzinfo=ZoneInfo("Asia/Tokyo")), user)
    body = client_logged_in.get(reverse("workorders:workorder_detail", args=[step.workorder.pk])).content.decode()
    assert "作業 0012 作り物の太郎" in body
    assert str(with_worker).endswith(" 作業 0012 作り物の太郎") and str(with_worker) in body
    assert " 作業 " not in str(without) and str(without) in body
