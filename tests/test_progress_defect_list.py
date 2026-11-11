"""不良の一覧と、原因と対策を書く画面 (progress の ProgressDefectListView・ProgressDefectUpdateView。2 段目 N407b。見比べのまとめの M09)。

一覧の列 (区分は見せる字。消した区分は写しの名前、区分の無い行は「区分なし」) と CSV・絞り (期間・区分・工程・部品・対策の無い行だけ)・
土台の集計 (?view=summary) の軸と数の合計と割合 (区分を消した行と区分の無い行は「未設定」)・実績の一覧の集計の不良の数と合計がそろう事・
htmx の行の部分の ⋮ の「原因と対策を書く」・原因と対策を書いて一覧へ戻り、指示書の詳細の「履歴」に出る事・行の数で問い合わせが増えない事を見る。
区分と実績は services で作る (工程の行だけは R07 の試験と同じく表に直に作る)。名前は作り物だけ。
"""

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.core import access
from seizou.core.screens import all_screens
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress import services
from seizou.progress.models import ProgressDefect
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
OCT = datetime(2026, 10, 30, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
NOV = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
HX = {"HTTP_HX_REQUEST": "true"}
USECASE = re.compile(r"使う人: .+ / いつ: .+ / 何のために: .+ / 次にすること: .+")
LIST_HELP = (
    "実績で入れた不良の行。区分・工程・部品・月で絞り、「集計」で区分ごとの数と割合を見て CSV に出せます。"
    "区分を消した行と区分の無い行は、集計で「未設定」に入ります。"
)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


def _workorder(user, part_no, processes):
    customer = masters_services.create_partner({"code": f"C{part_no[-3:]}", "name": f"作り物の得意先 {part_no}", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": part_no, "name": f"作り物の部品 {part_no}", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("100"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )
    workorder = workorders_services.issue_workorder(
        order.lines.get(), Decimal("100"), [{"process": process, "planned_qty": Decimal("100")} for process in processes], user
    )
    return item, list(workorder.steps.order_by("seq"))


@pytest.fixture
def world(user):
    """部品 2 つ・工程 2 つ (切断・曲げ)・区分 3 つ (傷・寸法・錆。錆は後で消す) と、不良の行を持つ実績。

    10 月: 部品 A の切断に 傷 2。11 月: 部品 A の切断に 傷 1 と 寸法 3、部品 A の曲げに 区分なし 2 と 錆 4 (錆は消して写しに)、部品 B の切断に 不良の数 5 (区分なし)。
    """
    cut = Process.objects.create(code="CUT", name="切断")
    bend = Process.objects.create(code="BEND", name="曲げ")
    item_a, (a_cut, a_bend) = _workorder(user, "T-101", (cut, bend))
    item_b, (b_cut,) = _workorder(user, "T-102", (cut,))
    scratch = masters_services.create_defect_kind({"name": "作り物の傷", "sort_order": 1}, user)
    size = masters_services.create_defect_kind({"name": "作り物の寸法", "sort_order": 2}, user)
    rust = masters_services.create_defect_kind({"name": "作り物の錆", "sort_order": 3}, user)
    october = services.record_progress(a_cut, Decimal("5"), OCT, user, defects=[{"kind": scratch, "qty": Decimal("2"), "cause_note": "作り物の当たり"}])
    services.record_progress(
        a_cut, Decimal("5"), NOV, user, defects=[{"kind": scratch, "qty": Decimal("1")}, {"kind": size, "qty": Decimal("3")}]
    )
    services.record_progress(a_bend, Decimal("5"), NOV, user, defects=[{"kind": None, "qty": Decimal("2")}, {"kind": rust, "qty": Decimal("4")}])
    services.record_progress(b_cut, Decimal("5"), NOV, user, defect_qty=Decimal("5"))
    masters_services.delete_defect_kind(rust, user)
    return {
        "cut": cut,
        "bend": bend,
        "item_a": item_a,
        "item_b": item_b,
        "a_cut": a_cut,
        "scratch": scratch,
        "size": size,
        "october": october,
    }


def _get(client, **params):
    return client.get(reverse("progress:progressdefect_list"), params)


def _kinds(response):
    return sorted((row["obj"].get_kind_name_display(), row["obj"].qty) for row in response.context["rows"])


def _summary(client, url, **params):
    return client.get(url, {"view": "summary", **params}).context["summary"]


# ---------------------------------------------------------------- (a) 入口・列・CSV


def test_menu_has_defect_entry_after_records():
    from seizou.progress.urls import MENU

    names = [url_name for _label, url_name in MENU]
    assert names.index("progress:progressdefect_list") == names.index("progress:record_list") + 1
    assert ("不良", "progress:progressdefect_list") in MENU


def test_list_columns_and_kind_text(client_logged_in, world):
    response = _get(client_logged_in)
    assert response.status_code == 200
    headers = [str(spec.label) for spec in response.context["columns"]]
    assert headers == ["日時", "指示書の工程", "区分", "数", "原因", "対策"]
    assert _kinds(response) == sorted(
        [
            ("作り物の傷", Decimal("2")),
            ("作り物の傷", Decimal("1")),
            ("作り物の寸法", Decimal("3")),
            ("区分なし", Decimal("2")),
            ("作り物の錆", Decimal("4")),
            ("区分なし", Decimal("5")),
        ]
    )
    body = response.content.decode()
    assert response.context["screen_help"] == LIST_HELP
    assert "作り物の錆" in body and "区分なし" in body and "作り物の当たり" in body
    # 日時は分まで (日時の新しい順。10 月の行が最後)
    assert "2026-10-30 10:00" in body
    assert response.context["rows"][-1]["obj"].progress_id == world["october"].pk
    # 新規登録の入口は無い (行は実績で入れる)
    assert response.context["create_url"] is None


def test_csv_has_same_columns(client_logged_in, world):
    text = _get(client_logged_in, format="csv").content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["日時", "指示書の工程", "区分", "数", "原因", "対策"]
    assert sorted(row[2] for row in rows[1:]) == sorted(["作り物の傷", "作り物の傷", "作り物の寸法", "区分なし", "作り物の錆", "区分なし"])
    assert str(world["a_cut"]) in {row[1] for row in rows[1:]}


# ---------------------------------------------------------------- (b) 絞り


def test_filters_narrow_rows(client_logged_in, user, world):
    nov = {"f_recorded_from": "2026-11-01", "f_recorded_to": "2026-11-30"}
    assert len(_get(client_logged_in, **nov).context["rows"]) == 5
    assert _kinds(_get(client_logged_in, f_kind=world["scratch"].pk)) == [("作り物の傷", Decimal("1")), ("作り物の傷", Decimal("2"))]
    assert _kinds(_get(client_logged_in, f_process=world["bend"].pk)) == [("作り物の錆", Decimal("4")), ("区分なし", Decimal("2"))]
    assert _kinds(_get(client_logged_in, f_item=world["item_b"].pk)) == [("区分なし", Decimal("5"))]
    assert len(_get(client_logged_in, f_item=world["item_a"].pk, **nov).context["rows"]) == 4

    line = ProgressDefect.objects.get(kind=world["size"])
    services.update_defect_note(line, "", "作り物の対策", user)
    rows = _get(client_logged_in, f_no_countermeasure="1").context["rows"]
    assert len(rows) == 5 and line.pk not in {row["obj"].pk for row in rows}
    # 検索は指示書の番号・区分の名前・写し・原因・対策
    assert _kinds(_get(client_logged_in, q="作り物の対策")) == [("作り物の寸法", Decimal("3"))]
    assert _kinds(_get(client_logged_in, q="錆")) == [("作り物の錆", Decimal("4"))]
    assert _kinds(_get(client_logged_in, q="当たり")) == [("作り物の傷", Decimal("2"))]
    wo_no = world["a_cut"].workorder.wo_no
    assert len(_get(client_logged_in, q=wo_no).context["rows"]) == 5


# ---------------------------------------------------------------- (c) 集計


def test_summary_axes_and_kind_rows(client_logged_in, world):
    url = reverse("progress:progressdefect_list")
    summary = _summary(client_logged_in, url, g_by="kind", g_val="qty")
    assert [axis.key for axis in summary["axes"]] == ["recorded:month", "kind", "process", "item"]
    assert [measure.key for measure in summary["measures"]] == ["count", "qty"]
    assert summary["measure"].label == "数の合計"
    rows = [(row["label"], Decimal(str(row["value"])), str(row["share"])) for row in summary["rows"]]
    # 区分を消した行 (錆 4) と区分の無い行 (2 と 5) は「未設定」(最後の行)
    assert rows == [
        ("作り物の傷", Decimal("3"), "17.6"),
        ("作り物の寸法", Decimal("3"), "17.6"),
        ("未設定", Decimal("11"), "64.7"),
    ]
    assert Decimal(str(summary["total"])) == Decimal("17")

    by_month = _summary(client_logged_in, url, g_by="recorded:month", g_val="qty")
    assert [(row["label"], Decimal(str(row["value"]))) for row in by_month["rows"]] == [("2026-10", Decimal("2")), ("2026-11", Decimal("15"))]
    by_process = _summary(client_logged_in, url, g_by="process", g_val="count")
    # 工程の軸の字は土台の集計が工程の __str__ (コードと名前) で出す (絞りの候補の字と違う。土台の今の動き)
    assert [(row["label"], row["value"]) for row in by_process["rows"]] == [(str(world["bend"]), 2), (str(world["cut"]), 4)]
    by_item = _summary(client_logged_in, url, g_by="item", g_val="qty")
    assert {row["label"]: Decimal(str(row["value"])) for row in by_item["rows"]} == {
        str(world["item_a"]): Decimal("12"),
        str(world["item_b"]): Decimal("5"),
    }

    text = client_logged_in.get(url, {"view": "summary", "g_by": "kind", "g_val": "qty", "format": "csv"}).content.decode("utf-8-sig")
    lines = list(csv.reader(io.StringIO(text)))
    assert lines[0] == ["区分", "数の合計", "割合 (%)"]
    assert lines[-2][0] == "未設定" and lines[-1][0] == "合計"


def test_summary_total_matches_record_list(client_logged_in, world):
    """同じ期間の不良の一覧の数の合計と、実績の一覧の不良の数の合計がそろう (実績の不良の数は行の合計を services が書くため)。"""
    for period in ({}, {"f_recorded_from": "2026-11-01", "f_recorded_to": "2026-11-30"}):
        defects = _summary(client_logged_in, reverse("progress:progressdefect_list"), g_by="kind", g_val="qty", **period)
        records = _summary(client_logged_in, reverse("progress:record_list"), g_by="process", g_val="defect_qty", **period)
        assert Decimal(str(defects["total"])) == Decimal(str(records["total"])), period


# ---------------------------------------------------------------- (d) 行の ⋮ と原因と対策を書く


def _row_menu(body, line):
    row = re.search(rf'<tr id="row-{line.pk}"[^>]*>(.*?)</tr>', body, re.S)
    assert row, line
    return re.findall(r'<a[^>]*href="([^"]*)"[^>]*data-modal-form="([^"]*)"[^>]*>.*?(原因と対策を書く)</a>', row.group(1), re.S)


def test_rows_have_note_menu_in_full_page_and_htmx(client_logged_in, world):
    line = ProgressDefect.objects.get(kind=world["size"])
    path = reverse("progress:progressdefect_update", args=[line.pk])
    for response in (_get(client_logged_in), client_logged_in.get(reverse("progress:progressdefect_list"), {"f_kind": world["size"].pk}, **HX)):
        body = response.content.decode()
        [(href, modal, label)] = _row_menu(body, line)
        assert href == modal and href.startswith(f"{path}?next=/progress/defects/")
        assert "編集" not in label


def test_note_screen_saves_and_returns_to_list(client_logged_in, user, world):
    line = ProgressDefect.objects.get(kind=world["size"])
    url = reverse("progress:progressdefect_update", args=[line.pk])
    list_url = reverse("progress:progressdefect_list")
    response = client_logged_in.get(url, {"next": list_url})
    assert response.status_code == 200
    form = response.context["form"]
    assert list(form.fields) == ["cause_note", "countermeasure"]
    assert form.fields["countermeasure"].widget.__class__.__name__ == "Textarea"
    label = f"{line.progress.workorder_step} の不良 作り物の寸法 3 の原因と対策を保存する"
    assert form.submit_label == label
    assert label in response.content.decode()

    response = client_logged_in.post(url, {"cause_note": "作り物の刃の摩耗", "countermeasure": "作り物の刃を替えた", "next": list_url})
    assert response.status_code == 302
    assert response["Location"] == list_url
    [(level, text)] = [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]
    assert level == "success" and text.endswith("の不良 (作り物の寸法 3) の原因と対策を保存しました。")
    line.refresh_from_db()
    assert (line.kind_id, line.qty, line.cause_note, line.countermeasure) == (world["size"].pk, Decimal("3"), "作り物の刃の摩耗", "作り物の刃を替えた")
    body = client_logged_in.get(list_url).content.decode()
    assert "作り物の刃を替えた" in body

    workorder = line.progress.workorder_step.workorder
    detail = client_logged_in.get(reverse("workorders:workorder_detail", args=[workorder.pk])).content.decode()
    history = detail[detail.index('aria-label="履歴"') :]
    assert "作り物の刃を替えた" in history
    assert "作り物の寸法 3 を直した" in history


def test_row_history_hides_kind_name_copy(client_logged_in, user, world):
    """不良の行の履歴に「区分の名前の写し」を出さない (区分の欄と同じ字が並ぶだけで、使う人に意味が無い。本体の 11-11 16:3x の答え (2))。"""
    record = world["october"]
    line = record.defects.get()
    services.update_progress(
        record,
        Decimal("5"),
        OCT,
        user,
        defects=[{"pk": line.pk, "kind": world["scratch"], "qty": Decimal("2")}, {"kind": world["size"], "qty": Decimal("1")}],
    )
    workorder = record.workorder_step.workorder
    detail = client_logged_in.get(reverse("workorders:workorder_detail", args=[workorder.pk])).content.decode()
    history = detail[detail.index('aria-label="履歴"') :]
    assert "作り物の寸法 1 を作った" in history
    assert "不良の区分" in history
    assert "区分の名前の写し" not in history


# ---------------------------------------------------------------- (e) 問い合わせの数


def test_list_queries_do_not_grow_with_rows(client_logged_in, user, world):
    url = reverse("progress:progressdefect_list")
    keep = set(ProgressDefect.objects.order_by("pk").values_list("pk", flat=True)[:3])
    ProgressDefect.objects.exclude(pk__in=keep).delete()
    client_logged_in.get(url)
    with CaptureQueriesContext(connection) as few:
        response = client_logged_in.get(url, {"per": "50"})
    assert len(response.context["rows"]) == 3
    kinds = [world["scratch"], world["size"], None]
    for n in range(27):
        services.record_progress(world["a_cut"], Decimal("1"), NOV, user, defects=[{"kind": kinds[n % 3], "qty": Decimal("1")}])
    with CaptureQueriesContext(connection) as many:
        response = client_logged_in.get(url, {"per": "50"})
    assert len(response.context["rows"]) == 30
    assert len(many) == len(few)


# ---------------------------------------------------------------- (f) 権限とユースケース


def test_note_action_is_free_and_not_money():
    assert "progress.update_defect_note" in access.FREE_ACTIONS
    assert "progress.update_defect_note" not in access.MONEY_ACTIONS
    assert "progress.update_defect_note" not in access.ACTION_KINDS


def test_two_screens_have_usecase():
    by_url_name = {screen.url_name: screen for screen in all_screens()}
    listing = by_url_name["progress:progressdefect_list"]
    note = by_url_name["progress:progressdefect_update"]
    assert USECASE.fullmatch(listing.usecase) and USECASE.fullmatch(note.usecase)
    assert listing.usecase.startswith("使う人: 品質・生産管理・社長 / いつ: 品質の問合せが来たときと、月に 1 回の振り返り / ")
    assert note.usecase == "使う人: 品質 / いつ: 不良の原因を調べた後 / 何のために: 原因と対策を不良の行に残す / 次にすること: 不良の一覧で確かめる"
    assert (listing.name, note.name) == ("不良の一覧", "不良の原因と対策")
