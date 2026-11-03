"""使う人の結果の数字 (N83): 1 行の転記の分の設定と、ダッシュボードの「手で打たずに受けた明細」のカード。

見る所: 設定 transcription_minutes_per_line (既定 1・画面から変えない・点検が良い)、カードの行数 (入口が読み取りか
届いた注文の、今月の取消でない受注の明細) と ≒ 転記の分の字、設定の値の戻し方、問い合わせの数。
日は TODAY を渡して固定し、dashboard_stats と画面を通す試験だけ今日 (timezone.localdate) で作る。名前は作り物だけ。
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.core import conf, dashboard, doctor, views
from seizou.core import services as core_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services

TODAY = date(2026, 11, 3)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物のシャフト", "unit": "個"}, user)


class _NoParts:
    """部品が 1 つも入っていない会社の apps の代わり。"""

    def get_model(self, app_label, name):
        raise LookupError(f"{app_label}.{name}")


def make_order(user, customer, item, count, *, source, order_date):
    """明細 count 行の受注を、入口 source で作る。"""
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("1"), "unit_price": Decimal("100")} for _ in range(count)],
        user,
        order_date=order_date,
        source=source,
    )


def make_month(user, customer, item, today):
    """today の月に、数える明細 5 行 (読み取り 2 + 届いた注文 3) と、数えない明細を作る。"""
    make_order(user, customer, item, 2, source="intake", order_date=today)
    make_order(user, customer, item, 3, source="inbox", order_date=today)
    make_order(user, customer, item, 4, source="manual", order_date=today)
    cancelled = make_order(user, customer, item, 1, source="intake", order_date=today)
    orders_services.cancel_order(cancelled, user)
    last_month = date(today.year, today.month, 1) - timedelta(days=1)
    make_order(user, customer, item, 1, source="inbox", order_date=last_month)


def test_minutes_setting_is_locked_and_doctor_is_good(client, user, settings):
    settings.SEIZOU = {}

    assert conf.setting("transcription_minutes_per_line") == 1
    assert "transcription_minutes_per_line" in conf.LOCKED_KEYS
    rows = doctor.check_settings()
    assert len(rows) == 1 and rows[0].ok, rows
    assert rows[0].label == "会社の選べる設定"
    with pytest.raises(ValueError):
        core_services.validate_setting("transcription_minutes_per_line", 2)

    client.force_login(user)
    body = client.get(reverse("core:settings")).content.decode()
    assert "1 行の転記にかかる分" in body


def test_hands_free_card_counts_intake_and_inbox_lines(client, user, customer, shaft):
    today = timezone.localdate()
    make_month(user, customer, shaft, today)

    assert dashboard.hands_free_lines(today) == 5
    stats = views.dashboard_stats()
    card = stats[-1]
    assert card["label"] == "手で打たずに受けた明細"
    assert card["value"] == 5
    assert card["unit"] == "行"
    assert card["detail"] == "≒ 転記 5 分"
    assert card["url"] is None
    assert "money" not in card
    assert "1 行 1 分で数える" in card["note"]
    assert len(stats) == 8

    client.force_login(user)
    body = client.get(reverse("core:home")).content.decode()
    assert "手で打たずに受けた明細" in body
    assert "≒ 転記 5 分" in body


def test_hands_free_card_hours_text(user, customer, shaft, settings):
    settings.SEIZOU = {"transcription_minutes_per_line": 15}
    today = timezone.localdate()
    make_month(user, customer, shaft, today)

    card = views.dashboard_stats()[-1]
    assert card["detail"] == "≒ 転記 1 時間 15 分"
    assert "1 行 15 分" in card["note"]

    assert dashboard.transcription_text(5) == "5 分"
    assert dashboard.transcription_text(75) == "1 時間 15 分"
    assert dashboard.transcription_text(120) == "2 時間"
    assert dashboard.transcription_text(0) == "0 分"

    settings.SEIZOU = {"transcription_minutes_per_line": 1.5}
    assert dashboard.saved_minutes(5) == 8  # 7.5 の四捨五入


def test_minutes_per_line_falls_back(db, settings):
    settings.SEIZOU = {"transcription_minutes_per_line": 1.5}
    assert dashboard.minutes_per_line() == Decimal("1.5")
    settings.SEIZOU = {"transcription_minutes_per_line": "2"}
    assert dashboard.minutes_per_line() == Decimal("2")
    for wrong in (0, -1, 61, "abc", True, None, [1]):
        settings.SEIZOU = {"transcription_minutes_per_line": wrong}
        assert dashboard.minutes_per_line() == Decimal(1), wrong


def test_hands_free_lines_one_query(user, customer, shaft, monkeypatch):
    make_month(user, customer, shaft, TODAY)

    with CaptureQueriesContext(connection) as captured:
        assert dashboard.hands_free_lines(TODAY) == 5
    assert len(captured.captured_queries) == 1

    monkeypatch.setattr(dashboard, "apps", _NoParts())
    assert dashboard.hands_free_lines(TODAY) is None
