"""AI の呼び出しの上限 (seizou.core.ai の guard・has_time・record_use・take_watch_call と、conf の ai_limits・ai_features) の試験。

本物の API は呼ばない。SDK (anthropic) は、例外の親子を本物に合わせた作り物の module (tests.test_core_ai の fake_anthropic) に差し替え、
部品の _ask_claude は作り物の返事を返す関数に差し替える。鍵は作り物の字だけ。利用者と会社名と品番は作り物。
締め切りの時計は ai._now を差し替えて進める。回数の数は一時の置き場のファイル (tests/settings.py の SEIZOU_AI_CACHE_DIR) で、
conftest の autouse の fixture が試験ごとに前と後で捨てる。
"""

import logging
import sys
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from seizou.assistant import services as assistant_services
from seizou.core import ai, conf
from seizou.feedback import chat as feedback_chat
from seizou.intake import services as intake_services
from tests.test_assistant_services import FAKE_KEY, refuse_api as refuse_chat_api, text_reply, tool_reply
from tests.test_core_ai import MESSAGES, fake_anthropic

#: 作り物の混み合いの字 (部品の本物の字とは別)
BUSY = "作り物の混み合い"
#: 記録に出てはいけない作り物の質問の字
QUESTION = "作り物の質問の字 AS-100 の在庫はいくつ"


@pytest.fixture
def sdk(monkeypatch):
    module = fake_anthropic()
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return module


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user("tester2", password="pass-for-tests-only")


def limits(settings, **values):
    settings.SEIZOU = {"ai_limits": values}


# ---- (a) 設定の鍵と読み方 ----


def test_defaults_and_locked_keys():
    assert conf.DEFAULTS["ai_limits"] == {"concurrent": 2, "per_hour": 30, "watch_per_day": 200}
    assert conf.DEFAULTS["ai_features"] == {"assistant": True, "intake": True, "feedback": True}
    assert "ai_limits" in conf.LOCKED_KEYS
    assert "ai_features" in conf.LOCKED_KEYS


def test_ai_limits_overlays_answer_and_falls_back(settings):
    settings.SEIZOU = {}
    assert conf.ai_limits() == {"concurrent": 2, "per_hour": 30, "watch_per_day": 200}
    limits(settings, per_hour=5)
    assert conf.ai_limits() == {"concurrent": 2, "per_hour": 5, "watch_per_day": 200}
    for bad in ("abc", -1, 0, True, 2.5):
        limits(settings, per_hour=bad)
        assert conf.ai_limits()["per_hour"] == 30, bad
    settings.SEIZOU = {"ai_limits": "作り物の書き損じ"}
    assert conf.ai_limits() == conf.DEFAULTS["ai_limits"]


def test_ai_feature_reads_bool_only(settings):
    settings.SEIZOU = {"ai_features": {"assistant": False, "intake": "no"}}
    assert conf.ai_feature("assistant") is False
    assert conf.ai_feature("intake") is True  # 真偽でない値は既定
    assert conf.ai_feature("feedback") is True  # 無い鍵は既定
    settings.SEIZOU = {"ai_features": []}
    assert conf.ai_feature("assistant") is True
    with pytest.raises(KeyError):
        conf.ai_feature("作り物の機能")


# ---- (b) 人ごと・機能ごとの 1 時間の回数 ----


def test_hourly_limit_per_user_and_feature(settings, user, other_user):
    limits(settings, per_hour=2)
    for _ in range(2):
        with ai.guard("assistant", user, busy=BUSY):
            pass
    with pytest.raises(ValueError, match="1 時間に 2 回") as caught:
        with ai.guard("assistant", user, busy=BUSY):
            raise AssertionError("上限の後に入れた")
    assert isinstance(caught.value, ai.LimitReached)
    assert "AI チャット" in str(caught.value)

    with ai.guard("assistant", other_user, busy=BUSY):
        pass
    with ai.guard("feedback", user, busy=BUSY):
        pass
    for _ in range(3):
        with ai.guard("assistant", user, busy=BUSY, hourly=False):
            pass


