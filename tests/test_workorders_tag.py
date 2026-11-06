"""指示書の現品票 (印刷用の HTML と PDF) と、現品票の QR を読んだ先 (読み取りの着地)。

Django の test client で HTML を見る。印 pdf の試験は WeasyPrint と OS の pango が要る。
印刷の見た目 (A4 縦の上半分に収まるか・QR が 30mm 角か) とスマホのカメラで読めるかは朝に目で見る。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.core import reports
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress import services as progress_services
from seizou.workorders import services
from seizou.workorders.reports import WORKORDER_TAG_TEMPLATE, workorder_tag_context

ORDER_DATE = date(2026, 11, 2)
DUE_DATE = date(2026, 11, 20)
CUSTOMER_ORDER_NO = "CPO-7781"
PDF_NOTICE_WORDS = "PDF の準備がありません"
TAG_URL_NAMES = ("workorders:workorder_tag", "workorders:workorder_tag_pdf", "workorders:workorder_scan")


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
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
def order(user, customer, item):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": DUE_DATE}],
        user,
        order_date=ORDER_DATE,
        customer_order_no=CUSTOMER_ORDER_NO,
    )


@pytest.fixture
def line(order):
    return order.lines.get()


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="切断", sort_order=1)


@pytest.fixture
def bend(db):
    return Process.objects.create(code="BEND", name="曲げ", sort_order=2)


@pytest.fixture
def workorder(user, line, cut, bend):
    """工程 2 つ (1 = 切断、2 = 曲げ) の指示書。"""
    return services.issue_workorder(
        line,
        Decimal("4"),
        [{"process": cut, "planned_qty": Decimal("4")}, {"process": bend, "planned_qty": Decimal("4")}],
        user,
    )


def _scan_url(workorder):
    return reverse("workorders:workorder_scan", args=[workorder.pk])


def _row_with(body, words):
    """帳票の表の行 (<tr>…</tr>) のうち、words を含む行を 1 つ返す。"""
    rows = [row for row in re.findall(r"<tr>.*?</tr>", body, re.S) if words in row]
    assert len(rows) == 1, (words, rows)
    return rows[0]


def _input_buttons(body):
    """読み取りの着地の「実績を入力」の <a> の開きタグを並べて返す。"""
    return re.findall(r"<a [^>]*>実績を入力</a>", body)


@pytest.mark.django_db
def test_tag_views_require_login(client, workorder):
    for name in TAG_URL_NAMES:
        response = client.get(reverse(name, args=[workorder.pk]))
        assert response.status_code == 302, name
        assert response["Location"].startswith(reverse("core:login")), name


@pytest.mark.django_db
def test_tag_shows_workorder_and_steps(logged_in, workorder, order):
    response = logged_in.get(reverse("workorders:workorder_tag", args=[workorder.pk]))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    body = response.content.decode()

    for words in (workorder.wo_no, "T-100", "作り物の軸", "作り物の得意先", order.order_no, CUSTOMER_ORDER_NO):
        assert words in body, words
    # 工程は順のとおり
    assert body.index("切断") < body.index("曲げ")
    assert "<svg" in body and 'role="img"' in body
    # QR の下の字 = 読み取りの着地の絶対 URL
    assert "http://testserver" + _scan_url(workorder) in body
    assert 'href="/static/css/report.css"' in body
    assert "<form" not in body
    assert "csrf" not in body.lower()
    assert PDF_NOTICE_WORDS not in body


@pytest.mark.django_db
def test_tag_unplanned_step_says_unplanned(logged_in, user, workorder):
    first, second = workorder.steps.order_by("seq")
    services.plan_step(first, date(2026, 11, 10), date(2026, 11, 12), user)

    body = logged_in.get(reverse("workorders:workorder_tag", args=[workorder.pk])).content.decode()
    planned = _row_with(body, "切断")
    assert "2026年11月10日" in planned and "2026年11月12日" in planned
    assert "未計画" not in planned
    assert "未計画" in _row_with(body, "曲げ")
    assert second.planned_start is None


@pytest.mark.django_db
def test_tag_pdf_falls_back_to_html_when_unavailable(monkeypatch, logged_in, workorder):
    def unavailable(template, context):
        raise reports.PdfUnavailable("試験のために PDF を作れない形にした")

    monkeypatch.setattr("seizou.core.reports.render_pdf", unavailable)
    response = logged_in.get(reverse("workorders:workorder_tag_pdf", args=[workorder.pk]))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    body = response.content.decode()
    assert PDF_NOTICE_WORDS in body and "印刷用の画面をお使いください" in body
    assert workorder.wo_no in body
    assert "<svg" in body


@pytest.mark.pdf
@pytest.mark.django_db
def test_tag_pdf_is_returned_when_available(logged_in, workorder):
    response = logged_in.get(reverse("workorders:workorder_tag_pdf", args=[workorder.pk]))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert response["Content-Disposition"] == f'inline; filename="{workorder.wo_no}.pdf"'


@pytest.mark.pdf
@pytest.mark.django_db
def test_tag_pdf_is_single_a4_page(user, line):
    processes = [
        Process.objects.create(code=f"P{n}", name=f"作り物の工程{n}", sort_order=n) for n in range(1, 6)
    ]
    five = services.issue_workorder(
        line, Decimal("4"), [{"process": p, "planned_qty": Decimal("4")} for p in processes], user
    )
    context = {**workorder_tag_context(five), "qr_text": "http://testserver" + _scan_url(five)}

    document = reports._render_document(WORKORDER_TAG_TEMPLATE, context)

    assert len(document.pages) == 1
    page = document.pages[0]
    # A4 縦 = 210mm × 297mm (WeasyPrint の長さは CSS の px。1mm = 96 / 25.4 px)
    assert round(page.width * 25.4 / 96) == 210
    assert round(page.height * 25.4 / 96) == 297


@pytest.mark.django_db
def test_detail_has_tag_buttons(logged_in, workorder):
    response = logged_in.get(reverse("workorders:workorder_detail", args=[workorder.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "現品票" in body and "PDF" in body
    tag_url = reverse("workorders:workorder_tag", args=[workorder.pk])
    pdf_url = reverse("workorders:workorder_tag_pdf", args=[workorder.pk])
    assert f'href="{tag_url}" target="_blank"' in body
    assert f'href="{pdf_url}" target="_blank"' in body


@pytest.mark.django_db
def test_unknown_workorder_is_404(logged_in):
    for name in TAG_URL_NAMES:
        assert logged_in.get(reverse(name, args=[9999])).status_code == 404, name


@pytest.mark.django_db
def test_scan_links_each_step_to_progress_create(logged_in, workorder):
    response = logged_in.get(_scan_url(workorder))
    assert response.status_code == 200
    body = response.content.decode()
    assert workorder.wo_no in body
    steps = list(workorder.steps.order_by("seq"))
    for step in steps:
        assert reverse("progress:progress_create") + f"?workorder_step={step.pk}" in body
    buttons = _input_buttons(body)
    assert len(buttons) == len(steps)
    # 工程の数だけ並ぶので主色にしない (見るのは「実績を入力」のボタンだけ)
    assert all("btn-primary" not in button for button in buttons)
    assert "no-store" in response["Cache-Control"]


@pytest.mark.django_db
def test_scan_link_preselects_step(logged_in, workorder):
    step = workorder.steps.order_by("seq").last()
    response = logged_in.get(reverse("progress:progress_create") + f"?workorder_step={step.pk}")
    assert response.status_code == 200
    match = re.search(r'<select name="workorder_step".*?</select>', response.content.decode(), re.S)
    assert match, "指示書の工程の選ぶ欄が無い"
    select = match.group(0)
    assert f'<option value="{step.pk}" selected>' in select
    assert select.count(" selected>") == 1


@pytest.mark.django_db
def test_scan_shows_cancelled_status(logged_in, user, workorder):
    issued = logged_in.get(_scan_url(workorder)).content.decode()
    assert ">未着手</span>" in issued

    services.cancel_workorder(workorder, user, reason="作り物の取消")
    body = logged_in.get(_scan_url(workorder)).content.decode()
    assert ">取消</span>" in body
    # ボタンは消さない (取消の指示書を断るかは progress の側の話)
    assert len(_input_buttons(body)) == 2


@pytest.mark.django_db
def test_scan_marks_done_steps(logged_in, user, workorder):
    first, second = workorder.steps.order_by("seq")
    progress_services.record_progress(first, first.planned_qty, timezone.now(), user)

    body = logged_in.get(_scan_url(workorder)).content.decode()
    cards = [card for card in re.findall(r"<li .*?</li>", body, re.S) if "実績を入力" in card]
    assert len(cards) == 2
    done = [card for card in cards if "完了" in card]
    assert len(done) == 1
    assert "切断" in done[0]
    assert reverse("progress:progress_create") + f"?workorder_step={first.pk}" in done[0]
    assert f"?workorder_step={second.pk}" not in done[0]
    # 完了の工程にもボタンを残す (予定を超えた実数も記録する = 現物優先)
    assert len(_input_buttons(body)) == 2
