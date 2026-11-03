"""選べる設定の画面からの変更 (seizou.core の SettingOverride・conf.setting の上書きと控え・services・設定の画面)。

見る所: 読む順 (画面の上書き → SEIZOU → 既定) と出所の字、process の中の控えの寿命と捨て方、db の無い試験で表を読まない事、
書く関数 (validate_setting・set_setting・clear_setting) の値の確かめと記録の 1 行、上書きが部品の読む所に届く事。
"""

from __future__ import annotations

import logging

import pytest
from django.contrib.auth import get_user_model

from seizou.core import conf, services
from seizou.core.models import SettingOverride


@pytest.fixture(autouse=True)
def fresh_setting_cache():
    # 試験の transaction は戻るが、process の中の控えは戻らない。前と後で捨て、ほかの試験に上書きを残さない。
    conf.reset_cache()
    yield
    conf.reset_cache()


@pytest.fixture
def manager(db):
    return get_user_model().objects.create_superuser("admin", password="pass-for-tests-only")


@pytest.fixture
def plain(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


def settings_records(caplog):
    return [r for r in caplog.records if r.name == "seizou.settings"]


# ---------------------------------------------------------------- 1 段目: 読む順と控え


def test_setting_prefers_screen_then_answer_then_default(manager, settings):
    settings.SEIZOU = {"over_shipment": "stop"}
    assert conf.setting("over_shipment") == "stop"

    services.set_setting("over_shipment", "off", manager)
    assert conf.setting("over_shipment") == "off"

    services.clear_setting("over_shipment", manager)
    assert conf.setting("over_shipment") == "stop"

    settings.SEIZOU = {}
    assert conf.setting("over_shipment") == "warn"
    with pytest.raises(KeyError):
        conf.setting("no_such_key")


def test_setting_source_names_three_places(manager, settings):
    settings.SEIZOU = {"over_shipment": "stop"}
    services.set_setting("over_shipment", "off", manager)
    assert conf.setting_source("over_shipment") == "画面" == conf.SOURCE_SCREEN

    services.clear_setting("over_shipment", manager)
    assert conf.setting_source("over_shipment") == "答え (settings.py)" == conf.SOURCE_ANSWER

    settings.SEIZOU = {}
    assert conf.setting_source("over_shipment") == "既定" == conf.SOURCE_DEFAULT
    with pytest.raises(KeyError):
        conf.setting_source("no_such_key")


def test_cache_holds_until_reset(manager):
    services.set_setting("over_shipment", "off", manager)
    assert conf.setting("over_shipment") == "off"

    # 表を直に変えても、控えが生きている間は控えの値
    SettingOverride.objects.filter(key="over_shipment").update(value="stop")
    assert conf.setting("over_shipment") == "off"

    conf.reset_cache()
    assert conf.setting("over_shipment") == "stop"
    assert conf.overrides() == {"over_shipment": "stop"}


def test_cache_expires_after_cache_seconds(manager, monkeypatch):
    base = 1000.0
    monkeypatch.setattr(conf, "_now", lambda: base)
    services.set_setting("over_shipment", "off", manager)
    assert conf.setting("over_shipment") == "off"

    SettingOverride.objects.filter(key="over_shipment").update(value="stop")
    monkeypatch.setattr(conf, "_now", lambda: base + conf.CACHE_SECONDS - 1)
    assert conf.setting("over_shipment") == "off"

    monkeypatch.setattr(conf, "_now", lambda: base + conf.CACHE_SECONDS + 1)
    assert conf.setting("over_shipment") == "stop"


def test_setting_without_db_falls_back_to_answer(settings):
    # db の fixture を付けない = pytest-django が DB を RuntimeError で止める道
    settings.SEIZOU = {"order_number": "ym_seq"}

    assert conf.setting("order_number") == "ym_seq"
    assert conf.setting_source("order_number") == "答え (settings.py)"
    assert conf.overrides() == {}
    # 読めなかった結果は控えない (migrate の後に読み直せるように)
    assert conf._cache is None
    assert conf.overrides() == {}


# ---------------------------------------------------------------- 1 段目: 書く関数


def test_set_setting_refuses_unknown_key(manager):
    with pytest.raises(KeyError):
        services.set_setting("no_such_key", "x", manager)
    with pytest.raises(KeyError):
        services.clear_setting("no_such_key", manager)
    assert not SettingOverride.objects.exists()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("over_shipment", "bogus"),
        ("partial_shipment", "yes"),
        ("access_control", {"department": "on"}),
        ("access_control", {"department": "maybe", "write": "on", "money": "on"}),
        ("assistant_model", "   "),
        ("assistant_model", "x" * 101),
        ("hidden_fields", "note"),
        ("menu", []),
    ],
)
def test_set_setting_refuses_bad_values(manager, key, value):
    with pytest.raises(ValueError) as caught:
        services.set_setting(key, value, manager)
    assert key in str(caught.value) and "ください" in str(caught.value)
    assert not SettingOverride.objects.exists()


def test_set_setting_normalizes_logs_and_records_user(manager, caplog):
    caplog.set_level(logging.INFO, logger="seizou.settings")

    row = services.set_setting("assistant_model", "  claude-x  ", manager)

    assert row.value == "claude-x"
    assert SettingOverride.objects.get(key="assistant_model").updated_by == manager
    assert conf.setting("assistant_model") == "claude-x"
    records = settings_records(caplog)
    assert len(records) == 1 and records[0].levelno == logging.INFO
    message = records[0].getMessage()
    assert message.startswith("設定を変えた: assistant_model ")
    assert repr(conf.DEFAULTS["assistant_model"]) in message and "'claude-x'" in message and "(admin)" in message

    services.set_setting("assistant_model", "claude-y", manager)
    assert SettingOverride.objects.filter(key="assistant_model").count() == 1
    assert conf.setting("assistant_model") == "claude-y"

    assert services.set_setting("hidden_fields", ["note", " note ", "", "unit"], manager).value == ["note", "unit"]
    assert conf.setting("hidden_fields") == ["note", "unit"]


def test_validate_setting_keeps_false_and_orders_access_control():
    # 真偽の False と空の並びは空でなく値 (表の JSONField にそのまま入る)
    assert services.validate_setting("partial_shipment", False) is False
    assert services.validate_setting("hidden_fields", []) == []
    assert services.validate_setting("over_workorder", "stop") == "stop"
    assert services.validate_setting("access_control", {"money": "on", "write": "off", "department": "log"}) == {
        "department": "log",
        "write": "off",
        "money": "on",
    }


def test_clear_setting_removes_row_and_logs(manager, settings, caplog):
    settings.SEIZOU = {"over_shipment": "stop"}
    services.set_setting("over_shipment", "off", manager)
    caplog.set_level(logging.INFO, logger="seizou.settings")

    assert services.clear_setting("over_shipment", manager) is None

    assert not SettingOverride.objects.filter(key="over_shipment").exists()
    assert conf.setting_source("over_shipment") == conf.SOURCE_ANSWER
    records = settings_records(caplog)
    assert len(records) == 1
    assert records[0].getMessage() == "設定を戻した: over_shipment 'off' → 'stop' (admin)"

    # 行が無い鍵を戻しても誤りにしない
    services.clear_setting("order_form", manager)
    assert len(settings_records(caplog)) == 2


def test_override_reaches_readers(manager):
    from seizou.orders.forms import hidden_names

    services.set_setting("access_control", {"department": "on", "write": "off", "money": "log"}, manager)
    assert conf.access_control("write") == "off"
    assert conf.access_control("department") == "on"

    services.set_setting("hidden_fields", ["note"], manager)
    assert "note" in hidden_names()
