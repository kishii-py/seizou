"""設定の画面 (seizou.core.settings_screen。/settings/)。

見る所: ログインの確かめ、会社の選べる設定の今の値、普段は触らない物の入口 (つながり・アカウント管理)、システムの状態、
右上の利用者のメニューの「設定」、左のメニューから「つながり」が消えた事。鍵の値を出さない事。
"""

from __future__ import annotations

import re

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.urls import reverse

from seizou.core import settings_screen
from seizou.core.doctor import Row


@pytest.fixture
def logged_in(client, db):
    client.force_login(get_user_model().objects.create_user(username="tester", password="tests-only"))
    return client


@pytest.fixture
def manager_client(client, db):
    """アカウント管理の権限がある人 (システム管理者)。"""
    client.force_login(get_user_model().objects.create_superuser("boss", password="tests-only"))
    return client


def _doctor_part(body):
    """「システムの状態」の「点検」の行の中身 (id="doctor-result" の dd)。"""
    start = body.index('id="doctor-result"')
    return body[start : body.index("</dd>", start)]


def _system_part(body):
    """「システムの状態」の節 (見出しのボタンを含む)。"""
    start = body.index('aria-labelledby="system-title"')
    return body[start : body.index("</section>", start)]


def test_anonymous_is_sent_to_login(client, db):
    response = client.get(reverse("core:settings"))
    assert response.status_code == 302 and "/login/" in response["Location"]


def test_settings_screen_shows_company_settings_and_links(logged_in, settings, monkeypatch):
    settings.SEIZOU = {"id": "sample_co", "port": 8010, "over_shipment": "stop", "hidden_fields": ["note"]}
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SEIZOU_MCP_USER", raising=False)

    body = logged_in.get(reverse("core:settings")).content.decode()

    assert "設定" in body and "会社の選べる設定" in body
    assert "止める" in body and "知らせる" in body
    assert "note" in body
    assert "sample_co / 8010" in body
    assert reverse("connect:connection_list") in body and "取引先との接続" in body
    assert reverse("attachments:attachment_list") in body and "添付ファイルの一覧" in body
    # アカウント管理の入口: 部品 accounts (N36) が入っていれば一覧への link、無ければ「まだ入っていない」の案内。
    # 題の下の説明の 1 文は出さない (決め 11-03 14:1x) ので、字「アカウント管理」は入口の出る人の画面で見る
    if apps.is_installed("seizou.accounts"):
        # 普通の利用者には入口を出さない (アカウント管理の権限がある人と管理者だけ)
        assert reverse("accounts:access_list") not in body
        logged_in.force_login(get_user_model().objects.create_superuser(username="boss", password="tests-only"))
        boss_body = logged_in.get(reverse("core:settings")).content.decode()
        assert reverse("accounts:access_list") in boss_body and "アカウント管理" in boss_body
    else:
        assert "アカウント管理" in body and "まだ入っていない" in body
    assert "ない (読み取りとチャットは使えない)" in body
    assert "決めていない (書く道具は断る)" in body
    # データベースの名前は CI の postgres の組でも通るよう、つないでいる物から決める (sqlite と postgresql の 2 つ)
    assert {"sqlite": "SQLite", "postgresql": "PostgreSQL"}[connection.vendor] in body
    # フィードバックの一覧の入口: 部品 feedback (N59) が入っている会社だけ。一覧は自分の分を全員が見られるので全員に出す
    if apps.is_installed("seizou.feedback"):
        assert reverse("feedback:feedback_list") in body and "フィードバックの一覧" in body
    else:
        assert "フィードバックの一覧" not in body


def test_links_section_comes_before_company_settings(logged_in):
    """「普段は触らない物」の節 (アカウント管理の入口) を「会社の選べる設定」の表より前に置く (初見テスト R5 の V22。C14-1 = a)。

    入口がページの下の端にあり、開いた直後の窓に入っていなかった。アカウント管理の権限がある人と無い人の両方で、DOM の順を見る。
    """
    plain_body = logged_in.get(reverse("core:settings")).content.decode()
    logged_in.force_login(get_user_model().objects.create_superuser(username="boss", password="tests-only"))
    boss_body = logged_in.get(reverse("core:settings")).content.decode()

    for body in (plain_body, boss_body):
        links = body.index('aria-labelledby="links-title"')
        assert links < body.index('aria-labelledby="company-settings-title"')
        assert links < body.index('aria-labelledby="company-info-title"') < body.index('aria-labelledby="system-title"')
    if apps.is_installed("seizou.accounts"):
        links = boss_body.index('aria-labelledby="links-title"')
        assert links < boss_body.index(reverse("accounts:access_list")) < boss_body.index('aria-labelledby="company-settings-title"')