def test_guard_without_user_counts_in_one_bucket(settings):
    limits(settings, per_hour=1)
    with ai.guard("assistant", None, busy=BUSY):
        pass
    with pytest.raises(ValueError, match="1 時間に 1 回"):
        with ai.guard("assistant", None, busy=BUSY):
            pass


# ---- (c) process ごとの同時の数 ----


def test_concurrent_limit_and_release(user):
    with ai.guard("assistant", user, busy=BUSY), ai.guard("intake", user, busy=BUSY):
        with pytest.raises(ValueError) as caught:
            with ai.guard("feedback", user, busy=BUSY):
                raise AssertionError("同時の数の上限の後に入れた")
        assert str(caught.value) == BUSY
    with ai.guard("feedback", user, busy=BUSY):
        pass

    # 例外で抜けても semaphore を返す
    with pytest.raises(RuntimeError):
        with ai.guard("assistant", user, busy=BUSY):
            raise RuntimeError("作り物の失敗")
    with ai.guard("assistant", user, busy=BUSY), ai.guard("intake", user, busy=BUSY):
        pass


def test_busy_does_not_count_hour(settings, user):
    limits(settings, per_hour=1, concurrent=1)
    with ai.guard("intake", user, busy=BUSY, hourly=False):
        with pytest.raises(ValueError, match=BUSY):
            with ai.guard("assistant", user, busy=BUSY):
                pass
    with ai.guard("assistant", user, busy=BUSY):
        pass


# ---- (d) 1 つの質問の締め切り ----


def test_has_time_until_sixty_seconds_left(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(ai, "_now", lambda: now["t"])
    started = ai.clock()
    assert ai.has_time(started) is True
    now["t"] = 1010.0
    assert ai.has_time(started) is True
    now["t"] = 1015.0  # 残り 60 秒
    assert ai.has_time(started) is True
    now["t"] = 1016.0  # 残り 59 秒
    assert ai.has_time(started) is False
    now["t"] = 1020.0
    assert ai.has_time(started) is False


# ---- (e) 状態 404 の文 ----


def test_not_found_names_setting_key_inside_guard(sdk, user):
    with ai.guard("intake", user, busy=BUSY):
        with pytest.raises(ValueError) as caught:
            with ai.translate_errors(MESSAGES, model="claude-作り物"):
                raise sdk.APIStatusError(404)
        assert "intake_model" in str(caught.value)
        assert "claude-作り物" in str(caught.value)
        # model を渡さなければ今の字
        with pytest.raises(ValueError, match=r"^作り物: 届かない \(状態 404\)$"):
            with ai.translate_errors(MESSAGES):
                raise sdk.APIStatusError(404)
    # guard の外も今の字
    with pytest.raises(ValueError, match=r"^作り物: 届かない \(状態 404\)$"):
        with ai.translate_errors(MESSAGES, model="claude-作り物"):
            raise sdk.APIStatusError(404)
    # 404 の外の状態は今の字
    with ai.guard("assistant", user, busy=BUSY):
        with pytest.raises(ValueError, match=r"^作り物: 届かない \(状態 500\)$"):
            with ai.translate_errors(MESSAGES, model="claude-作り物"):
                raise sdk.APIStatusError(500)


# ---- (f) 使った記録 ----


def _ai_lines(caplog):
    return [record for record in caplog.records if record.name == "seizou.ai"]


def test_record_use_writes_one_info_line_without_contents(caplog, user):
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=1234, output_tokens=56),
        stop_reason="end_turn",
        _request_id="req_作り物",
        content=[SimpleNamespace(type="text", text=f"作り物の答え {QUESTION}")],
    )
    caplog.set_level(logging.INFO, logger="seizou.ai")
    with ai.guard("assistant", user, busy=BUSY):
        ai.record_use("claude-作り物", response)

    lines = _ai_lines(caplog)
    assert len(lines) == 1
    assert lines[0].levelno == logging.INFO
    text = lines[0].getMessage()
    for word in ("tester", "assistant", "claude-作り物", "1234", "56", "end_turn", "req_作り物"):
        assert word in text, word
    assert QUESTION not in text
    assert "作り物の答え" not in text


