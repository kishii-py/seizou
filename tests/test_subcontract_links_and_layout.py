"""協力企業に出した工程の行と発注の明細の、ほかの部品の詳細へ移る link と、狭い画面の並びと、問い合わせの数。

- 指示書の詳細の工程の行の取引先の名前は、部署で発注を開ける人にだけ発注の詳細への link にする (開けない人には字だけ)。
- 発注の詳細の明細の「指示書 … の工程 …」は、部署で指示書を開ける人にだけ指示書の詳細への link にする。
  訳: 関わる記録の link は、部署でその部品を開ける人にだけ出す (土台の基本の表の値の link = record_url と同じ判定。作者の決め)。
  詳細の画面は部署で絞らないので、URL を直に開けば開く (押すと 403 になるわけではない)。部署の判定が on の会社だけで違いが出る。
- 発注の詳細は、指示書の工程を指す明細が増えても、指示書の工程の表を引く問い合わせが増えない (明細の並び lines_with_received が
  工程を一緒に読む。発注の詳細は 2 秒ごとに取り直すので、明細の数だけ問い合わせを増やさない)。
- 工程の行の左は、幅の狭い画面では取引先と数を工程の字の下の行に置く (同じ行に並べると工程の字がほとんど隠れた。class の期待で見る)。

作り物のデータは tests/test_purchasing_subcontract.py の build_workorder と subcontract_po で作る。会社と人と品番は作り物。
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.html import escape

from seizou.purchasing import services as purchasing_services
from tests.test_core_access import account
from tests.test_core_department_buttons import _departments
from tests.test_purchasing_subcontract import ORDER_DATE, build_workorder, subcontract_po

#: 部署の判定だけを on にする (編集の権限と金額の閲覧は on のまま)
DEPARTMENT_ON = {"access_control": {"department": "on", "write": "on", "money": "on"}}

User = get_user_model()


@pytest.fixture
def wo(user):
    return build_workorder(user)


def _open(client, person, url):
    """person でログインし直して url を開く (権限の控えは利用者の物ごとなので、毎回表から取り直す)。"""
    client.force_login(User.objects.get(pk=person.pk))
    response = client.get(url)
    assert response.status_code == 200, url
    return response.content.decode()


@pytest.mark.django_db
def test_step_row_partner_is_a_link_only_for_people_who_can_open_purchasing(settings, client, user, wo):
    po, line = subcontract_po(user, wo.subcontractor, wo.step)
    settings.SEIZOU = dict(DEPARTMENT_ON)
    depts = _departments()
    wo_url = reverse("workorders:workorder_detail", args=[wo.workorder.pk])
    po_href = f'href="{reverse("purchasing:purchaseorder_detail", args=[po.pk])}"'
    name = escape(wo.subcontractor.name)
    title = f'title="{po.po_no}-{line.line_no}"'

    # 製造の部署は指示書を開けるが、発注は開けない: 取引先の名前は字だけ
    seizo = _open(client, account("seizo", departments=[depts["seizo"]]), wo_url)
    assert f"<span {title}>{name}</span>" in seizo
    assert po_href not in seizo

    # 発注も開ける人 (2 つの部署): 発注の詳細への link
    both = _open(client, account("kanri", departments=[depts["seizo"], depts["sales"]]), wo_url)
    assert f'<a class="link link-primary" {po_href} {title}>{name}</a>' in both


@pytest.mark.django_db
def test_order_line_step_is_a_link_only_for_people_who_can_open_workorders(settings, client, user, wo):
    po, _line = subcontract_po(user, wo.subcontractor, wo.step)
    settings.SEIZOU = dict(DEPARTMENT_ON)
    depts = _departments()
    po_url = reverse("purchasing:purchaseorder_detail", args=[po.pk])
    wo_href = f'href="{reverse("workorders:workorder_detail", args=[wo.workorder.pk])}"'
    text = escape(f"指示書 {wo.workorder.wo_no} の工程 {wo.step.seq} {wo.step.process.name}")

    # 受注と出荷の部署は発注を開けるが、指示書は開けない: 工程の字だけ
    sales = _open(client, account("jimu", departments=[depts["sales"]]), po_url)
    assert text in sales
    assert wo_href not in sales

    # 指示書も開ける人: 指示書の詳細への link
    both = _open(client, account("kanri", departments=[depts["seizo"], depts["sales"]]), po_url)
    assert f'<a class="link link-primary" {wo_href}>{text}</a>' in both


@pytest.mark.django_db
def test_order_detail_reads_steps_with_the_lines(logged_in, user):
    """指示書の工程を指す明細が 1 行の発注と 3 行の発注で、指示書の工程の表を引く問い合わせの数が同じ (明細ごとに引かない)。

    発注の詳細には、明細ごとの問い合わせがほかに残っている (受入と、相手からの出荷の知らせ)。ここでは見ない。
    """
    made = [build_workorder(user, code=str(code)) for code in (1, 2, 3, 4)]
    partner = made[0].subcontractor

    def _order(steps):
        lines = [{"workorder_step": step, "qty": Decimal("10"), "unit_price": Decimal("30")} for step in steps]
        return purchasing_services.create_purchase_order(partner, lines, user, order_date=ORDER_DATE)

    one = _order([made[0].step])
    three = _order([made[1].step, made[2].step, made[3].step])

    def _step_queries(po):
        with CaptureQueriesContext(connection) as captured:
            response = logged_in.get(reverse("purchasing:purchaseorder_detail", args=[po.pk]))
        assert response.status_code == 200
        body = response.content.decode()
        for line in po.lines.select_related("workorder_step__workorder", "workorder_step__process"):
            step = line.workorder_step
            assert escape(f"指示書 {step.workorder.wo_no} の工程 {step.seq} {step.process.name}") in body
        return sum(1 for query in captured if 'FROM "workorders_workorderstep"' in query["sql"])

    assert _step_queries(three) == _step_queries(one)


@pytest.mark.django_db
def test_step_row_puts_partner_under_the_step_on_narrow_screens(logged_in, user, wo):
    """工程の行の左: 幅の狭い画面では縦に (工程の字の下に取引先と数)、640px から横に並べる。"""
    subcontract_po(user, wo.subcontractor, wo.step)
    body = logged_in.get(reverse("workorders:workorder_detail", args=[wo.workorder.pk])).content.decode()
    left = '<span class="flex min-w-0 flex-col gap-1 sm:flex-row sm:items-center sm:gap-2"><span class="flex min-w-0 items-center gap-2"><span class="truncate" title="'
    assert body.count(left) == wo.workorder.steps.count()
    # 取引先と数の箱は、工程の字と「完了」の印の箱の外 (下の行) に置く
    assert '</span><span class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-base-content/70">' in body