def _settings_part(body):
    """「会社の選べる設定」の節 (表の上の説明と表)。"""
    start = body.index('aria-labelledby="company-settings-title"')
    return body[start : body.index("</section>", start)]


def test_settings_table_column_widths(manager_client, settings):
    """表の列の幅 (初見テスト R5 の a-flagged-26・a-blind-08・a-blind-09。C14-2 = a)。

    「設定」の列は折り返す (tablet・1024・1280 で「編集」が箱の外に出ないように)。「今の値」は 640px 以上だけ最小の幅を見出しに付け、
    権限の判定は塊ごとに出して 640px 以上では塊の途中で折らない。「鍵」の列はスマホの幅でも隠さない。
    """
    settings.SEIZOU = {"access_control": {"department": "on", "write": "on", "money": "on"}}
    part = _settings_part(manager_client.get(reverse("core:settings")).content.decode())

    assert '<th class="font-medium">設定</th><th class="font-medium sm:min-w-36">今の値</th>' in part
    for row in settings_screen.company_settings():
        assert f"<tr><td>{row['label']}" in part, row["key"]
    assert (
        '<td><span class="sm:whitespace-nowrap">部署: 有効</span>・<span class="sm:whitespace-nowrap">編集の権限: 有効</span>'
        '・<span class="sm:whitespace-nowrap">金額の閲覧: 有効</span></td>'
    ) in part
    # 鍵の列は全部の幅で出す (幅で隠す class を足さない)
    assert '<th class="font-medium">鍵</th>' in part
    assert part.count('<td class="whitespace-nowrap font-mono text-xs text-base-content/70">') == len(settings_screen.company_settings())
    classes = " ".join(re.findall(r'class="([^"]*)"', part)).split()
    assert [name for name in classes if name.endswith("hidden")] == []


def test_settings_table_value_parts_only_for_dict_values(settings):
    settings.SEIZOU = {"access_control": {"money": "off", "department": "log"}}
    rows = {row["key"]: row for row in settings_screen.company_settings()}
    # 並びは SUB_LABELS の順 (保存した並びに頼らない)。字は _display と同じ
    assert rows["access_control"]["value_parts"] == ["部署: 記録だけ", "金額の閲覧: 使わない"]
    assert rows["access_control"]["value"] == "部署: 記録だけ・金額の閲覧: 使わない"
    assert all(row["value_parts"] == [] for key, row in rows.items() if key != "access_control")
    settings.SEIZOU = {"access_control": {}}
    rows = {row["key"]: row for row in settings_screen.company_settings()}
    assert rows["access_control"]["value_parts"] == [] and rows["access_control"]["value"] == "なし"


#: 作る人向けの字。設定の表の節と会社の情報の説明に出さない (大文字と小文字を分けて数える。業種の 2 行目の「空なら seizou」は残す)
INTERNAL_WORDS = ("settings.py", "SEIZOU", "YAML")

#: 2 社の答えの YAML と同じく、選べる設定の多くを答えに書いた形 (出所の字が 3 つとも出るように、画面の上書きも 1 つ作る)
ANSWER_SEIZOU = {
    "id": "sample_co",
    "port": 8010,
    "order_form": "multi",
    "partial_shipment": True,
    "over_shipment": "warn",
    "over_workorder": "warn",
    "receiving_required": "stop",
    "order_number": "seq",
    "hidden_fields": [],
    "production_plan": True,
    "access_control": {"department": "on", "write": "on", "money": "on"},
    "brand_color": "#0369a1",
    "industry": "表面処理",
}


def _text(html):
    """HTML の tag を除いた字 (属性の中の URL などを数えない)。"""
    return re.sub(r"<[^>]+>", "", html)


