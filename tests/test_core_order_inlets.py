"""注文の入口 (N83): 取引先の一覧の「注文の入口」の列 (core の dashboard.annotate_order_inlet)。

見る所: 得意先 (両方を含む) のうち、受ける向きのつながりがある = seizou で直接、無くて今月を含む直近 3 か月に読み取りの行がある =
注文書の読み取り、それ以外 = 手入力。協力企業だけの取引先は空。一覧の問い合わせの数は行の数で増えない。
本物の API は呼ばない (読み取りの行は _ask_claude を作り物の返事に差し替えて extract_order で作る)。名前は作り物だけ。
"""

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.core import dashboard
from seizou.intake import services as intake_services
from seizou.intake.models import Extraction
from seizou.masters.models import Partner
from seizou.masters.services import create_partner
from tests.test_intake_extract import FAKE_KEY, refuse_api, reply_data, upload

TODAY = date(2026, 11, 3)
ENV_NAME = "SEIZOU_PARTNER_INLETCO_SECRET"


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(intake_services, "_ask_claude", refuse_api)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def read_order(monkeypatch, user):
    """作り物の返事で、得意先 customer の読み取りの行を 1 つ作って返す。"""

    def _read(customer):
        text = json.dumps(reply_data(), ensure_ascii=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
        monkeypatch.setattr(intake_services, "_ask_claude", lambda model, block: text)
        try:
            return intake_services.extract_order(upload(), customer, user)
        finally:
            monkeypatch.setattr(intake_services, "_ask_claude", refuse_api)

    return _read


def _partner(user, code, name, role="customer"):
    return create_partner({"code": code, "name": name, "role": role}, user)


@pytest.fixture
def partners(user, read_order):
    """C1 = 受ける向きのつながり、C2 = 今の読み取りの行、C3 = 4 か月前の読み取りの行だけ、C4 = 何も無い、
    B1 = 両方 (受ける向きのつながり)、S1 = 協力企業 (送る向きのつながり)。"""
    c1 = _partner(user, "C1", "作り物の直接の得意先")
    c2 = _partner(user, "C2", "作り物の読み取りの得意先")
    c3 = _partner(user, "C3", "作り物の前に読み取った得意先")
    c4 = _partner(user, "C4", "作り物の手入力の得意先")
    b1 = _partner(user, "B1", "作り物の両方の取引先", "both")
    s1 = _partner(user, "S1", "作り物の協力企業", "subcontractor")
    connect_services.register_connection(c1, "", ENV_NAME, "receive", user)
    connect_services.register_connection(b1, "", ENV_NAME, "receive", user)
    connect_services.register_connection(s1, "", ENV_NAME, "send", user)
    read_order(c2)
    old = read_order(c3)
    # 試験の中だけの直し: 読み取った日時を、今月を含む直近 3 か月の前 (2026-07) にする。
    Extraction.objects.filter(pk=old.pk).update(read_at=datetime(2026, 7, 15, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo")))
    return {"C1": c1, "C2": c2, "C3": c3, "C4": c4, "B1": b1, "S1": s1}


def test_partner_list_shows_order_inlet(client, user, partners):
    inlets = dict(
        dashboard.annotate_order_inlet(Partner.objects.all(), today=TODAY).values_list("code", "order_inlet")
    )
    assert inlets == {
        "C1": "seizou で直接",
        "C2": "注文書の読み取り",
        "C3": "手入力",
        "C4": "手入力",
        "B1": "seizou で直接",
        "S1": None,
    }

    client.force_login(user)
    url = reverse("masters:partner_list")
    body = client.get(url).content.decode()
    assert "注文の入口" in body
    for text in ("seizou で直接", "注文書の読み取り", "手入力"):
        assert text in body, text
    header = client.get(url + "?format=csv").content.decode("utf-8-sig").splitlines()[0]
    assert "注文の入口" in header


def test_partner_list_queries_do_not_grow(client, user):
    client.force_login(user)
    url = reverse("masters:partner_list")
    _partner(user, "Q1", "作り物の得意先 1")
    client.get(url)  # 設定とセッションの控えを温める

    with CaptureQueriesContext(connection) as one:
        assert client.get(url).status_code == 200
    for number in range(2, 7):
        _partner(user, f"Q{number}", f"作り物の得意先 {number}")
    with CaptureQueriesContext(connection) as six:
        assert client.get(url).status_code == 200
    assert len(six.captured_queries) == len(one.captured_queries)
