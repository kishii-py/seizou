"""生産管理ボードの「進捗」の字の協力企業の数 (見比べのまとめの M01 の 3 段目。N406c)。

今の工程を協力企業に出していれば、「進捗」の字の後ろに出ている数 (・協力企業 n) とまだ渡していない数 (・渡す前 n) が付く。
数は purchasing.services.annotate_subcontract の値で、盤の 1 ページは工程の問い合わせに Subquery で足す (問い合わせを増やさない)。
prefetch_for を通らない道 (受注の詳細の段の帯) は、今の工程 1 つだけを同じ関数で 1 回読み、同じ字になる。

作り物のデータは services で作る (tests/test_purchasing_subcontract.py の build_workorder と subcontract_po)。
Django の test client で HTML を見る (本物のブラウザは使わない)。発注はつながりの無い協力企業で作り、相手に送らない。
"""

import re
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.accounts import services as accounts_services
from seizou.core import dashboard
from seizou.masters import services as masters_services
from seizou.orders import board
from seizou.orders import services as orders_services
from seizou.orders.models import OrderLine
from seizou.progress import services as progress_services
from seizou.purchasing import services as purchasing_services
from seizou.workorders import services as workorders_services
from tests.query_helpers import count_queries
from tests.test_purchasing_subcontract import DAY, build_workorder, line_of, subcontract_po
from tests.test_purchasing_subcontract import fake, sent_partner  # noqa: F401 (送る相手の協力企業の fixture)

pytestmark = pytest.mark.django_db


def finish(user, step, qty="10"):
    """工程に完了の印の付いた実績を入れる (前の工程を終えて、次の工程を今の工程にする)。"""
    progress_services.record_progress(step, Decimal(qty), timezone.now(), user, completes_step=True)


def receive(user, line, qty):
    """協力企業からの戻りを受入で入れる (指示書の工程を指す明細なので、その工程の実績になる。R32)。"""
    return purchasing_services.receive(line_of(line.pk), Decimal(qty), DAY, user)


def board_progress(client):
    """生産管理ボード (prefetch_for を通る道) の「進捗」の枡の字を、受注の明細の pk ごとに返す。"""
    response = client.get(reverse("orders:orderline_list"))
    assert response.status_code == 200
    index = [col.label for col in response.context["columns"]].index("進捗")
    return {row["obj"].pk: row["cells"][index]["value"] for row in response.context["rows"]}


def plain_progress(line):
    """prefetch_for を通らない道 (受注の詳細の段の帯と同じ。取り直した明細の OrderLine.board) の「進捗」の字。"""
    return OrderLine.objects.get(pk=line.pk).board.progress_label


def both_progress(client, line):
    """2 つの道の字が同じことを確かめて、その字を返す。"""
    on_board = board_progress(client)[line.pk]
    assert plain_progress(line) == on_board
    return on_board


class _WithoutPurchasing:
    """purchasing の部品が入っていない会社の apps の代わり (is_installed だけを替え、ほかは本物に任せる)。"""

    def __getattr__(self, name):
        return getattr(django_apps, name)

    def is_installed(self, app_name):
        return app_name != "seizou.purchasing" and django_apps.is_installed(app_name)