def test_settings_section_has_no_internal_words(logged_in, settings):
    """表と説明の字を画面の言葉にする (初見テスト R5 の V99・U101。候補 C14)。

    「会社の選べる設定」の節と「会社の情報」の説明に settings.py・SEIZOU・YAML を出さない。「model」は鍵の列の 3 つだけ。
    アカウント管理の権限がある人 (「編集」の列あり) と無い人の両方で見る。
    """
    from seizou.core import conf, services

    settings.SEIZOU = ANSWER_SEIZOU
    services.set_setting("over_shipment", "stop", None)
    plain_body = logged_in.get(reverse("core:settings")).content.decode()
    logged_in.force_login(get_user_model().objects.create_superuser(username="boss", password="tests-only"))
    boss_body = logged_in.get(reverse("core:settings")).content.decode()

    for body in (plain_body, boss_body):
        part = _settings_part(body)
        start = body.index('aria-labelledby="company-info-title"')
        info = body[start : body.index("</dl>", start)]
        for word in INTERNAL_WORDS:
            assert word not in part and word not in info, word
        assert _text(part).count("model") == 3
        assert "空なら seizou" in part
        # 出所の 3 つの字が表に出る (この画面で変えた・導入の時に決めた・初期値)
        assert "<th class=\"font-medium\">どこで決めたか</th>" in part
        for source in (conf.SOURCE_SCREEN, conf.SOURCE_ANSWER, conf.SOURCE_DEFAULT):
            assert f">{source}</td>" in part, source
    for label, _words in settings_screen.SETTING_LABELS.values():
        assert "model" not in label.lower(), label


def test_settings_screen_does_not_call_accounts_a_part(manager_client):
    """アカウント管理のカードと表の上の説明で、権限を「部品」と言わない (初見テスト R5 の V100。候補 C47 の 1 の (c)・(d))。

    カードは accounts が入っていてもいなくてもシステム管理者に出る (入っていなければ灰色のカード) ので、どちらの会社でも同じ字で見る。
    システムの状態の見出し「入っている部品」は C47-2 = a で残るので、body の「部品」の有無は見ない。
    """
    body = manager_client.get(reverse("core:settings")).content.decode()

    for word in ("権限管理の部品", "権限の部品"):
        assert word not in body, word
    assert "アカウントと部署と権限。" in body
    assert "アカウント管理が入っていない会社ではシステム管理者" in _settings_part(body)


def test_settings_source_column_follows_screen_edit_and_clear(manager_client, settings):
    """画面で変えた後と「答えの値に戻す」の後に、「どこで決めたか」の列の字が変わる。"""
    from seizou.core import conf

    settings.SEIZOU = {"over_shipment": "stop"}

    def source_of(key):
        part = _settings_part(manager_client.get(reverse("core:settings")).content.decode())
        row = next(tr for tr in part.split("<tr>") if f">{key}</td>" in tr)
        return re.search(r'<td class="whitespace-nowrap text-xs text-base-content/70">([^<]*)</td>', row).group(1)

    assert source_of("over_shipment") == conf.SOURCE_ANSWER == "導入の時に決めた"
    assert source_of("order_form") == conf.SOURCE_DEFAULT == "初期値"
    manager_client.post(reverse("core:setting_update", args=["over_shipment"]), {"over_shipment": "off"})
    assert source_of("over_shipment") == conf.SOURCE_SCREEN == "この画面で変えた"
    manager_client.post(reverse("core:setting_clear", args=["over_shipment"]))
    assert source_of("over_shipment") == conf.SOURCE_ANSWER


def test_locked_note_only_on_locked_rows(manager_client):
    """「画面からは変えない」の字は、画面から変えない 3 つ (conf.LOCKED_KEYS) の行だけに出る。「編集」の列でなく名前の下の 2 行目に出す。"""
    from seizou.core import conf

    part = _settings_part(manager_client.get(reverse("core:settings")).content.decode())
    rows = {key: tr for tr in part.split("<tr>") for key in conf.DEFAULTS if f">{key}</td>" in tr}
    assert set(rows) == set(conf.DEFAULTS)
    for key, row in rows.items():
        assert ("画面からは変えない" in row) == (key in conf.LOCKED_KEYS), key
    for key in conf.LOCKED_KEYS:
        note = settings_screen.setting_note(key)
        assert note.endswith(settings_screen.LOCKED_NOTE)
        assert f'<span class="block text-xs text-base-content/70">{note}</span></td>' in rows[key]
        # 「編集」の列は空のまま (折れない列に字を足さない)
        assert '<td class="whitespace-nowrap"></td></tr>' in rows[key]
    assert part.count(settings_screen.LOCKED_NOTE) == len(conf.LOCKED_KEYS)


