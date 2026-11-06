"""注文の入口の図 (N93。N83 の 25e1b5c と 2817da4 の形): ダッシュボードの「注文の入口 (今月)」の節 (core の dashboard.order_inlets)。

見る所: 得意先 (両方を含む) のうち、受ける向きのつながりがある = seizou で直接、無くて今月を含む直近 3 か月に読み取りの行がある =
注文書の読み取り、それ以外 = 手入力。協力企業だけの取引先は空 (annotate_order_inlet)。図は得意先ごとの丸と線が 1 つの受注の箱に集まり、
12 社を超えた分は「ほか n 社」の 1 行。問い合わせは 1 回。節の題の横に説明書の枠の「?」(part=inlets) がある。
本物の API は呼ばない (読み取りの行は _ask_claude を作り物の返事に差し替えて extract_order で作る)。名前は作り物だけ。
fixture は tests/test_core_order_inlets.py を import せず、このファイルの中に写した (N94 が同じ名前のファイルを作りうるため)。
"""

import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.connect import services as connect_services
from seizou.core import dashboard, views
from seizou.intake import services as intake_services
from seizou.intake.models import Extraction
from seizou.masters.models import Partner
from seizou.masters.services import create_item, create_partner
from seizou.orders import services as orders_services
from tests.test_intake_extract import FAKE_KEY, refuse_api, reply_data, upload

TODAY = date(2026, 11, 3)
ENV_NAME = "SEIZOU_PARTNER_INLETCO_SECRET"
CORE = Path(__file__).resolve().parent.parent / "src" / "seizou" / "core"


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


class _NoParts:
    """部品が 1 つも入っていない会社の apps の代わり。"""

    def get_model(self, app_label, name):
        raise LookupError(f"{app_label}.{name}")


@pytest.fixture
def shaft(user):
    return create_item({"part_no": "T-100", "name": "作り物のシャフト", "unit": "個"}, user)


def _order(user, customer, item, count, order_date):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("1"), "unit_price": Decimal("100")} for _ in range(count)],
        user,
        order_date=order_date,
    )


def _chart_part(body):
    """ダッシュボードの本文のうち、注文の入口の図の svg (id="inlet-chart") の中。"""
    start = body.index('<svg id="inlet-chart"')
    return body[start : body.index("</svg>", start)]


def test_annotate_order_inlet(partners):
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


def test_inlet_chart_has_circle_per_customer(client, user, partners, shaft):
    today = timezone.localdate()  # 画面を通すので今日 (図の今月は画面と同じ日で数える)
    _order(user, partners["C1"], shaft, 3, today)
    _order(user, partners["C2"], shaft, 2, today)

    inlets = dashboard.order_inlets(today)
    rows = inlets["rows"]
    assert [row["lines"] for row in rows[:2]] == [3, 2]
    assert len(rows) == 5  # 得意先 4 社と両方の 1 社 (協力企業は入らない)
    assert rows[0]["name"] == "作り物の直接の得意先" and rows[0]["inlet"] == "direct"
    assert rows[1]["inlet"] == "intake" and rows[1]["inlet_label"] == "注文書の読み取り"
    assert {row["inlet"] for row in rows[2:]} == {"direct", "manual"}
    assert inlets["total_lines"] == 5
    assert inlets["others"] is None

    client.force_login(user)
    body = client.get(reverse("core:home")).content.decode()
    assert "注文の入口 (今月)" in body
    chart = _chart_part(body)
    assert chart.count("<circle") == 5
    assert chart.count('class="fill-primary"') == 2  # 直接の C1 と両方の B1
    assert chart.count('class="fill-secondary"') == 1
    assert "受注" in chart and "今月 5 行" in chart
    # 節の題の横に説明書の枠の「?」(N93。2817da4 は題だけで、既にある枠の試験が落ちた)
    assert body.count('?part=inlets"') == 1
    assert 'aria-label="注文の入口の説明"' in body


def test_inlet_chart_others_after_twelve(client, user):
    long_name = "作り物のとても長い名前の得意先の株式会社さん"[:20]
    _partner(user, "K01", long_name)
    for number in range(2, 15):
        _partner(user, f"K{number:02d}", f"作り物の得意先 {number}")

    inlets = dashboard.order_inlets(TODAY)
    assert len(inlets["rows"]) == 12
    assert inlets["others"]["count"] == 2 and inlets["others"]["lines"] == 0
    assert inlets["others"]["y"]
    assert inlets["rows"][0]["name"] == long_name[:12] + "…"

    client.force_login(user)
    chart = _chart_part(client.get(reverse("core:home")).content.decode())
    assert chart.count("<circle") == 12
    assert "ほか 2 社" in chart


def test_order_inlets_one_query(client, user, monkeypatch):
    client.force_login(user)
    assert dashboard.order_inlets(TODAY) is None
    assert "注文の入口 (今月)" not in client.get(reverse("core:home")).content.decode()

    for number in range(1, 6):
        _partner(user, f"N{number}", f"作り物の得意先 {number}")
    with CaptureQueriesContext(connection) as captured:
        inlets = dashboard.order_inlets(TODAY)
    assert len(captured.captured_queries) == 1
    assert len(inlets["rows"]) == 5

    monkeypatch.setattr(dashboard, "apps", _NoParts())
    assert dashboard.order_inlets(TODAY) is None


def test_home_frames_can_shrink():
    # 幅 390 (精査 A7 の 1-2 の不具合 2): grid の中の 4 つの枠は min-w-0 を持ち、表と図の幅で列を押し広げない。
    # 頁の幅が 390 に収まることは test client では見られない (本体の手の確かめ)。ここは class の字と、作った CSS にその class があることを見る。
    html = (CORE / "templates" / "core" / "home.html").read_text(encoding="utf-8")
    for frame in ("shipping", "production", "recent-orders", "due-alerts"):
        tags = re.findall(rf'<section class="([^"]*)" aria-labelledby="{frame}-title">', html)
        assert len(tags) == 1, frame
        assert "min-w-0" in tags[0].split(), frame
    assert ".min-w-0{" in (CORE / "static" / "css" / "app.css").read_text(encoding="utf-8")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("minutes", "text"),
    [(59, "受付の平均 59 分"), (61, "受付の平均 1 時間 1 分"), (120, "受付の平均 2 時間")],
)
def test_accept_average_text(monkeypatch, minutes, text):
    # 「受付の平均」は 60 分からは「h 時間 m 分」(精査 A7 の 3 節の 2 で「受付の平均 2007 分」と出た)。字の作り方は入力を省けた時間と同じ transcription_text。
    monkeypatch.setattr(dashboard, "accept_minutes_this_month", lambda today=None: minutes)
    cards = [stat for stat in views.dashboard_stats() if stat["label"] == "手入力せずに取り込んだ明細"]
    assert len(cards) == 1
    assert cards[0]["note"].endswith(f"。{text}")
