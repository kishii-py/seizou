"""入力の土台の小窓の形 (決め 11-04 17:5x〜18:0x。一覧の行の操作はページを移らず小窓で入力する)。

BaseFormView は、htmx の要求で見出し HX-Target が seizou-modal-box (base.html の小窓の箱) のとき、入力を小窓の中身
(core/_form_modal.html) で返す。誤りと赤・黄の知らせと「このまま保存」は小窓の中に描き直し、保存したら本文の無い 204 に
HX-Trigger の seizou:saved を付けて返す (messages に残さない)。一覧の form は seizou:refresh で行を取り直す。
試験の画面は部品マスターの休日の新規登録 (作り物の休日の名前)。
"""

import json
from datetime import date
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse

from seizou.core import checks
from seizou.masters.models import Holiday

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
ACTION = "masters.create_holiday"
JS = Path(__file__).resolve().parent.parent / "src" / "seizou" / "core" / "static" / "js" / "seizou-modal.js"


class _HolidayNote(checks.Check):
    """作り物の知らせる検査 (名前に「作り物」が入っていたら黄の知らせ)。"""

    name = "T91"
    action = ACTION
    mode = "warn"

    def run(self, data, user):
        return "作り物の知らせです。確かめてから保存してください。" if "作り物" in str(data.get("name", "")) else None


class _HolidayStop(checks.Check):
    """作り物の止める検査。番号は決まりの形 (R と 2 桁) にして、赤の知らせの「?」の出し分けを見る。"""

    name = "R13"
    action = ACTION
    mode = "stop"

    def run(self, data, user):
        return "作り物の止めです。" if "止め" in str(data.get("name", "")) else None


@pytest.fixture
def logged_in(client, db):
    client.force_login(get_user_model().objects.create_user("tester", password="pass-for-tests-only"))
    return client


@pytest.fixture
def holiday_checks():
    before = checks.registered(ACTION)
    yield
    checks.clear(ACTION)
    for check in before:
        checks.register(check)


def text(response) -> str:
    return response.content.decode()


def holiday_data(name: str) -> dict:
    return {"date": "2026-12-29", "name": name, "kind": "company"}


def test_plain_get_is_the_whole_page(logged_in):
    response = logged_in.get(reverse("masters:holiday_create"))
    body = text(response)
    assert response.status_code == 200
    assert "<html" in body
    assert "hx-post" not in body
    assert "キャンセル" in body
    assert "HX-Target" in response["Vary"]
    # htmx のほかの要求 (差し替える先が小窓の箱でない) も画面の全体のまま
    other = logged_in.get(reverse("masters:holiday_create"), HTTP_HX_REQUEST="true", HTTP_HX_TARGET="rows")
    assert "<html" in text(other)


def test_modal_get_is_the_fragment(logged_in):
    url = reverse("masters:holiday_create")
    response = logged_in.get(url + "?name=x", **MODAL)
    body = text(response)
    assert response.status_code == 200
    assert "<html" not in body
    assert "休日の新規登録" in body
    assert 'class="seizou-modal-wide"' in body
    assert '<form method="dialog">' in body
    assert f'hx-post="{url}?name=x"' in body
    assert 'hx-target="#seizou-modal-box"' in body
    assert 'hx-swap="innerHTML"' in body
    assert "data-enter-guard" in body
    assert "キャンセル" not in body
    assert "HX-Target" in response["Vary"]
    # ファイルの欄のある入力 (部品の新規登録の図面) は multipart で送る
    item = text(logged_in.get(reverse("masters:item_create"), **MODAL))
    assert 'hx-encoding="multipart/form-data"' in item
    assert 'enctype="multipart/form-data"' in item


def test_modal_post_with_errors_redraws_the_fragment(logged_in):
    response = logged_in.post(reverse("masters:holiday_create"), {}, **MODAL)
    body = text(response)
    assert response.status_code == 200
    assert "<html" not in body
    assert "休日の日付を入れてください。" in body
    assert "hx-post" in body
    assert Holiday.objects.count() == 0