def test_record_use_survives_reply_without_usage(caplog):
    caplog.set_level(logging.INFO, logger="seizou.ai")
    ai.record_use("claude-作り物", SimpleNamespace(stop_reason="end_turn", content=[]))
    lines = _ai_lines(caplog)
    assert len(lines) == 1
    assert "claude-作り物" in lines[0].getMessage()


# ---- (g) 見張りの 1 日の枚数 ----


def test_take_watch_call_until_daily_limit(settings):
    limits(settings, watch_per_day=2)
    assert ai.take_watch_call() is True
    assert ai.take_watch_call() is True
    assert ai.take_watch_call() is False
    assert ai.take_watch_call() is False


# ---- 2. AI チャットとフィードバックの答えのループ (上限の with と締め切り) ----


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)


def ask_assistant(question, user):
    return assistant_services.answer(question, page_url="/", screen_name="ダッシュボード", user=user, history=[])


def ask_feedback(session, message, user):
    return feedback_chat.answer(session, message, user=user, screen="orders:order_list")


def clock_at(monkeypatch, start=1000.0):
    """ai._now を差し替えた時計。返す辞書の "t" を進めると、締め切りの残りが減る。"""
    now = {"t": start}
    monkeypatch.setattr(ai, "_now", lambda: now["t"])
    return now


def slow_tool_reply(monkeypatch, now, seconds=20.0):
    """AI チャットの _ask_claude を、毎回 tool_use の作り物の返事を返して時計を seconds 秒進める物に差し替える。呼ばれた model の一覧を返す。"""
    calls = []

    def _fake(model, system, messages, tools):
        calls.append(model)
        now["t"] += seconds
        return tool_reply("list_screens", {})

    monkeypatch.setattr(assistant_services, "_ask_claude", _fake)
    return calls


def test_assistant_stops_before_deadline(with_key, monkeypatch, user):
    now = clock_at(monkeypatch)
    calls = slow_tool_reply(monkeypatch, now)

    result = ask_assistant(QUESTION, user)

    assert len(calls) == 1  # 2 回目の頭で残りが 55 秒 (1 回の待ち 60 秒より短い) なので始めない
    assert result["text"] == assistant_services.MESSAGE_DEADLINE
    assert f"({ai.DEADLINE_SECONDS} 秒)" in result["text"]
    assert result["tool_calls"] == ["list_screens"]
    assert result["links"] == []


def test_assistant_is_busy_without_calling_api(with_key, monkeypatch, user, other_user):
    monkeypatch.setattr(assistant_services, "_ask_claude", refuse_chat_api)

    with ai.guard("intake", other_user, busy=BUSY), ai.guard("feedback", other_user, busy=BUSY):
        result = ask_assistant(QUESTION, user)

    assert result["text"] == assistant_services.MESSAGE_BUSY
    assert result["tool_calls"] == []
    assert result["proposals"] == []


def test_assistant_hourly_limit_refuses_third_question(settings, with_key, monkeypatch, user):
    limits(settings, per_hour=2)
    calls = []

    def _fake(model, system, messages, tools):
        calls.append(model)
        return text_reply("作り物の答え")

    monkeypatch.setattr(assistant_services, "_ask_claude", _fake)

    assert ask_assistant("1 つ目の作り物の質問", user)["text"] == "作り物の答え"
    assert ask_assistant("2 つ目の作り物の質問", user)["text"] == "作り物の答え"
    third = ask_assistant("3 つ目の作り物の質問", user)

    assert len(calls) == 2
    assert third["text"].startswith("AI チャットは 1 人 1 時間に 2 回までです。")
    assert third["tool_calls"] == []


