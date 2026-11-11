"""検査の層の読むだけの関数 preview (seizou.core.checks.preview) の約束。

preview は、保存の前に、保存の時に検査の層が出す文のうち、今の data で返る物を断らずに集める。
出荷の新規登録の数量の欄の下の字と、受注の明細の候補の口の for=shipping が、数を入れる前の明細で呼ぶ。

- 止めると知らせるの文を、登録の順に (mode, CheckWarning) の並びで返す。CheckViolation を出さず、文に「保存できません。」の頭を付けない。
- 設定が通す (off) の検査は呼ばない。
- run で例外を出す検査 (数の無い data を前提にしない会社の検査など) と、会社の設定の mode が不正な検査は飛ばし、ほかの検査の文は返す。
- 同じ登録で run は今のとおり止める (preview は run を変えない)。

出所: 精査 Q1 (2026-11-10) の番号 1 と、本体の決め 11-11 14:2x (開発の記録 docs/nights/20261111/N421.md)。
作り物の検査だけを使い、登録は試験の後に消す (tests/test_core_checks.py と同じ後始末)。
"""

import pytest

from seizou.core import checks
from seizou.core.exceptions import CheckViolation

ACTION = "tests.preview"


class _Say(checks.Check):
    """mode と文を決めて作る作り物の検査。呼ばれた data と利用者を控える。"""

    action = ACTION

    def __init__(self, name, mode, message):
        self.name = name
        self.mode = mode
        self.message = message
        self.calls = []

    def run(self, data, user):
        self.calls.append((data, user))
        return self.message


class _Broken(checks.Check):
    """数 (qty) を前提にした作り物の会社の検査。数の無い data では KeyError を出す。"""

    name = "T09"
    action = ACTION
    mode = "warn"

    def run(self, data, user):
        return "数は " + str(data["qty"])


class _BadSetting(checks.Check):
    """会社の設定の値が不正な作り物の検査 (current_mode が ValueError を出す)。"""

    name = "T08"
    action = ACTION
    setting_key = "over_shipment"

    def run(self, data, user):  # pragma: no cover - 呼ばれない
        return "呼ばれない"


@pytest.fixture(autouse=True)
def _clean_registry():
    checks.clear(ACTION)
    yield
    checks.clear(ACTION)


def test_preview_returns_stop_and_warn_with_mode_without_raising():
    checks.register(_Say("T01", "warn", "知らせるの文"))
    checks.register(_Say("T02", "stop", "止めるの文"))
    checks.register(_Say("T03", "warn", None))

    found = checks.preview(ACTION, {"order_line": "作り物の明細"}, user=None)

    assert found == [
        ("warn", checks.CheckWarning("T01", "知らせるの文")),
        ("stop", checks.CheckWarning("T02", "止めるの文")),
    ]
    # 止めるの文にも、保存の時の赤の頭を付けない
    assert all(not warning.message.startswith(checks.STOP_HEAD) for _mode, warning in found)
    # 同じ登録で、run は今のとおり止める
    with pytest.raises(CheckViolation) as info:
        checks.run(ACTION, {"order_line": "作り物の明細"}, user=None)
    assert info.value.code == "T02"
    assert info.value.message == checks.STOP_HEAD + "止めるの文"


def test_preview_passes_data_and_user_to_run():
    said = checks.register(_Say("T01", "warn", "知らせるの文"))
    data = {"order_line": "作り物の明細"}

    checks.preview(ACTION, data, user="作り物の利用者")

    assert said.calls == [(data, "作り物の利用者")]


def test_preview_does_not_call_off():
    off = checks.register(_Say("T01", "off", "通すの文"))

    assert checks.preview(ACTION, {}, user=None) == []
    assert off.calls == []


def test_preview_skips_check_that_raises_and_keeps_others():
    checks.register(_Broken())
    checks.register(_Say("T02", "stop", "止めるの文"))

    assert checks.preview(ACTION, {"order_line": "作り物の明細"}, user=None) == [
        ("stop", checks.CheckWarning("T02", "止めるの文")),
    ]


def test_preview_skips_check_with_bad_setting(settings):
    settings.SEIZOU = {"over_shipment": "maybe"}
    checks.register(_BadSetting())
    checks.register(_Say("T02", "warn", "知らせるの文"))

    assert checks.preview(ACTION, {}, user=None) == [("warn", checks.CheckWarning("T02", "知らせるの文"))]


def test_preview_of_unknown_action_is_empty():
    assert checks.preview("tests.preview_none", {}, user=None) == []
