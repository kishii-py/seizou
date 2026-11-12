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
from seizou.core.templatetags.seizou_static import stamped_static
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
    # URL には中身の印 ?v= が付く (N442)
    assert f'href="{stamped_static("css/report.css")}"' in body and 'href="/static/css/report.css?v=' in body
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
    # 主色は今の工程の 1 つだけ (見るのは「実績を入力」のボタンだけ。実績の無い指示書なので今の工程は切断)
    primary = [button for button in buttons if "btn-primary" in button]
    assert len(primary) == 1
    assert f"?workorder_step={steps[0].pk}&amp;" in primary[0]
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
    """完了の印の付いた実績がある工程に「完了」(初見テスト R5 の候補 C103 の PROG-1)。"""
    first, second = workorder.steps.order_by("seq")
    progress_services.record_progress(first, first.planned_qty, timezone.now(), user, completes_step=True)

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


# --- 読み取りの着地の工程ごとの実績・予定・残と今の工程、記録の後に戻る道 (初見テスト R5 の候補 C70) ---


def _scan_cards(body):
    """読み取りの着地の工程の札 (<li …>…</li> のうち「実績を入力」を含む物) を順に返す。"""
    return [card for card in re.findall(r"<li .*?</li>", body, re.S) if "実績を入力" in card]


def _card_text(card):
    """札の字 (tag を除いた物)。"""
    return re.sub(r"<[^>]+>", "", card)


def _scan_hidden_next(workorder):
    return f'<input type="hidden" name="next" value="{_scan_url(workorder)}">'


def _post_progress(client, step, qty, **extra):
    data = {"workorder_step": step.pk, "qty": qty, "recorded_at": "2026-11-03 15:00", "note": "", **extra}
    return client.post(reverse("progress:progress_create"), data)


@pytest.mark.django_db
def test_scan_input_links_carry_next(logged_in, workorder):
    """(a) どの「実績を入力」の href にも、この画面へ戻る ?next= が付く。"""
    body = logged_in.get(_scan_url(workorder)).content.decode()
    for step in workorder.steps.order_by("seq"):
        assert f"?workorder_step={step.pk}&amp;next={_scan_url(workorder)}" in body


@pytest.mark.django_db
def test_scan_next_reaches_progress_form(logged_in, workorder):
    """(b) ?next= 付きで開いた実績の新規登録は、「キャンセル」と form の hidden の next がこの画面を指す。"""
    first = workorder.steps.order_by("seq").first()
    response = logged_in.get(
        reverse("progress:progress_create"), {"workorder_step": first.pk, "next": _scan_url(workorder)}
    )
    assert response.status_code == 200
    assert response.context["cancel_url"] == _scan_url(workorder)
    assert _scan_hidden_next(workorder) in response.content.decode()


@pytest.mark.django_db
def test_scan_record_returns_to_scan(logged_in, workorder):
    """(c) 記録すると読み取りの着地へ戻り、切断の札に実績と残が出て、今の工程は切断のまま (2 < 4)。"""
    first = workorder.steps.order_by("seq").first()
    response = _post_progress(logged_in, first, "2", next=_scan_url(workorder))
    assert response.status_code == 302
    assert response["Location"] == _scan_url(workorder)

    cards = _scan_cards(logged_in.get(_scan_url(workorder)).content.decode())
    assert "切断" in cards[0]
    assert "実績 2 / 予定 4・残 2" in _card_text(cards[0])
    assert 'aria-current="step"' in cards[0]


@pytest.mark.django_db
def test_scan_record_over_plan_confirm_returns_to_scan(settings, logged_in, user, workorder):
    """(d) 予定を超えて黄の確かめで描き直しても、hidden の next を引き継ぎ、「このまま保存」の後に戻る。"""
    settings.SEIZOU = {"over_workorder": "warn"}
    first = workorder.steps.order_by("seq").first()
    progress_services.record_progress(first, Decimal("4"), timezone.now(), user)

    checked = _post_progress(logged_in, first, "1", next=_scan_url(workorder))
    assert checked.status_code == 200
    body = checked.content.decode()
    assert "このまま保存" in body
    assert _scan_hidden_next(workorder) in body

    confirmed = _post_progress(logged_in, first, "1", next=_scan_url(workorder), confirmed="1")
    assert confirmed.status_code == 302
    assert confirmed["Location"] == _scan_url(workorder)


@pytest.mark.django_db
def test_scan_current_moves_to_next_step(logged_in, user, workorder):
    """(e) 切断に完了の印を付けると、切断は「完了」で主色が外れ、今の工程と主色は曲げへ移る (PROG-1)。"""
    first, second = workorder.steps.order_by("seq")
    progress_services.record_progress(first, Decimal("4"), timezone.now(), user, completes_step=True)

    cut_card, bend_card = _scan_cards(logged_in.get(_scan_url(workorder)).content.decode())
    assert "完了" in cut_card
    assert "実績 4 / 予定 4・残 0" in _card_text(cut_card)
    assert "aria-current" not in cut_card
    assert "btn-primary" not in _input_buttons(cut_card)[0]
    assert 'aria-current="step"' in bend_card
    assert "実績 0 / 予定 4・残 4" in _card_text(bend_card)
    assert "btn-primary" in _input_buttons(bend_card)[0]


