"""発注の詳細の「段の帯」(案 4-A の発注。N151)。段の読み方 (purchasing.timeline.stage_of・stage_band) と、詳細の画面の帯と、
受注と発注が同じ core の断片 (core/_stage_band.html) を使うこと。

作り物のデータは全部 services で作る。相手への HTTP は偽の requests.post (tests.test_connect_send の FakePost) に差し替え、
鍵はテストのたびに作る (tests/test_purchasing_timeline.py と同じ形)。段は読むたびに表から読み直すので、
足した後は明細を取り直して渡す (_line)。user と logged_in は tests/conftest.py の物を借りる。
"""

import html
import re
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
import requests
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.urls import reverse
from django.utils import timezone

from seizou.connect import services as connect_services
from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.timeline import STAGES, stage_band, stage_of
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

ENV_NAME = "SEIZOU_PARTNER_TESTSUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def no_http(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


@pytest.fixture
def send_connection(monkeypatch, subcontractor, user):
    monkeypatch.setenv(ENV_NAME, _new_secret())
    return connect_services.register_connection(subcontractor, PARTNER_URL, ENV_NAME, "send", user)


def _order(user, partner, item, lines=1):
    return services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("25")} for _ in range(lines)],
        user,
        order_date=timezone.localdate(),
    )


def _line(po, line_no=1):
    """段を読み直すために明細を取り直す。"""
    return po.lines.get(line_no=line_no)


def _notice(line, shipment_no="SH-0001"):
    return connect_services.record_shipping_notice(
        line, Decimal("10"), timezone.localdate(), None, message_id=uuid.uuid4(), shipment_no=shipment_no
    )


def _failing_post():
    return FakePost(raises=requests.ConnectionError("作り物の失敗"))


def _detail(client, po):
    response = client.get(reverse("purchasing:purchaseorder_detail", args=[po.pk]))
    assert response.status_code == 200
    return response, response.content.decode()


def _band(body):
    start = body.index('<div id="po-stages"')
    return body[start : body.index("</div>", start)]


def _element(body, marker):
    """marker で始まる div の外側の HTML (入れ子の div を数えて、閉じる所まで)。"""
    start = body.index(marker)
    depth = 0
    for match in re.finditer(r"<div\b|</div>", body[start:]):
        depth += 1 if match.group() == "<div" else -1
        if depth == 0:
            return body[start : start + match.end()]
    raise AssertionError(f"{marker} の div が閉じていない")


@pytest.mark.django_db
def test_order_alone_is_at_the_first_stage(user, subcontractor, item, no_http):
    po = _order(user, subcontractor, item)

    assert stage_of(_line(po)) == 1
    band = stage_band(po)
    assert (band.current, band.current_name, band.summary) == (1, "発注", None)
    assert [(s.no, s.name, s.done, s.current) for s in band.stages] == [
        (no, name, False, no == 1) for no, name in enumerate(STAGES, start=1)
    ]


@pytest.mark.django_db
def test_stages_advance_from_sending_to_acceptance(monkeypatch, user, subcontractor, item, send_connection):
    monkeypatch.setattr("requests.post", FakePost())
    po = _order(user, subcontractor, item)
    assert stage_of(_line(po)) == 2  # 相手の受け口が受け取った = 送付

    po = services.mark_accepted(po, user)
    assert stage_of(_line(po)) == 3
    _notice(_line(po))
    assert stage_of(_line(po)) == 4
    monkeypatch.setattr("requests.post", _failing_post())
    receipt = services.receive(_line(po), Decimal("10"), timezone.localdate(), user)
    assert receipt.acceptance_sent_at is None
    assert stage_of(_line(po)) == 5  # 検収が相手に届いていない受入は「受入」のまま
    monkeypatch.setattr("requests.post", FakePost())
    assert connect_services.send_acceptance(receipt, user).acceptance_sent_at is not None
    assert stage_of(_line(po)) == 6
    assert (stage_band(po).current, stage_band(po).current_name) == (6, "検収")


@pytest.mark.django_db
def test_failed_send_does_not_count_and_receipt_alone_reaches_received(monkeypatch, user, subcontractor, item, send_connection):
    monkeypatch.setattr("requests.post", _failing_post())
    po = _order(user, subcontractor, item)
    assert (po.status, po.outbox_messages.count()) == (po.STATUS_DRAFT, 1)
    assert stage_of(_line(po)) == 1  # 送れなかった注文は数えない

    services.receive(_line(po), Decimal("4"), timezone.localdate(), user)

    assert stage_of(_line(po)) == 5  # 送付・回答・出荷の知らせが無くても、受入があれば「受入」(届いている一番先の段)
    assert [s.done for s in stage_band(po).stages] == [True, True, True, True, False, False]


