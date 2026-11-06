"""使う人の結果の数字 (N83): 1 行の手入力の分の設定と、ダッシュボードの「手入力せずに取り込んだ明細」のカード。

見る所: 設定 transcription_minutes_per_line (既定 1・画面から変えない・点検が良い)、カードの行数 (入口が読み取りか
受信した注文の、今月の取消でない受注の明細) と入力を省けた時間の字、設定の値の戻し方、問い合わせの数、
読み取りと受信した注文を受注にした直後の緑の知らせの 1 文 (手入力の受注には付かない)、受付の平均の分
(承認の日時の列 Inbox.accepted_at は N82 が足す。列の無い main では無い方の枝だけが通り、ある方の枝は取り込みの後の main で回る)。
日は TODAY を渡して固定し、dashboard_stats と画面を通す試験だけ今日 (timezone.localdate) で作る。名前は作り物だけ。
"""

import json
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.connect import services as connect_services
from seizou.connect import signing
from seizou.connect.models import Inbox
from seizou.core import conf, dashboard, doctor, views
from seizou.core import services as core_services
from seizou.intake import services as intake_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.test_connect_signing import _new_secret
from tests.test_intake_extract import FAKE_KEY, REPLY_PATH, refuse_api, upload
from tests.test_r10_inbox_accept import _line, _payload

TODAY = date(2026, 11, 3)
ENV_NAME = "SEIZOU_PARTNER_OUTCOMECO_SECRET"


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
    """today の月に、数える明細 5 行 (読み取り 2 + 受信した注文 3) と、数えない明細を作る。"""
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
    assert "1 行の手入力にかかる分" in body


def test_hands_free_card_counts_intake_and_inbox_lines(client, user, customer, shaft):
    today = timezone.localdate()
    make_month(user, customer, shaft, today)

    assert dashboard.hands_free_lines(today) == 5
    stats = views.dashboard_stats()
    card = stats[-1]
    assert card["label"] == "手入力せずに取り込んだ明細"
    assert card["value"] == 5
    assert card["unit"] == "行"
    assert card["detail"] == "入力を省けた時間 ≒ 5 分"
    assert card["url"] is None
    assert "money" not in card
    assert "1 行 1 分で数える" in card["note"]
    assert len(stats) == 8

    client.force_login(user)
    body = client.get(reverse("core:home")).content.decode()
    assert "手入力せずに取り込んだ明細" in body
    assert "入力を省けた時間 ≒ 5 分" in body


def test_hands_free_card_hours_text(user, customer, shaft, settings):
    settings.SEIZOU = {"transcription_minutes_per_line": 15}
    today = timezone.localdate()
    make_month(user, customer, shaft, today)

    card = views.dashboard_stats()[-1]
    assert card["detail"] == "入力を省けた時間 ≒ 1 時間 15 分"
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


def _messages(response):
    return [str(message) for message in response.context["messages"]]


def test_accept_message_says_lines_not_typed(client, user, customer, shaft, monkeypatch):
    client.force_login(user)
    bracket = masters_services.create_item(
        {"part_no": "B-200", "name": "作り物のブラケット", "unit": "個", "standard_price": Decimal("240")}, user
    )

    # 注文書の読み取り (明細 2 行) を受注にする。本物の API は呼ばない。
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setattr(intake_services, "_ask_claude", lambda model, block: REPLY_PATH.read_text(encoding="utf-8"))
    extraction = intake_services.extract_order(upload(), customer, user)
    monkeypatch.setattr(intake_services, "_ask_claude", refuse_api)
    url = reverse("intake:extraction_accept", args=[extraction.pk])
    response = client.post(url, {"item_1": shaft.pk, "item_2": bracket.pk}, follow=True)
    assert _messages(response) == ["保存しました。明細 2 行を手入力せずに受注にしました。"]

    # 受信した注文 (明細 1 行) を受け入れる。
    secret = _new_secret()
    monkeypatch.setenv(ENV_NAME, secret)
    connect_services.register_connection(customer, "", ENV_NAME, "receive", user)
    msg_id = str(uuid.uuid4())
    body = json.dumps(_payload(msg_id, [_line(1, "A-100")]), ensure_ascii=False).encode("utf-8")
    inbox = connect_services.receive(body, signing.headers_for(secret, msg_id, body))
    response = client.post(reverse("connect:inbox_accept", args=[inbox.pk]), {"item_1": bracket.pk}, follow=True)
    messages = _messages(response)
    assert len(messages) == 1 and messages[0].endswith("明細 1 行を手入力せずに受注にしました。"), messages