def test_feature_switch_stops_only_that_feature(settings, with_key):
    settings.SEIZOU = {"ai_features": {"assistant": False}}
    assert assistant_services.is_available() is False
    assert feedback_chat.is_available() is True
    assert intake_services.is_available() is True

    settings.SEIZOU = {"ai_features": {"feedback": False}}
    assert assistant_services.is_available() is True
    assert feedback_chat.is_available() is False
    assert intake_services.is_available() is True


def test_assistant_feature_off_answers_without_api(settings, with_key, monkeypatch, user):
    settings.SEIZOU = {"ai_features": {"assistant": False}}
    monkeypatch.setattr(assistant_services, "_ask_claude", refuse_chat_api)

    assert ask_assistant(QUESTION, user)["text"] == assistant_services.MESSAGE_NO_KEY


def test_real_ask_claude_records_use_through_fake_sdk(sdk, with_key, caplog, user):
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=321, output_tokens=12),
        stop_reason="end_turn",
        _request_id="req_作り物",
        content=[SimpleNamespace(type="text", text="作り物の答え")],
    )
    seen = []

    def create(**kwargs):
        seen.append(kwargs)
        return response

    sdk.Anthropic.messages = SimpleNamespace(create=create)
    caplog.set_level(logging.INFO, logger="seizou.ai")

    result = ask_assistant(QUESTION, user)

    assert result["text"] == "作り物の答え"
    assert len(seen) == 1
    assert seen[0]["model"] == assistant_services.model_name()
    lines = _ai_lines(caplog)
    assert len(lines) == 1
    assert lines[0].levelno == logging.INFO
    text = lines[0].getMessage()
    for word in ("tester", "assistant", assistant_services.model_name(), "321", "12", "end_turn", "req_作り物"):
        assert word in text, word
    assert QUESTION not in text
    assert "作り物の答え" not in text


def test_real_ask_claude_names_setting_key_on_404(sdk, with_key, user):
    def create(**kwargs):
        raise sdk.APIStatusError(404)

    sdk.Anthropic.messages = SimpleNamespace(create=create)

    result = ask_assistant(QUESTION, user)

    assert "assistant_model" in result["text"]
    assert assistant_services.model_name() in result["text"]


def test_feedback_deadline_and_busy_keep_session(with_key, monkeypatch, user, other_user):
    now = clock_at(monkeypatch)
    calls = slow_tool_reply(monkeypatch, now)
    before = {"turns": [{"role": "user", "text": "a"}, {"role": "assistant", "text": "b"}], "questions": 1, "input_tokens": 3, "output_tokens": 4, "screen": ""}
    session = {feedback_chat.CHAT_KEY: dict(before)}

    result = ask_feedback(session, "作り物の困りごと", user)

    assert len(calls) == 1
    assert result == {"text": feedback_chat.MESSAGE_DEADLINE, "turns": [], "feedback": None}
    assert session == {feedback_chat.CHAT_KEY: before}

    monkeypatch.setattr(assistant_services, "_ask_claude", refuse_chat_api)
    with ai.guard("intake", other_user, busy=BUSY), ai.guard("assistant", other_user, busy=BUSY):
        result = ask_feedback(session, "作り物の困りごと", user)
    assert result == {"text": assistant_services.MESSAGE_BUSY, "turns": [], "feedback": None}
    assert session == {feedback_chat.CHAT_KEY: before}


def test_feedback_hourly_limit_is_separate_from_assistant(settings, with_key, monkeypatch, user):
    limits(settings, per_hour=1)

    def _fake(model, system, messages, tools):
        return text_reply("作り物の問い")

    monkeypatch.setattr(assistant_services, "_ask_claude", _fake)

    assert ask_assistant("作り物の質問", user)["text"] == "作り物の問い"
    assert ask_feedback({}, "作り物の困りごと", user)["text"] == "作り物の問い"  # 機能が違うので別に数える
    second = ask_feedback({}, "作り物の困りごと", user)
    assert second["text"].startswith("フィードバックの AI の聞き取りは 1 人 1 時間に 1 回までです。")
