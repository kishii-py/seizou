"""チームのもう 1 人の役は、会期中の役として書く (会期の後の今の役は、文書に書かない)。

本人の決め 11-14 08:5x。前は、今も続いている役のように今の形で書いていた。確かな事だけを書くため。
変えてよい条件 = もう 1 人の今の役を、本人の言葉で確かめたとき。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _text(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_why_writes_the_role_as_the_event_period():
    text = _text("docs/why.md")
    assert "会期中は、チームのもう 1 人が画面を通しで確かめる役と動画を受け持った。" in text
    assert "受け持つ。デバッグ" not in text


def test_hackathon_writes_the_team_as_the_event_period():
    text = _text("docs/hackathon.md")
    assert "会期中のチームは 2 人で、" in text
    assert "と一緒に作った。" in text
    assert "と一緒に作っている。" not in text


def test_governance_keeps_the_role_under_the_event_period():
    """GOVERNANCE.md のもう 1 人の行は、会期中の事の下にある (530 回目の形のまま)。"""
    section = _text("GOVERNANCE.md").split("## コードを書いた者と、人が確かめた物", 1)[1]
    assert section.index("会期中 (2026-11-02〜11-08)") < section.index("チームのもう 1 人") < section.index("会期の後")
