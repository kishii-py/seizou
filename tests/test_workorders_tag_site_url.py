"""現品票の QR の頭と、手元だけの住所で刷る時の黄色の 1 行と、「現品票を読む」が通す住所 (精査の番号 10。本体の決め 11-11 14:2x)。

設定の mail の site_url (設定の「メールの送り方」の「seizou の URL の頭」) が入っていれば、QR の頭に使う。
空で、開いている住所の名前が 127.0.0.1 か localhost なら、印刷用の画面の上に黄色の 1 行を出す (紙と PDF には出さない。report.css の @media screen)。
「現品票を読む」は、開いている住所に加えて、site_url の名前とポートの札も通す。
Django の test client で HTML と CSS の字を見る。黄色の見た目と、紙と PDF に出ない事は、画面と紙では見ていない (記録に未確認と書く)。
fixture は tests/test_workorders_tag.py の形を写した。名前と品番は作り物。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core import reports
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.workorders import services

SITE = "https://seizou.example.jp"
LOCAL = "127.0.0.1:8001"
NOTICE_CLASS = 'class="report-screen-notice"'
NOTICE_HEAD = "この現品票の QR は、この PC の中だけの住所 ({host}) を指すので、スマホでは開けません。"
NOTICE_TAIL = "社内のスマホから開くには、設定の「メールの送り方」の「seizou の URL の頭」に、社内から開ける URL を入れてから刷ってください。"
PDF_NOTICE_WORDS = "PDF の準備がありません"
TITLE = '<h1 class="report-title'
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
NOT_TAG = "読み取れません。この QR は、このシステムの現品票の QR ではありません。"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def workorder(user):
    """工程 1 つ (切断) の指示書。"""
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": date(2026, 11, 20)}],
        user,
        order_date=date(2026, 11, 2),
    )
    cut = Process.objects.create(code="CUT", name="切断", sort_order=1)
    return services.issue_workorder(order.lines.get(), Decimal("4"), [{"process": cut, "planned_qty": Decimal("4")}], user)


def _scan_path(workorder):
    return reverse("workorders:workorder_scan", args=[workorder.pk])


def _tag(client, workorder, host):
    response = client.get(reverse("workorders:workorder_tag", args=[workorder.pk]), HTTP_HOST=host)
    assert response.status_code == 200
    return response.content.decode()


def _qr_text(body):
    """QR の下の字 (QR の中身と同じ字)。"""
    found = re.findall(r'<p class="report-qr-text">(.*?)</p>', body, re.S)
    assert len(found) == 1, found
    return found[0]


def _notice(body):
    """黄色の 1 行の <p> (無ければ None)。"""
    return re.search(r'<p class="report-screen-notice" role="status">(.*?)</p>', body, re.S)


def _unavailable(template, context):
    raise reports.PdfUnavailable("試験のために PDF を作れない形にした")


# --- (a) 設定の URL の頭があれば QR の頭に使い、黄色の 1 行は出さない ---


@pytest.mark.django_db
@pytest.mark.parametrize("host", ["testserver", LOCAL])
def test_site_url_heads_qr_and_hides_notice(settings, logged_in, workorder, host):
    settings.SEIZOU = {"mail": {"site_url": SITE}}
    body = _tag(logged_in, workorder, host)
    assert _qr_text(body) == SITE + _scan_path(workorder)
    assert f"http://{host}" + _scan_path(workorder) not in body
    assert NOTICE_CLASS not in body


# --- (b) 頭が空で、開いている住所が手元だけの名前なら、黄色の 1 行を題の後に出す ---


@pytest.mark.django_db
@pytest.mark.parametrize("host", [LOCAL, "localhost:8001"])
def test_local_host_without_site_url_shows_notice(logged_in, workorder, host):
    body = _tag(logged_in, workorder, host)
    assert _qr_text(body) == f"http://{host}" + _scan_path(workorder)
    notice = _notice(body)
    assert notice, "黄色の 1 行が無い"
    assert notice.group(1) == NOTICE_HEAD.format(host=host.split(":")[0]) + NOTICE_TAIL
    assert body.count(NOTICE_CLASS) == 1
    # 位置: 題の後、指示書番号 (上の段の左の頭) の前
    assert body.index(TITLE) < notice.start() < body.index('class="report-tag-number"')


@pytest.mark.django_db
def test_local_notice_comes_before_pdf_notice(monkeypatch, logged_in, workorder):
    """PDF を作れない時の印刷用の HTML (pdf_notice の口) にも同じ行が出て、PDF の知らせより前にある。"""
    monkeypatch.setattr("seizou.core.reports.render_pdf", _unavailable)
    response = logged_in.get(reverse("workorders:workorder_tag_pdf", args=[workorder.pk]), HTTP_HOST=LOCAL)
    assert response.status_code == 200
    body = response.content.decode()
    notice = _notice(body)
    assert notice, "黄色の 1 行が無い"
    assert body.index(TITLE) < notice.start() < body.index(PDF_NOTICE_WORDS)
    assert _qr_text(body) == f"http://{LOCAL}" + _scan_path(workorder)


# --- (c) 頭が空でも、手元だけの名前でなければ出さない ---


@pytest.mark.django_db
@pytest.mark.parametrize("host", ["192.168.1.10:8001", "testserver"])
def test_other_host_without_site_url_has_no_notice(settings, logged_in, workorder, host):
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "192.168.1.10"]
    body = _tag(logged_in, workorder, host)
    assert _qr_text(body) == f"http://{host}" + _scan_path(workorder)
    assert NOTICE_CLASS not in body


# --- (d) PDF の口でも、頭があれば QR の字がその頭になる ---


@pytest.mark.django_db
def test_pdf_qr_uses_site_url(monkeypatch, settings, logged_in, workorder):
    settings.SEIZOU = {"mail": {"site_url": SITE}}
    seen = {}

    def fake_pdf(template, context):
        seen.update(context)
        return b"%PDF-1.4 fake"

    monkeypatch.setattr("seizou.core.reports.render_pdf", fake_pdf)
    response = logged_in.get(reverse("workorders:workorder_tag_pdf", args=[workorder.pk]), HTTP_HOST=LOCAL)
    assert response["Content-Type"] == "application/pdf"
    assert seen["qr_text"] == SITE + _scan_path(workorder)


@pytest.mark.django_db
def test_pdf_fallback_qr_uses_site_url(monkeypatch, settings, logged_in, workorder):
    settings.SEIZOU = {"mail": {"site_url": SITE}}
    monkeypatch.setattr("seizou.core.reports.render_pdf", _unavailable)
    response = logged_in.get(reverse("workorders:workorder_tag_pdf", args=[workorder.pk]), HTTP_HOST=LOCAL)
    body = response.content.decode()
    assert PDF_NOTICE_WORDS in body
    assert _qr_text(body) == SITE + _scan_path(workorder)
    assert NOTICE_CLASS not in body


# --- (e) 黄色の 1 行は画面で見る時だけ出る (紙と PDF は print の media) ---


def test_report_css_shows_screen_notice_on_screen_only():
    body, _ = reports.fetch_local(reports.BASE_URL + "static/css/report.css")
    css = body.decode("utf-8")

    screen = re.search(r"@media screen \{(.*?)\n\}", css, re.S)
    assert screen, "@media screen の塊が無い"
    hide = re.search(r"^\.report-screen-notice \{\s*display: none;\s*\}", css, re.M)
    assert hide, "黄色の 1 行を隠す規則が無い"
    # 同じ強さの選び方では後に書いた方が勝つので、隠す規則が後にあると画面でも出ない
    assert hide.start() < screen.start()

    show = re.search(r"\.report-screen-notice \{(.*?)\}", screen.group(1), re.S)
    assert show, "media の screen の塊の中に黄色の 1 行の規則が無い"
    assert "display: block;" in show.group(1)
    assert "background:" in show.group(1)
    assert "color: #000;" in show.group(1)
    # 規則は隠す物と画面の物の 2 つだけ (print の media で出す規則は無い)
    assert css.count(".report-screen-notice {") == 2


# --- (f) 「現品票を読む」は、設定の URL の頭で刷った札を、別の住所で開いても通す ---


def _refused(body):
    """「現品票を読む」の小窓の赤い帯に、この会社の現品票の QR でない知らせがある (字は tag と空白を除いて比べる)。"""
    return "alert-error" in body and re.sub(r"\s+", "", NOT_TAG) in re.sub(r"<[^>]+>|\s+", "", body)


def _read(client, code, host=LOCAL):
    response = client.get(reverse("workorders:workorder_reader"), {"code": code}, HTTP_HOST=host, **MODAL)
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_reader_opens_tag_printed_with_site_url(settings, logged_in, workorder):
    settings.SEIZOU = {"mail": {"site_url": SITE}}
    for code in (SITE + _scan_path(workorder), f"http://{LOCAL}" + _scan_path(workorder)):
        body = _read(logged_in, code)
        assert not _refused(body), code
        assert workorder.wo_no in body and "実績を入力" in body, code


@pytest.mark.django_db
def test_reader_without_site_url_refuses_other_host(logged_in, workorder):
    body = _read(logged_in, SITE + _scan_path(workorder))
    assert _refused(body)
    assert workorder.wo_no not in body and "実績を入力" not in body


@pytest.mark.django_db
def test_reader_with_unreadable_site_url_keeps_open_host(settings, logged_in, workorder):
    """答えの YAML に読めない頭 (例 閉じていない [ ) があっても、500 にせず、開いている住所の札は今までどおり開く。"""
    settings.SEIZOU = {"mail": {"site_url": "http://[bad"}}
    body = _read(logged_in, f"http://{LOCAL}" + _scan_path(workorder))
    assert not _refused(body)
    assert workorder.wo_no in body and "実績を入力" in body