def test_modal_warning_waits_for_confirmation(logged_in, holiday_checks):
    checks.register(_HolidayNote())
    url = reverse("masters:holiday_create")
    first = logged_in.post(url, holiday_data("作り物の会社の休み"), **MODAL)
    body = text(first)
    assert first.status_code == 200
    assert "<html" not in body
    assert "作り物の知らせです。" in body
    assert '<input type="hidden" name="confirmed" value="1">' in body
    assert "このまま保存" in body
    assert Holiday.objects.count() == 0
    second = logged_in.post(url, {**holiday_data("作り物の会社の休み"), "confirmed": "1"}, **MODAL)
    assert second.status_code == 204
    assert Holiday.objects.filter(name="作り物の会社の休み").count() == 1


def test_modal_stop_shows_red_notice_without_rule_help(logged_in, holiday_checks):
    checks.register(_HolidayStop())
    url = reverse("masters:holiday_create")
    modal = text(logged_in.post(url, holiday_data("作り物の止め"), **MODAL))
    assert "作り物の止めです。" in modal
    assert "R13" in modal
    # 小窓の中では「?」(決まりの説明を同じ小窓の箱に入れる) を出さない。画面の全体では出す
    help_url = reverse("core:rule_help", args=["R13"])
    assert help_url not in modal
    assert "R13 の決まりの説明" not in modal
    whole = text(logged_in.post(url, holiday_data("作り物の止め"), HTTP_HX_REQUEST="true"))
    assert help_url in whole
    assert "R13 の決まりの説明" in whole
    assert Holiday.objects.count() == 0


def test_modal_save_returns_204_with_trigger_and_no_messages(logged_in):
    response = logged_in.post(reverse("masters:holiday_create"), holiday_data("作り物の年末の休み"), **MODAL)
    assert response.status_code == 204
    assert response.content == b""
    assert "HX-Redirect" not in response
    header = response["HX-Trigger"]
    assert header.isascii() and not header.startswith("=?")
    assert json.loads(header) == {"seizou:saved": {"message": "保存しました。", "level": "success"}}
    assert Holiday.objects.get(name="作り物の年末の休み").date == date(2026, 12, 29)
    assert list(get_messages(response.wsgi_request)) == []
    # 次に開いた画面の全体にも古い知らせが出ない
    assert 'id="seizou-toasts"' not in text(logged_in.get(reverse("masters:holiday_list")))


def test_plain_htmx_save_still_redirects_with_message(logged_in):
    response = logged_in.post(reverse("masters:holiday_create"), holiday_data("作り物の夏の休み"), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert response["HX-Redirect"]
    assert "HX-Trigger" not in response
    assert [str(m) for m in get_messages(response.wsgi_request)] == ["保存しました。"]


def test_list_and_base_have_the_hooks(logged_in):
    body = text(logged_in.get(reverse("masters:holiday_list")))
    assert "change from:[data-list-filter], seizou:refresh from:body" in body
    assert "/static/js/seizou-modal.js" in body
    assert '<template id="seizou-toast-template">' in body
    assert body.count('data-toast-level="success"') == 1
    assert body.count('data-toast-level="warning"') == 1
    assert body.count("data-toast-text") == 2
    script = JS.read_text(encoding="utf-8")
    for word in ("seizou:saved", "seizou:refresh", "data-modal-form", "#seizou-modal-box", "showModal", "cloneNode", "textContent"):
        assert word in script
    for word in (".classList", ".className", ".innerHTML", "insertAdjacentHTML", "outerHTML"):
        assert word not in script


def test_modal_box_width_rules_are_in_the_css_source():
    """小窓の箱の幅の決まり (既定 48rem・入力 64rem・少ない中身 28rem・上下に 2rem の隙間) は scripts/css/input.css に 1 か所 (本人 11-04 22:2x)。"""
    from pathlib import Path

    css = Path(__file__).resolve().parents[1].joinpath("scripts", "css", "input.css").read_text(encoding="utf-8")
    assert "#seizou-modal-box {\n  max-width: min(48rem, calc(100vw - 2rem));\n  max-height: calc(100vh - 4rem);\n}" in css
    assert "#seizou-modal-box:has(> .seizou-modal-wide) {\n  max-width: min(64rem, calc(100vw - 2rem));\n}" in css
    assert "#seizou-modal-box:has(> .seizou-modal-narrow) {\n  max-width: min(28rem, calc(100vw - 2rem));\n}" in css
