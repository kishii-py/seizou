"""設定の画面の値の字は日本語だけ (N109。精査 A3 の「仕様どおりだが想定外」の 6。本人の決め 11-04 14:4x の (10))。

一覧の「今の値」の列と、設定の変更の画面の選択肢に、「止める (stop)」の形の英字の括弧を出さない。
答えの YAML と見比べる人のために、表の「鍵」の列 (英字の鍵) とシステムの状態 (会社の id とポート) は残す。
送る値 (option の value) と保存する値は、今までどおり英字の値。名前は作り物だけ。
"""

from __future__ import annotations

import re

from django.urls import reverse

from seizou.core import conf, settings_screen
from seizou.core.models import SettingOverride
from tests import test_core_settings_edit
from tests.test_core_settings_edit import edit_url

# 設定の変更の試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
# manager = システム管理者 (権限の判定 access_control も変えられる)。manager_client = その人でログインした client。
manager = test_core_settings_edit.manager
manager_client = test_core_settings_edit.manager_client

#: 選べる値 (conf.CHOICES・conf.ACCESS_MODES) と真偽の英字を括弧で添えた字。「(settings.py)」「(DEBUG)」のような説明の括弧は数えない
ENGLISH_VALUE = re.compile(r"\((True|False|warn|stop|off|log|on|multi|single|seq|ym_seq)\)")

#: 日本語の値の字を出す画面の鍵 (選べる値・権限の判定・真偽)
VALUE_KEYS = (*conf.CHOICES, "access_control", "partial_shipment", "production_plan")


def access_parts(*parts: str) -> str:
    """設定の画面の表の「今の値」の、権限の判定の塊の HTML (塊ごとの span を「・」でつなぐ)。"""
    return "・".join(f'<span class="sm:whitespace-nowrap">{part}</span>' for part in parts)


def test_settings_list_and_edit_pages_show_japanese_values_only(manager_client, settings):
    settings.SEIZOU = {
        "id": "sample_co",
        "port": 8010,
        "order_form": "single",
        "over_shipment": "stop",
        "order_number": "ym_seq",
        "partial_shipment": False,
        "access_control": {"department": "log", "write": "on", "money": "off"},
    }

    body = manager_client.get(reverse("core:settings")).content.decode()
    assert ENGLISH_VALUE.findall(body) == []
    for value in ("1 件 1 品", "止める", "知らせる", "年月 + 連番", "許さない", "使う"):
        assert f"<td>{value}</td>" in body
    # 権限の判定は「中の鍵: 値」の塊ごとに出す (640px 以上の幅では塊の途中で折らない。初見テスト R5 の a-blind-08。候補 C14)
    assert f"<td>{access_parts('部署: 記録だけ', '編集の権限: 有効', '金額の閲覧: 使わない')}</td>" in body
    # 表の「鍵」の列 (英字の鍵) とシステムの状態の会社の id とポートは残す (答えの YAML と見比べるため)
    for key in conf.DEFAULTS:
        assert f">{key}</td>" in body
    assert "sample_co / 8010" in body

    for key in VALUE_KEYS:
        response = manager_client.get(edit_url(key))
        assert response.status_code == 200
        page = response.content.decode()
        assert ENGLISH_VALUE.findall(page) == [], key
        # 鍵は設定の画面の表の「鍵」の列と URL に残す。変更の画面の頭の行には出さない (C76)
        assert f"<code class=\"font-mono\">{key}</code>" not in page
    # 選べる値はどれも日本語の字を持ち、丸の value は英字の値・名前は日本語だけ (字の無い値を足すと、ここで落ちる)。
    # input の後には改行が入る (Django の input.html の終わりの改行) ので \s* で受ける
    for key, values in conf.CHOICES.items():
        words = settings_screen.SETTING_LABELS[key][1]
        page = manager_client.get(edit_url(key)).content.decode()
        for value in values:
            assert re.search(rf'<input type="radio" name="{key}" value="{value}"[^>]*>\s*<span>{re.escape(words[value])}', page), (key, value)
    # 権限の判定は今の選ぶ欄のまま (option の value は英字の値・字は日本語だけ)
    words = settings_screen.SETTING_LABELS["access_control"][1]
    page = manager_client.get(edit_url("access_control")).content.decode()
    for value in conf.ACCESS_MODES:
        assert re.search(rf'<option value="{value}"( selected)?>{re.escape(words[value])}</option>', page), ("access_control", value)
    page = manager_client.get(edit_url("partial_shipment")).content.decode()
    assert re.search(r'<input type="radio" name="partial_shipment" value="true"[^>]*>\s*<span>許す</span>', page)
    assert re.search(r'<input type="radio" name="partial_shipment" value="false"[^>]*>\s*<span>許さない</span>', page)


def test_posted_values_stay_english_and_are_saved(manager_client):
    response = manager_client.post(edit_url("over_shipment"), {"over_shipment": "stop"})
    assert response.status_code == 302 and response["Location"] == reverse("core:settings")
    assert conf.setting("over_shipment") == "stop"
    assert SettingOverride.objects.get(key="over_shipment").value == "stop"

    response = manager_client.post(edit_url("access_control"), {"department": "on", "write": "off", "money": "log"})
    assert response.status_code == 302
    assert conf.setting("access_control") == {"department": "on", "write": "off", "money": "log"}

    response = manager_client.post(edit_url("order_number"), {"order_number": "ym_seq"})
    assert response.status_code == 302
    assert conf.setting("order_number") == "ym_seq"

    # 日本語の字は送る値ではない (選べない値の誤りで、保存しない)
    response = manager_client.post(edit_url("order_form"), {"order_form": "1 件 1 品"})
    assert response.status_code == 200
    assert conf.setting("order_form") == "multi"
    assert not SettingOverride.objects.filter(key="order_form").exists()

    body = manager_client.get(reverse("core:settings")).content.decode()
    assert ENGLISH_VALUE.findall(body) == []
    assert "<td>止める</td>" in body and "<td>年月 + 連番</td>" in body
    assert f"<td>{access_parts('部署: 有効', '編集の権限: 使わない', '金額の閲覧: 記録だけ')}</td>" in body
    assert body.count(f">{conf.SOURCE_SCREEN}</td>") == 3


def test_dict_values_render_in_fixed_order_regardless_of_stored_key_order():
    """辞書の設定の字は、保存した鍵の並びに頼らず SUB_LABELS の順で出す (PostgreSQL の jsonb は鍵を並べ替えて返す。CI の postgres で 132 回目から落ちていた)。"""
    stored = {"money": "log", "write": "off", "department": "on"}
    assert settings_screen._display("access_control", stored) == "部署: 有効・編集の権限: 使わない・金額の閲覧: 記録だけ"
    assert settings_screen._display("access_control", {"extra": "on", "department": "log"}) == "部署: 記録だけ・extra: 有効"
