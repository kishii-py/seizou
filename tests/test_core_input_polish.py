"""入力の磨き (決め 11-04 22:0x): 書きかけの離脱の確認・Enter で次の欄・日付の曜日。

JS は動かさず、字と読み込みの並びと node --check を見る (ブラウザの確かめは本体の手)。
曜日は filter weekday_ja と、一覧の日付の枡・詳細の日付の項目・CSV (曜日を足さない) を見る。
試験の画面は部品マスターの休日 (作り物の休日の名前)。
"""

import csv
import io
import re
import shutil
import subprocess
from datetime import date, datetime
from datetime import timezone as dt_timezone
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

import seizou.core
from seizou.core.templatetags.seizou_tags import weekday_ja
from seizou.core.views import BaseListView
from seizou.masters.models import Holiday

CORE = Path(seizou.core.__file__).resolve().parent
FORM_GUARD = CORE / "static" / "js" / "seizou-form-guard.js"
ENTER_GUARD = CORE / "templates" / "core" / "_enter_guard.html"
SCRIPT_SRC = re.compile(r"<script [^>]*src=\"([^\"]+)\"[^>]*>")
WEEKDAY = '<span class="text-xs text-base-content/50">(火)</span>'


class _HolidayByName(BaseListView):
    """日付が 2 列目の一覧 (1 列目の link でない日付の枡を見る)。"""

    model = Holiday
    screen_name = "作り物の休日の一覧"
    columns = ("name", "date")
    template_name = "core/list.html"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def holiday(db):
    # 2026-11-03 は火曜
    return Holiday.objects.create(date=date(2026, 11, 3), name="作り物の会社の休み", kind="company")


def text(response) -> str:
    return response.content.decode()


def test_base_loads_form_guard_after_modal(logged_in):
    body = text(logged_in.get(reverse("core:home")))
    scripts = SCRIPT_SRC.findall(body)
    guard = [i for i, src in enumerate(scripts) if src.endswith("js/seizou-form-guard.js")]
    modal = [i for i, src in enumerate(scripts) if src.endswith("js/seizou-modal.js")]
    assert len(guard) == 1 and len(modal) == 1
    assert guard[0] == modal[0] + 1
    assert "defer" in re.search(r"<script [^>]*js/seizou-form-guard\.js[^>]*>", body).group(0)


def test_form_guard_js_words():
    script = FORM_GUARD.read_text(encoding="utf-8")
    for word in (
        "form[data-enter-guard]",
        'addEventListener("input"',
        'addEventListener("change"',
        'addEventListener("submit"',
        '"beforeunload"',
        "event.preventDefault()",
        "returnValue",
        "window.confirm(MESSAGE)",
        "入力した内容が消えます。閉じますか？",
        '"cancel"',
        '"close"',
        '"dialog"',
        "seizou-modal",
        "dataset.seizouDirty",
    ):
        assert word in script, word
    for word in (".classList", ".className", ".innerHTML", "insertAdjacentHTML", "outerHTML"):
        assert word not in script, word


def test_enter_guard_moves_to_next_field_and_saves_with_ctrl_enter(logged_in):
    body = text(logged_in.get(reverse("masters:holiday_create")))
    assert "data-enter-guard" in body
    for word in (
        'document.addEventListener("keydown"',
        "Enter で保存まで進めない",
        "isComposing",
        "keyCode === 229",
        "TEXTAREA",
        "Ctrl+Enter",
        "event.ctrlKey || event.metaKey",
        'button[type="submit"]',
        ".focus()",
        ".click()",
        ".ts-wrapper",
        "window.seizouEnterGuard",
    ):
        assert word in body, word
    # 最初の form にだけ付ける形をやめた (小窓の form にも付く)
    assert 'document.querySelector("form[data-enter-guard]")' not in body
    template = ENTER_GUARD.read_text(encoding="utf-8")
    for word in (".classList", ".className", ".innerHTML"):
        assert word not in template, word


@pytest.mark.skipif(shutil.which("node") is None, reason="node が無い (JS の字の形は本体の門の node --check で見る)")
def test_js_passes_node_check(tmp_path):
    inline = re.search(r"<script>(.*?)</script>", ENTER_GUARD.read_text(encoding="utf-8"), re.S).group(1)
    enter_guard = tmp_path / "enter_guard.js"
    enter_guard.write_text(inline, encoding="utf-8")
    for path in (FORM_GUARD, enter_guard):
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_weekday_ja_filter():
    days = [weekday_ja(date(2026, 11, day)) for day in range(2, 9)]
    assert days == ["月", "火", "水", "木", "金", "土", "日"]
    assert weekday_ja(None) == ""
    assert weekday_ja("") == ""
    assert weekday_ja("2026-11-03") == ""
    assert weekday_ja(3) == ""
    assert weekday_ja(datetime(2026, 11, 3, 23, 30)) == "火"
    # 時刻つきは画面の時刻 (Asia/Tokyo) の日付で数える (UTC の 11-03 15:30 は東京の 11-04 0:30)
    assert weekday_ja(datetime(2026, 11, 3, 15, 30, tzinfo=dt_timezone.utc)) == "水"


def test_list_date_cells_have_weekday(logged_in, user, holiday):
    body = text(logged_in.get(reverse("masters:holiday_list")))
    assert "2026-11-03" + " " + WEEKDAY + "</a></td>" in body
    request = RequestFactory().get("/")
    request.user = user
    response = _HolidayByName.as_view()(request)
    response.render()
    assert '<td class="whitespace-nowrap font-mono tabular-nums">2026-11-03 ' + WEEKDAY + "</td>" in text(response)


def test_detail_date_has_weekday(logged_in, holiday):
    body = text(logged_in.get(reverse("masters:holiday_detail", args=[holiday.pk])))
    assert "2026年11月3日 " + WEEKDAY + "</dd>" in body
    # 時刻つきの項目 (登録した日時) にも添える (filter が datetime も受ける)。日付でない項目には添えない
    assert body.count('<span class="text-xs text-base-content/50">(') == 2
    assert "作り物の会社の休み</dd>" in body


def test_csv_has_no_weekday(logged_in, holiday):
    response = logged_in.get(reverse("masters:holiday_list"), {"format": "csv"})
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[1][0] == "2026-11-03"
    assert "(火)" not in response.content.decode("utf-8-sig")