def test_manual_order_has_no_saved_note(user, customer, shaft):
    order = make_order(user, customer, shaft, 2, source="manual", order_date=TODAY)
    assert getattr(order, "saved_note", "") == ""

    assert views.with_saved_note("保存しました。", None) == "保存しました。"
    note = SimpleNamespace(saved_note="明細 1 行を手入力せずに受注にしました。")
    assert views.with_saved_note("保存しました。", note) == "保存しました。明細 1 行を手入力せずに受注にしました。"
    assert views.with_saved_note("保存しました。", SimpleNamespace(saved_note=3)) == "保存しました。"


def test_average_minutes_rounds():
    tz = ZoneInfo("Asia/Tokyo")
    at = datetime(2026, 11, 3, 10, 0, tzinfo=tz)
    pairs = [(at, at + timedelta(minutes=12)), (at, at + timedelta(minutes=21))]
    assert dashboard._average_minutes(pairs) == 17  # 16.5 の四捨五入
    skipped = pairs + [(None, at), (at, None), (at, at - timedelta(minutes=5))]
    assert dashboard._average_minutes(skipped) == 17
    assert dashboard._average_minutes([]) is None


def test_accept_minutes_needs_column(user, customer, shaft):
    has_column = any(field.name == "accepted_at" for field in Inbox._meta.get_fields())
    if not has_column:
        # 承認の日時の列 (N82 が足す) が無い main: 問い合わせをせずに None で、カードの脚注に受付の平均を出さない。
        with CaptureQueriesContext(connection) as captured:
            assert dashboard.accept_minutes_this_month(TODAY) is None
        assert len(captured.captured_queries) == 0
        assert "受付の平均" not in views.dashboard_stats()[-1]["note"]
    else:
        # 列がある main (N82 の後): 今月に受信した注文 2 件を承認し、届いてから承認までを 12 分と 21 分にする。
        today = timezone.localdate()
        received = datetime(today.year, today.month, today.day, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        for number, minutes in ((1, 12), (2, 21)):
            payload = _payload(str(uuid.uuid4()), [_line(1, f"A-{number}00")])
            inbox = connect_services.record_inbox(customer, payload, user)
            connect_services.accept(inbox, {f"A-{number}00": shaft}, user, send=False)
            # 試験の中だけの直し: 届いた日時と承認の日時を決めた値にする。
            Inbox.objects.filter(pk=inbox.pk).update(
                received_at=received, accepted_at=received + timedelta(minutes=minutes)
            )
        with CaptureQueriesContext(connection) as captured:
            assert dashboard.accept_minutes_this_month(today) == 17
        assert len(captured.captured_queries) == 1
        assert "受付の平均 17 分" in views.dashboard_stats()[-1]["note"]


# ---------------------------------------------------------------- 知らせの文と色は service の返す物が持つ (N96 の前提。11-04)


def test_with_saved_note_uses_service_message_then_note():
    assert views.with_saved_note("保存しました。", SimpleNamespace()) == "保存しました。"
    assert views.with_saved_note("保存しました。", SimpleNamespace(saved_note="明細 1 行。")) == "保存しました。明細 1 行。"
    assert views.with_saved_note("保存しました。", SimpleNamespace(saved_message="相手に送りました。")) == "相手に送りました。"
    assert views.with_saved_note("保存しました。", SimpleNamespace(saved_message="", saved_note="x")) == "保存しました。x"
    assert views.with_saved_note("保存しました。", SimpleNamespace(saved_message=None, saved_note=None)) == "保存しました。"