def test_brand_color_and_industry_edit_pages_show_the_same_note(manager_client):
    """主色と業種の変更の画面に、設定の画面の表の 2 行目と同じ字の注記を出す (「# と 16 進の 6 桁」は値を打つ所で要るため)。"""
    for key in ("brand_color", "industry"):
        page = manager_client.get(reverse("core:setting_update", args=[key])).content.decode()
        note = settings_screen.SETTING_NOTES[key]
        assert f'<p id="id_{key}_helptext" class="mt-2 text-xs text-base-content/50">{note}</p>' in page, key
        assert note == settings_screen.setting_note(key)


def test_settings_screen_hides_key_values(logged_in, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-value-that-must-not-appear")
    monkeypatch.setenv("SEIZOU_MCP_USER", "admin")

    body = logged_in.get(reverse("core:settings")).content.decode()

    assert "fake-key-value-that-must-not-appear" not in body
    assert "AI の鍵" in body and "ある" in body
    assert "決めてある" in body


def test_display_words():
    assert settings_screen._display("order_form", "single") == "1 件 1 品"
    assert settings_screen._display("partial_shipment", False) == "許さない"
    assert settings_screen._display("hidden_fields", []) == "なし"
    assert settings_screen._display("hidden_fields", ["a", "b"]) == "a・b"
    # 辞書の設定 (権限の判定。N36 が conf.DEFAULTS に足す) は「中の鍵: 値」を「・」で。知らない鍵はそのまま
    assert (
        settings_screen._display("access_control", {"department": "log", "write": "on", "money": "off"})
        == "部署: 記録だけ・編集の権限: 有効・金額の閲覧: 使わない"
    )
    assert settings_screen._display("access_control", {}) == "なし"
    assert settings_screen._display("unknown_dict", {"a": 1}) == "a: 1"


def test_user_menu_has_settings_and_sidebar_has_no_connections(logged_in):
    body = logged_in.get(reverse("core:home")).content.decode()

    assert reverse("core:settings") in body and ">設定<" in body.replace("\n", "")
    # つながりは左のメニューから設定の画面へ移した (普段は触らない物)
    assert reverse("connect:connection_list") not in body
    assert reverse("connect:inbox_list") in body


def test_settings_screen_counts_screen_overrides(logged_in):
    from seizou.core import services

    body = logged_in.get(reverse("core:settings")).content.decode()
    assert "画面で上書きした設定" in body and "0 件" in body

    services.set_setting("over_shipment", "stop", None)
    services.set_setting("partial_shipment", False, None)
    body = logged_in.get(reverse("core:settings")).content.decode()
    assert "2 件" in body
    rows = settings_screen.system_rows()
    assert [row["label"] for row in rows].count("画面で上書きした設定") == 1
    assert {row["label"]: row["value"] for row in rows}["画面で上書きした設定"] == "2 件"


def test_system_rows_show_dash_without_db():
    # db の fixture を付けない = pytest-django が DB を RuntimeError で止める道
    rows = {row["label"]: row["value"] for row in settings_screen.system_rows()}
    assert rows["画面で上書きした設定"] == "—"


# ---------------------------------------------------------------- 点検 (core/doctor.run をボタンの POST で走らせる)


def test_doctor_button_only_for_manager(logged_in):
    form = f'<form method="post" action="{reverse("core:settings")}">'

    body = logged_in.get(reverse("core:settings")).content.decode()
    assert form not in body and "点検を走らせる" not in body
    assert "まだ走らせていない" in _doctor_part(body)

    logged_in.force_login(get_user_model().objects.create_superuser("boss", password="tests-only"))
    boss_body = logged_in.get(reverse("core:settings")).content.decode()
    system = _system_part(boss_body)
    assert form in system and "点検を走らせる" in system and "btn-outline" in system
    assert boss_body.count("btn-primary") == 0
    assert "まだ走らせていない" in _doctor_part(boss_body)


def test_doctor_post_is_refused_for_plain_and_anonymous(logged_in, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("変えられない人の POST で点検を走らせた")

    monkeypatch.setattr(settings_screen.doctor, "run", fail)

    response = logged_in.post(reverse("core:settings"))
    assert response.status_code == 403
    body = response.content.decode()
    assert "この画面は開けません" in body and "アカウント管理の権限" in body
    assert "no-store" in response["Cache-Control"]

    anonymous = Client().post(reverse("core:settings"))
    assert anonymous.status_code == 302 and "/login/" in anonymous["Location"]


def test_doctor_post_lists_first_three_fixes(manager_client, monkeypatch):
    rows = [Row(True, "良い 1", "g1")] + [Row(False, f"直す {n}", f"d{n}", f"f{n}") for n in range(1, 6)]
    monkeypatch.setattr(settings_screen.doctor, "run", lambda production=None: rows)

    response = manager_client.post(reverse("core:settings"))
    assert response.status_code == 200
    part = _doctor_part(response.content.decode())
    assert "直す物 5 件" in part and "6 件を見た" in part
    for n in (1, 2, 3):
        assert f"直す {n}" in part and f"d{n}" in part and f"→ 直し方: f{n}" in part
    assert "直す 4" not in part and "直す 5" not in part
    assert "残りは" in part and "seizou_doctor" in part
    assert "text-error" in part

    # 結果は保存しない (表にも session にも書かない) ので、次の GET は「まだ走らせていない」
    again = _doctor_part(manager_client.get(reverse("core:settings")).content.decode())
    assert "まだ走らせていない" in again and "直す 1" not in again


def test_doctor_post_says_good_when_nothing_to_fix(manager_client, monkeypatch, settings):
    rows = [Row(True, "良い 1", "g1"), Row(True, "良い 2", "g2")]
    monkeypatch.setattr(settings_screen.doctor, "run", lambda production=None: rows)

    # tests.settings の既定 (DEBUG が偽 = 本番の条件)。先に 1 回送って、試験の client の middleware を DEBUG が偽のときの形で組ませる
    settings.DEBUG = False
    part = _doctor_part(manager_client.post(reverse("core:settings")).content.decode())
    assert "良い (2 件を見た)" in part
    assert "直す物" not in part and "残りは" not in part
    assert "本番の条件の行" not in part

    settings.DEBUG = True
    part = _doctor_part(manager_client.post(reverse("core:settings")).content.decode())
    assert "良い (2 件を見た)" in part
    assert "本番の条件の行" in part and "--production" in part


def test_doctor_post_runs_real_doctor_without_secret_values(client, db, monkeypatch, settings):
    monkeypatch.setenv("SECRET_KEY", "env-secret-key-must-not-appear")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ai-key-must-not-appear")
    monkeypatch.setenv("SEIZOU_PARTNER_TESTCO_SECRET", "partner-secret-must-not-appear")
    monkeypatch.setenv("PGPASSWORD", "db-password-must-not-appear")
    settings.SECRET_KEY = "fake-settings-secret-must-not-appear"
    # SECRET_KEY を替えた後にログインする (session の確かめの字が SECRET_KEY から作られるため)
    client.force_login(get_user_model().objects.create_superuser("boss", password="tests-only"))

    response = client.post(reverse("core:settings"))
    assert response.status_code == 200
    body = response.content.decode()
    part = _doctor_part(body)
    assert "件を見た" in part
    # tests.settings は DEBUG が偽 (本番の条件) で STATIC_ROOT が無いので、直す物が 1 件以上ある (数は決めない)
    assert "直す物" in part
    for value in (
        "env-secret-key-must-not-appear",
        "ai-key-must-not-appear",
        "partner-secret-must-not-appear",
        "db-password-must-not-appear",
        "fake-settings-secret-must-not-appear",
    ):
        leaked = value in body
        assert not leaked, "画面に鍵の値が出た"
