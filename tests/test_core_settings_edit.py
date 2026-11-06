"""選べる設定の画面からの変更 (seizou.core の SettingOverride・conf.setting の上書きと控え・services・設定の画面)。

見る所: 読む順 (画面の上書き → SEIZOU → 既定) と出所の字、process の中の控えの寿命と捨て方、db の無い試験で表を読まない事、
書く関数 (validate_setting・set_setting・clear_setting) の値の確かめと記録の 1 行、上書きが部品の読む所に届く事。
"""

from __future__ import annotations

import logging

import pytest
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.core import conf, services
from seizou.core.models import SettingOverride


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
    assert conf.setting_source("over_shipment") == "この画面で変えた" == conf.SOURCE_SCREEN

    services.clear_setting("over_shipment", manager)
    assert conf.setting_source("over_shipment") == "導入の時に決めた" == conf.SOURCE_ANSWER

    settings.SEIZOU = {}
    assert conf.setting_source("over_shipment") == "初期値" == conf.SOURCE_DEFAULT
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
    assert conf.setting_source("order_number") == "導入の時に決めた"
    assert conf.overrides() == {}
    # 読めなかった結果は控えない (migrate の後に読み直せるように)
    assert conf._cache is None
    assert conf.overrides() == {}


def test_overrides_read_uses_a_savepoint(db):
    # 試験の transaction の中では atomic() が savepoint になり、SAVEPOINT と RELEASE SAVEPOINT も数えられる
    conf.reset_cache()
    with CaptureQueriesContext(connection) as queries:
        conf.overrides()

    sqls = [q["sql"] for q in queries.captured_queries]
    assert any("SAVEPOINT" in sql for sql in sqls)
    assert any(sql.startswith("SELECT") and "core_settingoverride" in sql for sql in sqls)


def test_overrides_read_failure_keeps_outer_transaction(db, monkeypatch):
    # 表の無い置き場の代わりに、表の名前を無い名前に差し替える (表を消す DDL と cursor は使わない)。
    # PostgreSQL では savepoint が無いと、失敗した読みの後の外の atomic の問い合わせが全部落ちる (CI の postgres の組が見る)
    conf.reset_cache()
    with transaction.atomic():
        with monkeypatch.context() as patch:
            patch.setattr(SettingOverride._meta, "db_table", "core_no_such_table_for_tests")
            assert conf.overrides() == {}
        assert conf._cache is None
        assert SettingOverride.objects.count() == 0
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


# ---------------------------------------------------------------- 2 段目: 設定の画面・入力の画面・戻す口


def edit_url(key):
    return reverse("core:setting_update", args=[key])


def clear_url(key):
    return reverse("core:setting_clear", args=[key])


@pytest.fixture
def manager_client(client, manager):
    client.force_login(manager)
    return client


@pytest.fixture
def plain_client(client, plain):
    client.force_login(plain)
    return client