@pytest.mark.django_db
def test_scan_reached_step_without_mark_stays_current(logged_in, user, workorder):
    """切断が予定に届いても印が無ければ「完了」は出ず、今の工程と主色は切断のまま (PROG-1b = a)。"""
    first = workorder.steps.order_by("seq").first()
    progress_services.record_progress(first, Decimal("4"), timezone.now(), user)

    cut_card, bend_card = _scan_cards(logged_in.get(_scan_url(workorder)).content.decode())
    assert "完了" not in cut_card
    assert "実績 4 / 予定 4・残 0" in _card_text(cut_card)
    assert 'aria-current="step"' in cut_card
    assert "aria-current" not in bend_card


@pytest.mark.django_db
def test_scan_without_records_marks_first_step(logged_in, workorder):
    """(f) 実績の無い指示書は、切断の札だけが今の工程 (本文の aria-current="step" はちょうど 1 つ)。"""
    body = logged_in.get(_scan_url(workorder)).content.decode()
    cut_card, bend_card = _scan_cards(body)
    assert 'aria-current="step"' in cut_card
    assert "実績 0 / 予定 4・残 4" in _card_text(cut_card)
    assert "aria-current" not in bend_card
    assert body.count('aria-current="step"') == 1


@pytest.mark.django_db
def test_scan_all_steps_done_has_no_current(logged_in, user, workorder):
    """(g) 全部の工程に完了の印を付けると、今の工程は無く、主色のボタンも無い (ボタンは 2 つ残る)。"""
    for step in workorder.steps.order_by("seq"):
        progress_services.record_progress(step, Decimal("4"), timezone.now(), user, completes_step=True)

    body = logged_in.get(_scan_url(workorder)).content.decode()
    cards = _scan_cards(body)
    assert len(cards) == 2
    assert all("完了" in card for card in cards)
    assert 'aria-current="step"' not in body
    buttons = _input_buttons(body)
    assert len(buttons) == 2
    assert all("btn-primary" not in button for button in buttons)


@pytest.mark.django_db
def test_scan_over_plan_shows_negative_remaining(logged_in, user, workorder):
    """(h) 予定を超えた実績は残をマイナスのまま出し (現物優先)、印を付ければ「完了」で、今の工程は曲げ。"""
    first = workorder.steps.order_by("seq").first()
    progress_services.record_progress(first, Decimal("5"), timezone.now(), user, completes_step=True)

    cut_card, bend_card = _scan_cards(logged_in.get(_scan_url(workorder)).content.decode())
    assert "完了" in cut_card
    assert "実績 5 / 予定 4・残 -1" in _card_text(cut_card)
    assert "aria-current" not in cut_card
    assert 'aria-current="step"' in bend_card


# --- 離れて読む字 (見つけ print-06。指示書 11-07 の予備の束の R8) ---


def _meta_row(body, label):
    """要点の表の行 (<tr …><th>label</th>…</tr>) の HTML。要点の表は外の表の行の中にあるので、別の <tr を含まない行だけを取る。"""
    rows = [row for row in re.findall(r"<tr[^>]*>(?:(?!<tr\b).)*?</tr>", body, re.S) if f"<th>{label}</th>" in row]
    assert len(rows) == 1, (label, rows)
    return rows[0]


@pytest.mark.django_db
def test_tag_key_fields_are_big_and_title_is_compact(logged_in, workorder):
    """品番・数量・納期は大きい字の行 (report-big)、ほかの行は本文のまま、題は小さい (report-title-compact)。数量には単位。"""
    body = logged_in.get(reverse("workorders:workorder_tag", args=[workorder.pk])).content.decode()
    for label in ("品番", "数量", "納期"):
        assert _meta_row(body, label).startswith('<tr class="report-big">'), label
    for label in ("発行日", "品名", "得意先", "受注番号", "得意先の注文番号"):
        assert _meta_row(body, label).startswith("<tr>"), label
    assert "<td>4 個</td>" in _meta_row(body, "数量")
    assert "<td>2026年11月20日</td>" in _meta_row(body, "納期")
    assert '<h1 class="report-title report-title-compact">現品票</h1>' in body
    assert workorder_tag_context(workorder)["tag"]["unit"] == "個"


def test_report_css_makes_key_fields_bigger_than_number_and_title():
    """report.css の字の大きさの順: 要点の行 (.report-big) > 指示書番号 > 現品票とラベルの題 > 本文。要点は太字。"""
    body, _ = reports.fetch_local(reports.BASE_URL + "static/css/report.css")
    css = body.decode("utf-8")

    def size(selector):
        match = re.search(r"(?m)^" + re.escape(selector) + r" \{[^}]*?font-size: ([\d.]+)pt", css)
        assert match, selector
        return float(match.group(1))

    assert size(".report-big td") > size(".report-tag-number") > size(".report-title-compact") > size("html")
    assert "font-weight: 700" in re.search(r"(?m)^\.report-big td \{[^}]*\}", css).group(0)
    assert ".report-label-qty" not in css
