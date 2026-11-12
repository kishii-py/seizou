"""納品書と出荷ラベルの QR の頭と、手元だけの住所で刷る時の黄色の 1 行 (本体の決め 11-12 13:1x。現品票と同じ扱い。形は tests/test_workorders_tag_site_url.py)。

設定の mail の site_url (設定の「メールの送り方」の「seizou の URL の頭」) が入っていれば、QR の頭に使う (core.reports.qr_url)。
空で、開いている住所の名前が 127.0.0.1 か localhost なら、印刷用の画面の上に黄色の 1 行を出す (紙と PDF には出さない。report.css の @media screen)。
- (a) 頭のある会社で 127.0.0.1 で開いても、QR の字は頭で始まり、黄色の 1 行は無い。
- (b) 頭が空で 127.0.0.1 か localhost で開くと、QR の字は開いている住所で、黄色の 1 行が題の後に 1 回だけある。
- (c) 頭が空で社内の番地で開くと、黄色の 1 行は無い。
- (d) 帳票の写し (core.services.report_copies の html) と、写しを開く口には、黄色の 1 行が無い (写しは request を持たない)。
- (e) PDF の部品が無い置き場で PDF の口を開くと (印刷用の HTML に落ちる道)、(b) の形で黄色の 1 行が PDF の知らせの前に出る。
- (f) PDF の口が PDF を返す時も、QR の字は頭で始まる。
納品書は得意先に渡す紙なので QR の中身の字を出さない (tests/test_shipping_label.py)。納品書の QR の字は response.context で見る。
Django の test client で HTML の字を見る。黄色の見た目と、紙と PDF に出ない事は、画面と紙では見ていない (記録に未確認と書く)。
fixture は tests/test_shipping_label.py の形を写した。名前と品番は作り物。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core import reports
from seizou.core.services import report_copies
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services

SITE = "https://seizou.example.jp"
LOCAL = "127.0.0.1:8001"
LAN = "192.168.1.10:8001"
NOTICE_CLASS = 'class="report-screen-notice"'
NOTICE_WORDS = "この PC の中だけの住所"
NOTE_HEAD = "この納品書の QR は、この PC の中だけの住所 ({host}) を指すので、スマホでは開けません。"
LABEL_HEAD = "この出荷ラベルの QR は、この PC の中だけの住所 ({host}) を指すので、スマホでは開けません。"
NOTICE_TAIL = "社内のスマホから開くには、設定の「メールの送り方」の「seizou の URL の頭」に、社内から開ける URL を入れてから刷ってください。"
PDF_NOTICE_WORDS = "PDF の準備がありません"
TITLE = '<h1 class="report-title'
CUSTOMER_LINE = 'class="report-customer"'

#: 2 つの帳票 → (印刷用の画面の口, PDF の口, QR が指す詳細の口, 黄色の 1 行の頭)
KINDS = {
    "note": ("shipping:deliverynote_print", "shipping:deliverynote_pdf", "shipping:deliverynote_detail", NOTE_HEAD),
    "label": ("shipping:shipment_label", "shipping:shipment_label_pdf", "shipping:shipment_detail", LABEL_HEAD),
}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def shipment(user):
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("50"), "unit_price": Decimal("120"), "unit": "箱"}],
        user,
        order_date=date(2026, 11, 2),
        customer_order_no="CPO-5512",
    )
    return services.ship(order.lines.get(), Decimal("37"), date(2026, 11, 5), user)


@pytest.fixture
def note(user, shipment):
    return services.create_delivery_note(shipment.order_line.order.customer, [shipment], user)


@pytest.fixture
def records(shipment, note):
    """帳票の種類 → その帳票の記録 (納品書は納品書、出荷ラベルは出荷)。"""
    return {"note": note, "label": shipment}


@pytest.fixture
def lan(settings):
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "192.168.1.10"]


def _detail_path(kind, record):
    return reverse(KINDS[kind][2], args=[record.pk])


def _get(client, name, record, host):
    response = client.get(reverse(name, args=[record.pk]), HTTP_HOST=host)
    assert response.status_code == 200
    return response


def _qr_text(response):
    """QR の中身の字。ラベルは QR の下の字 (QR の中身と同じ字) も見る。納品書は字を出さないので context だけ。"""
    qr_text = response.context["qr_text"]
    body = response.content.decode()
    if 'class="report-qr-text"' in body:
        found = re.findall(r'<p class="report-qr-text">(.*?)</p>', body, re.S)
        assert found == [qr_text], found
    return qr_text


def _notice(body):
    """黄色の 1 行の <p> (無ければ None)。"""
    return re.search(r'<p class="report-screen-notice" role="status">(.*?)</p>', body, re.S)


def _unavailable(template, context):
    raise reports.PdfUnavailable("試験のために PDF を作れない形にした")


# --- (a) 設定の URL の頭があれば QR の頭に使い、黄色の 1 行は出さない ---


@pytest.mark.django_db
@pytest.mark.parametrize("kind", sorted(KINDS))
@pytest.mark.parametrize("host", [LOCAL, "testserver"])
def test_site_url_heads_qr_and_hides_notice(settings, logged_in, records, kind, host):
    settings.SEIZOU = {"mail": {"site_url": SITE}}
    record = records[kind]
    response = _get(logged_in, KINDS[kind][0], record, host)
    body = response.content.decode()
    assert _qr_text(response) == SITE + _detail_path(kind, record)
    assert f"http://{host}" + _detail_path(kind, record) not in body
    assert NOTICE_CLASS not in body
    assert NOTICE_WORDS not in body


# --- (b) 頭が空で、開いている住所が手元だけの名前なら、黄色の 1 行を題の後に出す ---


@pytest.mark.django_db
@pytest.mark.parametrize("kind", sorted(KINDS))
@pytest.mark.parametrize("host", [LOCAL, "localhost:8001"])
def test_local_host_without_site_url_shows_notice(logged_in, records, kind, host):
    record = records[kind]
    response = _get(logged_in, KINDS[kind][0], record, host)
    body = response.content.decode()
    assert _qr_text(response) == f"http://{host}" + _detail_path(kind, record)
    notice = _notice(body)
    assert notice, "黄色の 1 行が無い"
    assert notice.group(1) == KINDS[kind][3].format(host=host.split(":")[0]) + NOTICE_TAIL
    assert body.count(NOTICE_CLASS) == 1
    assert body.count(NOTICE_WORDS) == 1
    # 位置: 題の後、得意先の「御中」の行 (頭の段の左の頭) の前
    assert body.index(TITLE) < notice.start() < body.index(CUSTOMER_LINE)


# --- (c) 頭が空でも、手元だけの名前でなければ出さない ---


@pytest.mark.django_db
@pytest.mark.parametrize("kind", sorted(KINDS))
@pytest.mark.parametrize("host", [LAN, "testserver"])
def test_other_host_without_site_url_has_no_notice(lan, logged_in, records, kind, host):
    record = records[kind]
    response = _get(logged_in, KINDS[kind][0], record, host)
    body = response.content.decode()
    assert _qr_text(response) == f"http://{host}" + _detail_path(kind, record)
    assert NOTICE_CLASS not in body
    assert NOTICE_WORDS not in body


# --- (d) 帳票の写しには黄色の 1 行が無い (写しは request を持たない) ---


@pytest.mark.django_db
def test_report_copy_has_no_notice(logged_in, note):
    # 同じ住所で開いた印刷用の画面には出る (写しに無い事が、出す形の外である事を見るため)
    printed = _get(logged_in, "shipping:deliverynote_print", note, LOCAL).content.decode()
    assert NOTICE_CLASS in printed

    copies = list(report_copies(note))
    assert len(copies) == 1
    assert NOTICE_CLASS not in copies[0].html
    assert NOTICE_WORDS not in copies[0].html

    response = logged_in.get(reverse("core:report_copy", args=[copies[0].pk]), HTTP_HOST=LOCAL)
    assert response.status_code == 200
    body = response.content.decode()
    assert NOTICE_CLASS not in body
    assert NOTICE_WORDS not in body


# --- (e) PDF を作れない時の印刷用の HTML にも、同じ行が PDF の知らせの前に出る ---


@pytest.mark.django_db
@pytest.mark.parametrize("kind", sorted(KINDS))
def test_pdf_fallback_shows_notice_before_pdf_notice(monkeypatch, logged_in, records, kind):
    monkeypatch.setattr("seizou.core.reports.render_pdf", _unavailable)
    record = records[kind]
    response = _get(logged_in, KINDS[kind][1], record, LOCAL)
    assert response["Content-Type"].startswith("text/html")
    body = response.content.decode()
    assert _qr_text(response) == f"http://{LOCAL}" + _detail_path(kind, record)
    notice = _notice(body)
    assert notice, "黄色の 1 行が無い"
    assert notice.group(1) == KINDS[kind][3].format(host="127.0.0.1") + NOTICE_TAIL
    assert body.count(NOTICE_CLASS) == 1
    assert body.index(TITLE) < notice.start() < body.index(PDF_NOTICE_WORDS) < body.index(CUSTOMER_LINE)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", sorted(KINDS))
def test_pdf_fallback_with_site_url_has_no_notice(monkeypatch, settings, logged_in, records, kind):
    settings.SEIZOU = {"mail": {"site_url": SITE}}
    monkeypatch.setattr("seizou.core.reports.render_pdf", _unavailable)
    record = records[kind]
    response = _get(logged_in, KINDS[kind][1], record, LOCAL)
    body = response.content.decode()
    assert PDF_NOTICE_WORDS in body
    assert _qr_text(response) == SITE + _detail_path(kind, record)
    assert NOTICE_CLASS not in body


# --- (f) PDF の口が PDF を返す時も、QR の字は頭で始まる ---


@pytest.mark.django_db
@pytest.mark.parametrize("kind", sorted(KINDS))
def test_pdf_qr_uses_site_url(monkeypatch, settings, logged_in, records, kind):
    settings.SEIZOU = {"mail": {"site_url": SITE}}
    seen = {}

    def fake_pdf(template, context):
        seen.update(context)
        return b"%PDF-1.4 fake"

    monkeypatch.setattr("seizou.core.reports.render_pdf", fake_pdf)
    record = records[kind]
    response = _get(logged_in, KINDS[kind][1], record, LOCAL)
    assert response["Content-Type"] == "application/pdf"
    assert seen["qr_text"] == SITE + _detail_path(kind, record)
