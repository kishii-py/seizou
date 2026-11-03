"""画面の目録のユースケース (seizou.core.screens の usecase)。

全部の画面の class の docstring に「使う人 / いつ / 何のために / 次にすること」があり、目録がそれを返すこと。DB を使わない。
"""

import re
from pathlib import Path

import pytest

from seizou.core.screens import Screen, all_screens, usecase_from

ROOT = Path(__file__).resolve().parents[1]
USECASE = re.compile(r"使う人: .+ / いつ: .+ / 何のために: .+ / 次にすること: .+")
PROMISE = "使う人: … / いつ: … / 何のために: … / 次にすること: …"

# まだ docstring の無い画面の url_name。core:settings は N48 が settings_screen.py を触るので本体が後で足し、ここから外す
PENDING_SCREENS = {"core:settings"}
# 2 段目で部品ごとに docstring を書き、書いた部品をここから外す。終わったら空
PENDING_APPS = {"accounts", "attachments", "billing", "manuals", "stock"}

LINE = "使う人: 営業 / いつ: a / 何のために: b / 次にすること: c"


@pytest.mark.parametrize(
    ("doc", "expected"),
    [
        pytest.param(None, "", id="none"),
        pytest.param("", "", id="empty"),
        pytest.param("受注の一覧。", "", id="no-key"),
        pytest.param(f"受注の一覧。\n\n{LINE}", LINE, id="sentence-and-usecase"),
        pytest.param(
            "受注の一覧。\n\n使う人: 営業 / いつ: a /\n何のために: b / 次にすること: c",
            LINE,
            id="folded-before-purpose",
        ),
        pytest.param(LINE, LINE, id="usecase-only"),
        pytest.param(f"受注の一覧。\n\n{LINE}\n\n後の段落。", LINE, id="stops-at-paragraph"),
        pytest.param(f"受注の一覧。\n    \n    {LINE}\n    ", LINE, id="indented-in-class"),
    ],
)
def test_usecase_from_reads_the_usecase_paragraph(doc, expected):
    assert usecase_from(doc) == expected


def test_screen_has_usecase_with_empty_default():
    screen = Screen(app="orders", view="X", name="n", help="", url_name="fake:x", path="/x/")
    assert screen.usecase == ""


def test_every_screen_has_usecase():
    screens = all_screens()
    assert len(screens) >= 90
    missing = [
        screen.url_name
        for screen in screens
        if screen.app not in PENDING_APPS
        and screen.url_name not in PENDING_SCREENS
        and not USECASE.fullmatch(screen.usecase)
    ]
    assert missing == [], f"ユースケースの無い画面: {missing}"

    plan_rows = [screen for screen in screens if screen.view == "PlanBoardView"]
    assert len(plan_rows) == 2
    assert len({screen.usecase for screen in plan_rows}) == 1


def test_pending_lists_are_minimal():
    screens = all_screens()
    for app in sorted(PENDING_APPS):
        rows = [screen for screen in screens if screen.app == app]
        assert rows, f"{app} の画面が目録に無い"
        assert not all(USECASE.fullmatch(screen.usecase) for screen in rows), f"{app} は書き終えたので PENDING_APPS から外す"

    by_url_name = {screen.url_name: screen for screen in screens}
    for url_name in sorted(PENDING_SCREENS):
        assert url_name in by_url_name, f"{url_name} が目録に無い"
        assert not USECASE.fullmatch(by_url_name[url_name].usecase), f"{url_name} は書き終えたので PENDING_SCREENS から外す"


def test_catalog_usecase_matches_docstring():
    from seizou.core.views import HomeView

    home = next(screen for screen in all_screens() if screen.url_name == "core:home")
    assert home.usecase == usecase_from(HomeView.__doc__)
    assert home.usecase.startswith("使う人: 全員 / いつ: ")


def test_docs_state_the_promise():
    for name in ("CLAUDE.md", "RULES.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert PROMISE in text, name
    rules = (ROOT / "RULES.md").read_text(encoding="utf-8")
    assert "| R20 |" in rules