def internal_line(user, wo):
    """協力企業に出す工程の無い指示書 (切断だけ) の受注の明細。"""
    order = orders_services.create_order(
        wo.customer,
        [{"item": wo.item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=wo.order.order_date,
    )
    line = order.lines.get()
    workorders_services.issue_workorder(line, Decimal("10"), [{"process": wo.processes[0], "planned_qty": Decimal("10")}], user)
    return line


# ---------------------------------------------------------------- (a) 「進捗」の字


def test_progress_label_shows_out_and_waiting_of_current_step(user, logged_in):
    wo = build_workorder(user)
    line = wo.order.lines.get()
    other = internal_line(user, wo)
    finish(user, wo.steps[0])
    # 協力企業に出していない行の字は今のまま (今の工程がめっきでも、発注の明細が無ければ数を足さない)
    assert both_progress(logged_in, line) == "めっき 0/10"
    assert both_progress(logged_in, other) == "切断 0/10"

    _po, po_line = subcontract_po(user, wo.subcontractor, wo.step)
    assert both_progress(logged_in, line) == "めっき 0/10・協力企業 10"
    # 受入を 2 回に分けて、工程に実績を 2 件作る (実績の Sum と協力企業の数の Subquery が同じ問い合わせで正しい値になる)
    receive(user, po_line, "2")
    receive(user, po_line, "2")
    assert both_progress(logged_in, line) == "めっき 4/10・協力企業 6"
    assert both_progress(logged_in, other) == "切断 0/10"

    # 渡した日を空にすると、出ている数でなく、まだ渡していない数になる
    purchasing_services.set_handed_on(line_of(po_line.pk), None, user)
    assert both_progress(logged_in, line) == "めっき 4/10・渡す前 6"

    # 両方あれば、協力企業 → 渡す前の順
    purchasing_services.set_handed_on(line_of(po_line.pk), DAY, user)
    _po2, waiting_line = subcontract_po(user, wo.subcontractor, wo.step, "2", handed_on=None)
    assert both_progress(logged_in, line) == "めっき 4/10・協力企業 6・渡す前 2"

    # 受入で明細が終わると、工程に完了の印が付いて今の工程が次 (検査) へ進み、協力企業の数は出ない
    receive(user, po_line, "6")
    assert both_progress(logged_in, line) == "検査 0/10"
    assert purchasing_services.waiting_qty(line_of(waiting_line.pk)) == Decimal("2")


def test_progress_label_without_purchasing_part_is_unchanged(user, monkeypatch):
    wo = build_workorder(user)
    line = wo.order.lines.get()
    finish(user, wo.steps[0])
    _po, po_line = subcontract_po(user, wo.subcontractor, wo.step)
    receive(user, po_line, "4")
    assert plain_progress(line) == "めっき 4/10・協力企業 6"

    # 協力企業の部品を使わない会社: prefetch_for の道も、通らない道も、今までの字のまま
    monkeypatch.setattr(board, "apps", _WithoutPurchasing())
    rows = board.prefetch_for(OrderLine.objects.filter(pk=line.pk))
    assert [row.board.progress_label for row in rows] == ["めっき 4/10"]
    assert plain_progress(line) == "めっき 4/10"


def test_done_and_empty_labels_are_unchanged(user, logged_in):
    wo = build_workorder(user)
    line = wo.order.lines.get()
    _po, po_line = subcontract_po(user, wo.subcontractor, wo.step)
    for step in wo.steps:
        finish(user, step)
    # 指示書が全部完了なら「完了」のまま (出した明細が残っていても数を足さない)
    assert both_progress(logged_in, line) == "完了"
    assert purchasing_services.out_qty(line_of(po_line.pk)) == Decimal("10")


def test_csv_progress_column_has_the_same_label(user, logged_in):
    wo = build_workorder(user)
    finish(user, wo.steps[0])
    _po, po_line = subcontract_po(user, wo.subcontractor, wo.step)
    receive(user, po_line, "4")
    csv_body = logged_in.get(reverse("orders:orderline_list"), {"format": "csv"}).content.decode()
    header, *rows = csv_body.lstrip("﻿").splitlines()
    index = header.split(",").index("進捗")
    assert [row.split(",")[index] for row in rows] == ["めっき 4/10・協力企業 6"]
    body = logged_in.get(reverse("orders:orderline_list")).content.decode()
    assert '<td class="whitespace-nowrap">めっき 4/10・協力企業 6</td>' in body


# ---------------------------------------------------------------- (b) 問い合わせの数


def subcontracted_rows(user, codes):
    """codes の数だけ、切断を終えてめっきを協力企業に出し (渡した日あり)、2 を受け入れた受注の明細を作る。"""
    lines = []
    for code in codes:
        wo = build_workorder(user, code=code)
        finish(user, wo.steps[0])
        _po, po_line = subcontract_po(user, wo.subcontractor, wo.step)
        receive(user, po_line, "2")
        lines.append(wo.order.lines.get())
    return lines


def test_board_query_count_does_not_grow_with_subcontracted_rows(user, logged_in):
    plain = []
    for code in ("1", "2", "3"):
        wo = build_workorder(user, code=code)
        finish(user, wo.steps[0])
        plain.append(wo)
    logged_in.get(reverse("orders:orderline_list"))  # 最初の 1 回の控え (session など) を外す
    without_po, response = count_queries(logged_in)
    assert len(response.context["rows"]) == 3

    for wo in plain:
        _po, po_line = subcontract_po(user, wo.subcontractor, wo.step)
        receive(user, po_line, "2")
    small, response = count_queries(logged_in)
    assert {row["obj"].board.progress_label for row in response.context["rows"]} == {"めっき 2/10・協力企業 8"}

    subcontracted_rows(user, ("4", "5", "6"))
    large, response = count_queries(logged_in)
    assert len(response.context["rows"]) == 6
    assert {row["obj"].board.progress_label for row in response.context["rows"]} == {"めっき 2/10・協力企業 8"}

    assert without_po == small == large, (without_po, small, large)


def test_plain_path_reads_the_current_step_once(user, monkeypatch):
    (line,) = subcontracted_rows(user, ("1",))

    def counted():
        fresh = OrderLine.objects.get(pk=line.pk)
        with CaptureQueriesContext(connection) as captured:
            label = fresh.board.progress_label
        return label, len(captured.captured_queries)

    with_part, with_count = counted()
    monkeypatch.setattr(board, "apps", _WithoutPurchasing())
    without_part, without_count = counted()
    assert (with_part, without_part) == ("めっき 2/10・協力企業 8", "めっき 2/10")
    # prefetch_for を通らない道は、今の工程 1 つだけを 1 回読む (工程の数や明細の数で増えない)
    assert with_count == without_count + 1


# ---------------------------------------------------------------- 今日やることの 2 行と、移る先の一覧の絞り
#
# 協力企業に出していない工程 = purchasing.services.steps_to_subcontract の工程 (行の数は工程の数)。押した先は指示書の一覧の
# 絞り「協力企業に出していない工程があるだけ」(f_to_subcontract=1) で、同じ関数の工程を持つ指示書が並ぶ (一覧の件数は指示書の数)。
# 協力企業に渡していない明細 = purchasing.services.lines_not_handed の明細 (行の数は明細の数)。押した先は発注の一覧の
# 絞り「渡していない明細があるだけ」(f_not_handed=1) で、同じ関数の明細を持つ発注が並ぶ (一覧の件数は発注の数)。

SECTION = re.compile(r'<section[^>]*aria-labelledby="flow-guide-title".*?</section>', re.S)
#: 部署・編集・金額の判定を全部 on にした会社
ALL_ON = {"department": "on", "write": "on", "money": "on"}
PASSWORD = "kumo-tests-2611"
NEW_KEYS = ("subcontract", "handover")


def task_row(user, key):
    """その人の「今日やること」の key の行 (無ければ None)。"""
    today = dashboard.today_tasks(user)
    return None if today is None else next((row for row in today["rows"] if row["key"] == key), None)


def new_keys(login_id):
    """ログイン ID の人の「今日やること」の行のうち、足した 2 行の key の並び。利用者の物は控えを持たないよう取り直す。"""
    today = dashboard.today_tasks(get_user_model().objects.get(username=login_id))
    return [] if today is None else [row["key"] for row in today["rows"] if row["key"] in NEW_KEYS]


def listed(client, url):
    response = client.get(url)
    assert response.status_code == 200
    return [item["obj"] for item in response.context["rows"]]


def home_section(client):
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    return SECTION.search(response.content.decode()).group(0)


def two_outsourced_workorder(user, wo):
    """協力企業に出す工程を 2 つ持つ指示書 (めっき → 熱処理)。"""
    heat = masters_services.create_process({"code": "HEAT9", "name": "熱処理", "is_outsourced": True}, user)
    order = orders_services.create_order(
        wo.customer,
        [{"item": wo.item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=wo.order.order_date,
    )
    return workorders_services.issue_workorder(
        order.lines.get(),
        Decimal("10"),
        [{"process": process, "planned_qty": Decimal("10")} for process in (wo.processes[1], heat)],
        user,
    )


def test_today_row_for_steps_not_yet_subcontracted(user, logged_in):
    first = build_workorder(user, code="1")
    second = build_workorder(user, code="2")
    internal_line(user, first)  # 協力企業に出す工程の無い指示書は数えず、並ばない
    row = task_row(user, "subcontract")
    assert (row["label"], row["count"], row["unit"], row["button"]) == ("協力企業に出していない工程", 2, "行", "指示書")
    assert row["url"] == reverse("workorders:workorder_list") + "?f_to_subcontract=1"
    assert set(listed(logged_in, row["url"])) == {first.workorder, second.workorder}
    section = home_section(logged_in)
    assert '協力企業に出していない工程 <span class="font-bold tabular-nums">2</span> 行' in section
    assert f'href="{row["url"]}">指示書<' in section

    # 発注を作ると (指示書の工程を指す明細ができると) その工程は数えない。発注を取り消すと戻る
    po, _line = subcontract_po(user, first.subcontractor, first.step)
    assert task_row(user, "subcontract")["count"] == 1
    assert listed(logged_in, row["url"]) == [second.workorder]
    purchasing_services.cancel_purchase_order(po, user)
    assert task_row(user, "subcontract")["count"] == 2

    # 工程に完了の印が付くと数えない。全部に付くと行ごと消える
    finish(user, second.step)
    assert task_row(user, "subcontract")["count"] == 1
    assert listed(logged_in, row["url"]) == [first.workorder]
    finish(user, first.step)
    assert task_row(user, "subcontract") is None
    assert listed(logged_in, row["url"]) == []


def test_steps_row_counts_steps_and_list_counts_workorders(user, logged_in):
    wo = build_workorder(user)
    double = two_outsourced_workorder(user, wo)
    row = task_row(user, "subcontract")
    # 今日やることの数は工程の行の数、押して開いた一覧は指示書の数 (同じ指示書の 2 つの工程は 1 行の指示書になる)
    assert row["count"] == 3 == purchasing_services.steps_to_subcontract().count()
    rows = listed(logged_in, row["url"])
    assert sorted(rows, key=lambda w: w.pk) == [wo.workorder, double]
    assert {step.workorder_id for step in purchasing_services.steps_to_subcontract()} == {w.pk for w in rows}


def test_today_row_for_lines_not_handed(user, logged_in, sent_partner):  # noqa: F811
    wo = build_workorder(user, code="1")
    subcontract_po(user, wo.subcontractor, wo.step)  # 渡した日のある明細は数えない
    other = build_workorder(user, code="2")
    waiting_po, waiting = subcontract_po(user, other.subcontractor, other.step, handed_on=None)
    row = task_row(user, "handover")
    assert (row["label"], row["count"], row["unit"], row["button"]) == ("協力企業に渡していない明細", 1, "行", "発注")
    assert row["url"] == reverse("purchasing:purchaseorder_list") + "?f_not_handed=1"
    assert listed(logged_in, row["url"]) == [waiting_po]
    section = home_section(logged_in)
    assert '協力企業に渡していない明細 <span class="font-bold tabular-nums">1</span> 行' in section
    assert f'href="{row["url"]}">発注<' in section

    # 渡した日を入れると消え、空に戻すと戻る
    purchasing_services.set_handed_on(line_of(waiting.pk), DAY, user)
    assert task_row(user, "handover") is None
    assert listed(logged_in, row["url"]) == []
    purchasing_services.set_handed_on(line_of(waiting.pk), None, user)
    assert task_row(user, "handover")["count"] == 1

    # 完了にした明細は数えない (完了を外すと戻る)
    receive(user, waiting, "3")
    purchasing_services.mark_completed(line_of(waiting.pk), user)
    assert task_row(user, "handover") is None
    purchasing_services.clear_completed(line_of(waiting.pk), user)
    assert task_row(user, "handover")["count"] == 1

    # 取り消した発注の明細は数えない
    third = build_workorder(user, code="3")
    cancelled_po, _cancelled = subcontract_po(user, third.subcontractor, third.step, handed_on=None)
    assert task_row(user, "handover")["count"] == 2
    purchasing_services.cancel_purchase_order(cancelled_po, user)
    assert task_row(user, "handover")["count"] == 1

    # 取り消した明細 (送った発注の変更で外した明細) は数えない。1 つの発注に 2 つの明細があっても、一覧は発注 1 件
    po = purchasing_services.create_purchase_order(
        sent_partner,
        [
            {"workorder_step": third.step, "qty": Decimal("10"), "unit_price": Decimal("30")},
            {"workorder_step": third.step, "qty": Decimal("5"), "unit_price": Decimal("30")},
        ],
        user,
        order_date=wo.order.order_date,
    )
    assert task_row(user, "handover")["count"] == 3
    assert sorted(listed(logged_in, row["url"]), key=lambda p: p.pk) == [waiting_po, po]
    first, second = po.lines.order_by("line_no")
    purchasing_services.change_purchase_order(po, {}, [{"line": first}], user)
    assert line_of(second.pk).is_cancelled
    assert task_row(user, "handover")["count"] == 2 == purchasing_services.lines_not_handed().count()
    assert sorted(listed(logged_in, row["url"]), key=lambda p: p.pk) == [waiting_po, po]


def test_rows_follow_write_money_and_departments(settings, user):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}
    build_workorder(user, code="1")  # 協力企業に出していない工程 1 つ
    other = build_workorder(user, code="2")
    subcontract_po(user, other.subcontractor, other.step, handed_on=None)  # 協力企業に渡していない明細 1 つ
    assert [row["key"] for row in dashboard.today_tasks()["rows"] if row["key"] in NEW_KEYS] == ["subcontract", "handover"]

    both = accounts_services.create_department("作り物の生産管理の部署", ["workorders", "purchasing"], [], None)
    buying = accounts_services.create_department("作り物の購買の部署", ["purchasing"], [], None)
    making = accounts_services.create_department("作り物の製造の部署", ["workorders"], [], None)
    accounts_services.issue_account("kanri", PASSWORD, [], [], {"full_control": True}, None)
    accounts_services.issue_account("seisan", PASSWORD, [both], ["slip_edit"], {"see_money": True}, None)
    accounts_services.issue_account("seisan-nomoney", PASSWORD, [both], ["slip_edit"], {}, None)
    accounts_services.issue_account("seisan-read", PASSWORD, [both], [], {"see_money": True}, None)
    accounts_services.issue_account("kobai", PASSWORD, [buying], ["slip_edit"], {"see_money": True}, None)
    accounts_services.issue_account("seizo", PASSWORD, [making], ["slip_edit"], {"see_money": True}, None)

    assert new_keys("kanri") == ["subcontract", "handover"]
    assert new_keys("seisan") == ["subcontract", "handover"]
    # 発注の新規登録は金額に関わる操作 (MONEY_ACTIONS)。渡した日は金額に関わらない (受入と同じ)
    assert new_keys("seisan-nomoney") == ["handover"]
    # 発注 (協力企業の部品) を書けない人には、どちらも出ない (発注の新規登録と渡した日は、同じ種類の編集の権限)
    assert new_keys("seisan-read") == []
    # 部署で指示書の部品を開けない人には、指示書の一覧へ移る行が出ない
    assert new_keys("kobai") == ["handover"]
    # 部署で発注の部品を開けない人には、どちらも出ない (発注を作る入口と渡した日の入口が発注の側にある)
    assert new_keys("seizo") == []


def test_task_targets_of_new_rows():
    targets = dashboard._task_targets()
    assert targets["subcontract"] == (
        "指示書",
        "workorders:workorder_list",
        {"f_to_subcontract": "1"},
        "purchasing.create_purchase_order",
    )
    assert targets["handover"] == ("発注", "purchasing:purchaseorder_list", {"f_not_handed": "1"}, "purchasing.set_handed_on")
    steps = {step["key"]: step for flow in dashboard.FLOWS for step in flow["steps"]}
    purchase = next(flow for flow in dashboard.FLOWS if flow["key"] == "purchase")
    assert [step["key"] for step in purchase["steps"]] == [
        "subcontract",
        "purchase_order",
        "reply",
        "shipping_notice",
        "handover",
        "receipt",
        "acceptance",
    ]
    assert (steps["subcontract"]["label"], steps["subcontract"]["url_name"]) == ("協力企業に出す", "workorders:workorder_list")
    assert (steps["handover"]["label"], steps["handover"]["url_name"]) == ("渡す", "purchasing:purchaseorder_list")
    assert steps["subcontract"]["next_text"] == "指示書の詳細の工程の行から、協力企業に出す工程を発注に出します。"
    assert steps["handover"]["next_text"] == "現物を協力企業に渡したら、発注の明細の ⋮ の「渡した日」で日を入れます。"


def test_counts_are_none_without_purchasing(monkeypatch):
    # 部品があれば数える (手を動かす物が無ければ 0)。無ければ None (今の数え方の関数と同じ形。偽の 0 を置かない)
    assert (dashboard._flow_count("subcontract"), dashboard._flow_count("handover")) == (0, 0)
    real = dashboard._model
    monkeypatch.setattr(dashboard, "_model", lambda app, name: None if app == "purchasing" else real(app, name))
    assert dashboard._flow_count("subcontract") is None
    assert dashboard._flow_count("handover") is None


def test_list_filters_show_their_labels(user, logged_in):
    body = logged_in.get(reverse("workorders:workorder_list"), {"f_to_subcontract": "1"}).content.decode()
    assert (
        'name="f_to_subcontract" value="1" class="checkbox checkbox-sm" checked data-list-filter>協力企業に出していない工程があるだけ</label>'
        in body
    )
    body = logged_in.get(reverse("purchasing:purchaseorder_list"), {"f_not_handed": "1"}).content.decode()
    assert 'name="f_not_handed" value="1" class="checkbox checkbox-sm" checked data-list-filter>渡していない明細があるだけ</label>' in body