@pytest.mark.django_db
def test_sent_status_and_rejected_reply(user, subcontractor, item, no_http):
    po = services.mark_sent(_order(user, subcontractor, item), user)
    assert stage_of(_line(po)) == 2  # 状態が「送った」(送った注文の行が無くても送付。種の注文回答の写しと同じ)

    rejected = services.mark_rejected(po, user, reason="作り物の断り")

    band = stage_band(rejected)
    assert (band.current, band.current_name) == (3, "回答")  # 断られたも注文回答 (状態の印は「断られた」のまま)


@pytest.mark.django_db
def test_order_stage_is_the_slowest_line_with_a_summary(monkeypatch, user, logged_in, subcontractor, item, send_connection):
    monkeypatch.setattr("requests.post", FakePost())
    po = services.mark_accepted(_order(user, subcontractor, item, lines=3), user)
    _notice(_line(po, 1))
    services.receive(_line(po, 2), Decimal("10"), timezone.localdate(), user)

    band = stage_band(po)

    assert [stage_of(_line(po, no)) for no in (1, 2, 3)] == [4, 6, 3]
    assert (band.current, band.current_name) == (3, "回答")
    assert band.summary == "明細 3 行のうち、検収 1 行・出荷の知らせ 1 行・回答 1 行"
    assert [s.done for s in band.stages] == [True, True, False, False, False, False]
    shown = _band(_detail(logged_in, po)[1])
    assert '<p class="mt-1 text-xs text-base-content/70">明細 3 行のうち、検収 1 行・出荷の知らせ 1 行・回答 1 行</p>' in shown


@pytest.mark.django_db
def test_cancelled_order_has_no_band(user, logged_in, subcontractor, item, no_http):
    po = services.cancel_purchase_order(_order(user, subcontractor, item), user, reason="作り物の取消")

    assert stage_band(po) is None
    assert 'id="po-stages"' not in _detail(logged_in, po)[1]


@pytest.mark.django_db
def test_detail_shows_band_inside_the_live_heading(monkeypatch, user, logged_in, subcontractor, item, send_connection):
    monkeypatch.setattr("requests.post", FakePost())
    po = services.mark_accepted(_order(user, subcontractor, item), user)
    _notice(_line(po))

    response, body = _detail(logged_in, po)

    assert "core/_stage_band.html" in [template.name for template in response.templates]
    # 帯は見出しの帯 (#po-heading) の中 = 2 秒ごとの入れ替え (hx-select-oob の #po-heading) で段も新しくなる。題と状態の印の後
    heading = _element(body, '<div id="po-heading"')
    assert heading.index("<h1") < heading.index('<div id="po-stages"')
    band = _band(body)
    assert 'data-stage-current="4"' in band
    assert band.count('data-content="✓"') == 3
    assert band.count("step-primary") == 4
    assert '<li class="step step-primary" aria-current="step">出荷の知らせ</li>' in band
    assert '<li class="step">受入</li>' in band
    assert "明細 " not in band


def test_stage_band_fragment_is_in_core_only():
    get_template("core/_stage_band.html")
    for name in ("orders/_stage_band.html", "purchasing/_stage_band.html"):
        with pytest.raises(TemplateDoesNotExist):
            get_template(name)


@pytest.mark.django_db
def test_receipt_link_opens_with_today_not_shipped_date(monkeypatch, user, logged_in, subcontractor, item, send_connection):
    """出荷の知らせの「受入を記録する」は受入日を渡さず、受入の画面の受入日は form の初期値の今日 (決め 11-05。初見テスト R4 の 2-1)。"""
    monkeypatch.setattr("requests.post", FakePost())
    po = services.mark_accepted(_order(user, subcontractor, item), user)
    today = timezone.localdate()
    # 相手の出荷日は今日と違う日にする (同じ日だと、出荷日を渡しても受入日が今日に見えて試験にならない)
    shipped = today - timedelta(days=3)
    connect_services.record_shipping_notice(
        _line(po), Decimal("10"), shipped, None, message_id=uuid.uuid4(), shipment_no="SH-0001"
    )

    body = _detail(logged_in, po)[1]

    # 相手の出荷日は知らせの行の頭に今までどおり出る
    assert f'<span class="font-mono">{shipped.isoformat()}</span>' in body
    hrefs = re.findall(r'href="([^"]*)">受入を記録する</a>', body)
    assert len(hrefs) == 1
    link = html.unescape(hrefs[0])
    assert "received_on=" not in link
    assert f"?line={_line(po).pk}&" in link
    assert "qty=10" in link

    response = logged_in.get(link)

    assert response.status_code == 200
    field = re.search(r'<input\b[^>]*\bname="received_on"[^>]*>', response.content.decode())
    assert field is not None
    value = re.search(r'\bvalue="([^"]*)"', field.group())
    assert value is not None
    assert value.group(1) == today.isoformat()
    assert value.group(1) != shipped.isoformat()
