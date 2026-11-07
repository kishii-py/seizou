"""決まりの番号の「?」(N93。N88 の追記 11 = 北極星の決め 11-04 06:3x): 赤の知らせの番号の横のボタンと、RULES.md の行を出す小窓。

見る所: 赤の知らせ (core/_notices.html) の番号は残り、番号が決まりの形 (R と 2 桁) のときだけ横に「?」のボタンが出る。
番号の無い赤の知らせ (「入力」) と黄の知らせには出ない。ボタンが開く口 (core:rule_help) は RULES.md の表のその番号の行の文と、
行の上の見出し (中心の決まりか同梱のポカよけか) を返す。行が無ければ 200 で無い旨の 1 文、番号の形が違えば 404。
文は作り物だけ。本物の API と HTTP は呼ばない。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.urls import reverse

from seizou.core import views
from seizou.core.exceptions import CheckViolation, RuleViolation
from seizou.core.views import Notice

ROOT_RULES_MD = Path(__file__).resolve().parent.parent / "RULES.md"
NOT_FOUND = "決まりの一覧 (RULES.md) に R99 の行が見つかりません。"
#: R01・R04 の「?」の説明に出る、この会社の設定の行の頭 (候補 C20 の 1)
SETTING_HEAD = "この会社の設定「受注より多い出荷 (R01・R04)」は"


def _rules_md_text(code: str) -> str:
    """RULES.md の表の、番号が code の行の 2 つ目の欄 (tests/test_r14_no_raw_sql.py の _rules_md_text と同じ読み方)。"""
    for line in ROOT_RULES_MD.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {code} |"):
            return line.split("|")[2].strip().replace("`", "")
    raise AssertionError(f"RULES.md に {code} の行が無い")


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


def test_red_rule_notice_has_help_button():
    for notice, code in ((RuleViolation("R05", "作り物の文"), "R05"), (CheckViolation("R01", "作り物の文"), "R01")):
        html = render_to_string("core/_notices.html", {"notice": notice, "warnings": []})
        assert f'<span class="font-mono text-xs text-base-content/50">{code}</span>' in html, code  # 番号は残す (決め。N156 から文の末尾)
        assert f'hx-get="/rules/{code}/"' in html, code
        assert f'aria-label="{code} の決まりの説明"' in html, code
        assert 'hx-target="#seizou-modal-box"' in html, code
        assert html.count("<button") == 1, code
        assert "作り物の文" in html, code


def test_notice_without_rule_number_has_no_button():
    html = render_to_string("core/_notices.html", {"notice": Notice("入力", "作り物の文"), "warnings": []})
    assert "入力" not in html  # 見出しの語は出さない (N156)
    assert "/rules/" not in html
    assert "<button" not in html

    # 黄の知らせ (ポカよけの「知らせる」) には出さない (決めの字は「決まりに止められた赤の知らせ」)
    html = render_to_string(
        "core/_notices.html", {"notice": None, "warnings": [SimpleNamespace(code="R01", message="作り物の文")]}
    )
    assert '<span class="font-mono text-xs text-base-content/50">R01</span>' in html
    assert "/rules/" not in html
    assert "<button" not in html


def test_rule_help_shows_rules_md_line(client, user):
    assert views.RULES_MD == ROOT_RULES_MD.resolve()
    assert views.RULES_MD.is_file()

    url = reverse("core:rule_help", args=["R01"])
    assert url == "/rules/R01/"
    response = client.get(url, HTTP_HX_REQUEST="true")
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("core:login"))

    client.force_login(user)
    response = client.get(url, HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    body = response.content.decode()
    assert "R01 の決まり" in body
    assert _rules_md_text("R01") in body
    assert "同梱のポカよけ" in body

    body = client.get("/rules/R02/", HTTP_HX_REQUEST="true").content.decode()
    assert _rules_md_text("R02") in body
    assert "中心の決まり" in body

    response = client.get("/rules/R99/", HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert NOT_FOUND in response.content.decode()

    assert client.get("/rules/X1/", HTTP_HX_REQUEST="true").status_code == 404
    # htmx でない要求にも同じ中身を返す (移す先の頁が無い)
    assert _rules_md_text("R05") in client.get("/rules/R05/").content.decode()


def test_rule_help_without_rules_md(client, user, monkeypatch, tmp_path):
    # RULES.md の無い置き場 (配布の wheel だけで動かす形): 行は None で、小窓は 200 の無い旨の 1 文。
    monkeypatch.setattr(views, "RULES_MD", tmp_path / "RULES.md")
    assert views.rule_line("R01") is None
    assert views.rule_line("X1") is None
    client.force_login(user)
    response = client.get("/rules/R99/", HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert NOT_FOUND in response.content.decode()
    # RULES.md が無くても、設定の鍵のある番号は会社の設定の行を出す
    body = client.get("/rules/R01/", HTTP_HX_REQUEST="true").content.decode()
    assert "決まりの一覧 (RULES.md) に R01 の行が見つかりません。" in body
    assert SETTING_HEAD in body


@pytest.mark.parametrize(
    ("value", "line"),
    (
        ("stop", "この会社の設定「受注より多い出荷 (R01・R04)」は「止める」です。この決まりに当たると保存しません。"),
        ("warn", "この会社の設定「受注より多い出荷 (R01・R04)」は「知らせる」です。黄の知らせと「このまま保存」のボタンが出ます。"),
        ("off", "この会社の設定「受注より多い出荷 (R01・R04)」は「通す」です。知らせずに保存します。"),
    ),
)
def test_rule_help_shows_company_setting(client, user, settings, value, line):
    # 初見テスト R5 の V31 (候補 C20 の 1): 「?」の説明に、この会社の今の設定の行を出す。並びは行の文 → 設定の行 → 節の見出し。
    settings.SEIZOU = {"over_shipment": value}
    client.force_login(user)
    body = client.get("/rules/R01/", HTTP_HX_REQUEST="true").content.decode()
    assert line in body
    section = views.rule_line("R01")["section"]
    assert body.index(_rules_md_text("R01")) < body.index(line) < body.index(section)
    # R04 も同じ鍵 (over_shipment) なので同じ行が出る
    assert line in client.get("/rules/R04/", HTTP_HX_REQUEST="true").content.decode()


def test_rule_help_setting_line_only_for_rules_with_setting(client, user, settings):
    client.force_login(user)
    # 設定の鍵の無い番号 (中心の決まり) には出さない
    body = client.get("/rules/R02/", HTTP_HX_REQUEST="true").content.decode()
    assert _rules_md_text("R02") in body
    assert "この会社の設定" not in body
    # 設定の値が表に無い字のときも 200 で、行を出さない (Check.current_mode の ValueError を出さない)
    settings.SEIZOU = {"over_shipment": "maybe"}
    response = client.get("/rules/R01/", HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert "この会社の設定" not in response.content.decode()
    # R06・R07 は over_workorder の見出しで出す
    settings.SEIZOU = {"over_workorder": "warn"}
    body = client.get("/rules/R06/", HTTP_HX_REQUEST="true").content.decode()
    assert "この会社の設定「受注より多い指示書の数量と予定より多い実績 (R06・R07)」は「知らせる」です。" in body


def test_rule_setting_line_skips_unknown_keys(monkeypatch):
    # 鍵が設定の画面の表に無ければ値を読まない (conf.setting は知らない鍵で KeyError)
    monkeypatch.setattr(views.checks, "setting_key_for", lambda code: "no_such_key")
    assert views.rule_setting_line("R01") == ""
    monkeypatch.setattr(views.checks, "setting_key_for", lambda code: None)
    assert views.rule_setting_line("R01") == ""