def test_settings_screen_shows_source_and_edit_links_for_manager(manager_client, settings):
    settings.SEIZOU = {"over_shipment": "stop"}

    response = manager_client.get(reverse("core:settings"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "どこで決めたか" in body and "初期値" in body and "導入の時に決めた" in body
    assert edit_url("over_shipment") in body and ">編集<" in body
    # 左のメニューの括り (menu) は画面からは変えない (link を出さない)
    assert edit_url("menu") not in body


def test_plain_user_has_no_edit_link_and_is_refused(plain_client):
    response = plain_client.get(reverse("core:settings"))
    assert response.status_code == 200
    assert edit_url("over_shipment") not in response.content.decode()

    for response in (
        plain_client.get(edit_url("over_shipment")),
        plain_client.post(edit_url("over_shipment"), {"over_shipment": "stop"}),
        plain_client.post(clear_url("over_shipment")),
    ):
        assert response.status_code == 403
        body = response.content.decode()
        assert "この画面は開けません" in body and "アカウント管理の権限" in body
    assert "no-store" in plain_client.get(edit_url("over_shipment"))["Cache-Control"]
    assert not SettingOverride.objects.exists()


def test_anonymous_is_sent_to_login(client, db):
    for response in (client.get(edit_url("over_shipment")), client.post(clear_url("over_shipment"))):
        assert response.status_code == 302 and "/login/" in response["Location"]
    assert not SettingOverride.objects.exists()


def test_unknown_and_locked_keys_are_404(manager_client):
    assert manager_client.get("/settings/no_such_key/edit/").status_code == 404
    assert manager_client.get(edit_url("menu")).status_code == 404
    assert manager_client.post(clear_url("menu")).status_code == 404
    assert manager_client.post("/settings/no_such_key/clear/").status_code == 404


def test_edit_choice_setting_from_screen(manager_client):
    response = manager_client.get(edit_url("over_shipment"))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'name="over_shipment"' in body and "止める" in body
    assert f'href="{reverse("core:settings")}"' in body and "キャンセル" in body
    assert body.count("btn-primary") == 1
    assert "答えの値に戻す" not in body

    response = manager_client.post(edit_url("over_shipment"), {"over_shipment": "stop"})
    assert response.status_code == 302 and response["Location"] == reverse("core:settings")
    assert conf.setting("over_shipment") == "stop"

    body = manager_client.get(response["Location"]).content.decode()
    assert "止める" in body and conf.SOURCE_SCREEN in body
    assert "保存しました。" in body


def test_edit_bool_text_and_access_control(manager_client):
    # 真偽は丸の選択で "false" を送ると偽で保存する
    response = manager_client.post(edit_url("partial_shipment"), {"partial_shipment": "false"})
    assert response.status_code == 302
    assert conf.setting("partial_shipment") is False
    assert "許さない" in manager_client.get(reverse("core:settings")).content.decode()

    body = manager_client.get(edit_url("partial_shipment")).content.decode()
    assert '<input type="radio" name="partial_shipment" value="true"' in body and "許さない" in body

    assert manager_client.post(edit_url("assistant_model"), {"assistant_model": " claude-test "}).status_code == 302
    assert conf.setting("assistant_model") == "claude-test"

    body = manager_client.get(edit_url("access_control")).content.decode()
    assert 'name="department"' in body and 'name="write"' in body and 'name="money"' in body
    response = manager_client.post(edit_url("access_control"), {"department": "on", "write": "off", "money": "log"})
    assert response.status_code == 302
    assert conf.setting("access_control") == {"department": "on", "write": "off", "money": "log"}
    assert conf.access_control("write") == "off"


def test_edit_hidden_fields_with_checkboxes(manager_client):
    body = manager_client.get(edit_url("hidden_fields")).content.decode()
    for name in ("customer_order_no", "due_date", "unit", "note"):
        assert f'value="{name}"' in body
    # 欄の名前は form.hidden_fields と重ならない名前 (重なると checkbox が 2 度出る)
    assert body.count('type="checkbox" name="names"') == 5
    assert 'name="hidden_fields"' not in body
    assert "checkbox checkbox-sm" in body

    response = manager_client.post(edit_url("hidden_fields"), {"names": ["note", "customer_order_no"]})
    assert response.status_code == 302
    assert conf.setting("hidden_fields") == ["note", "customer_order_no"]

    order_body = manager_client.get(reverse("orders:order_create")).content.decode()
    assert 'name="customer_order_no"' not in order_body


def test_hidden_field_candidates_match_orders_forms():
    # 候補は orders の側 (forms.hideable_fields) が持ち、core の設定の画面は importlib で同じ並びを引く
    from seizou.core import settings_screen
    from seizou.orders.forms import KEPT_FIELDS, OrderForm, OrderLineForm, hideable_fields

    fields = {**OrderForm.base_fields, **OrderLineForm.base_fields}
    names = [name for name, _label in hideable_fields()]
    assert set(names) == set(fields) - KEPT_FIELDS - {"order_line"}
    assert len(names) == len(set(names))
    assert all(label == str(fields[name].label) for name, label in hideable_fields())
    assert settings_screen.hideable_fields() == tuple(hideable_fields())


def test_hideable_fields_are_empty_without_orders(monkeypatch):
    # db の fixture を付けない。apps.is_installed を差し替えた context の中では hideable_fields() だけを呼ぶ
    from seizou.core import settings_screen
    from seizou.core.forms import SettingForm

    def no_orders_forms(name):
        raise ImportError(name)

    with monkeypatch.context() as patch:
        patch.setattr(settings_screen, "import_module", no_orders_forms)
        assert settings_screen.hideable_fields() == ()
    with monkeypatch.context() as patch:
        patch.setattr(settings_screen.apps, "is_installed", lambda label: False)
        assert settings_screen.hideable_fields() == ()
    with monkeypatch.context() as patch:
        patch.setattr(settings_screen, "import_module", no_orders_forms)
        form = SettingForm(key="hidden_fields")
    assert list(form.fields[SettingForm.HIDDEN_FIELDS_FIELD].choices) == []


def test_invalid_input_is_not_saved(manager_client):
    response = manager_client.post(edit_url("assistant_model"), {"assistant_model": ""})
    assert response.status_code == 200
    assert "text-error" in response.content.decode()
    assert not SettingOverride.objects.exists()

    response = manager_client.post(edit_url("over_shipment"), {"over_shipment": "bogus"})
    assert response.status_code == 200
    assert "text-error" in response.content.decode()
    assert not SettingOverride.objects.exists()


def test_clear_restores_answer_value(manager_client, settings):
    settings.SEIZOU = {"over_shipment": "stop"}
    manager_client.post(edit_url("over_shipment"), {"over_shipment": "off"})
    assert conf.setting("over_shipment") == "off"

    body = manager_client.get(edit_url("over_shipment")).content.decode()
    assert f'action="{clear_url("over_shipment")}"' in body and "答えの値に戻す" in body

    response = manager_client.post(clear_url("over_shipment"))
    assert response.status_code == 302 and response["Location"] == reverse("core:settings")
    assert not SettingOverride.objects.filter(key="over_shipment").exists()
    assert conf.setting("over_shipment") == "stop"
    assert conf.setting_source("over_shipment") == conf.SOURCE_ANSWER

    body = manager_client.get(response["Location"]).content.decode()
    assert "答えの値に戻しました。" in body

    assert "答えの値に戻す" not in manager_client.get(edit_url("over_shipment")).content.decode()
