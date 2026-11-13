"""「本番の前に残る物」の 1 つ目: Linux は VPS で手で通した事に数え、残りは日本語の Windows の実機と書く。

本人の決め 11-14 08:0x。VPS (AlmaLinux 9) に docs/vps.md の手順で 2 社を手で立て、発注が相手の受注になるまでを画面で
通してあるので、「Linux と Windows で手で通した確認」を残る物に数えない。手元の PC の形 (runserver で試す手順) を Linux で
人の手で通した事は無いので、そこは CI だけと書き分ける。変えてよい条件 = 日本語の Windows の実機で通したとき (この 1 つ目を消す)。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: 一覧の 1 つ目を書いている文書
LIST_DOCS = ["README.md", "docs/roadmap.md", "docs/production.md"]
#: 入れ方の節で、どの OS で通したかを書いている文書
INSTALL_DOCS = ["README.md", "docs/getting-started.md"]
#: 前の字 (どの文書にも残さない)
OLD_PHRASES = ["Linux と Windows で手で通した確認", "人の手で通した確認と、日本語の Windows の実機での確認はまだ無い"]


def _text(name):
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted({*LIST_DOCS, *INSTALL_DOCS}))
def test_old_phrases_are_gone(name):
    text = _text(name)
    for phrase in OLD_PHRASES:
        assert phrase not in text, phrase


@pytest.mark.parametrize("name", LIST_DOCS)
def test_first_item_is_the_japanese_windows_machine(name):
    text = _text(name)
    assert "日本語の Windows の実機で手で通した確認" in text
    assert "Linux は済み" in text
    assert "AlmaLinux 9" in text


def _first_item(name):
    lines = [line for line in _text(name).splitlines() if line.startswith("1. 日本語の Windows の実機で手で通した確認")]
    assert len(lines) == 1, name
    return lines[0].replace("](docs/", "](")


def test_readme_and_roadmap_first_items_are_the_same():
    """README の一覧は roadmap の一覧と同じ字 (link の道だけ違う)。"""
    assert _first_item("README.md") == _first_item("docs/roadmap.md")


@pytest.mark.parametrize("name", INSTALL_DOCS)
def test_install_sections_say_what_was_done_by_hand(name):
    text = _text(name)
    assert "人の手でも入れて通した" in text
    assert "日本語の Windows の実機での確認はまだ無い" in text
    # 手元の PC の形を Linux で人の手で通したとは書かない
    assert "CI" in text
