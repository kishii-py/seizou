"""GOVERNANCE.md の「コードを書いた者と、人が確かめた物」は、会期中にした事と、会期の後の今の形を分けて書く。

本人の決め 11-14 08:3x。前は、会期中 (2026-11-02〜11-08) の事だけを過去の形で書いていて、今だれが何を確かめているかが
読めなかった。変えてよい条件 = 確かめ方 (だれが main に入れ、だれが何を見るか) を本人の決めで変えたとき。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEAD = "## コードを書いた者と、人が確かめた物"


def _section():
    text = (ROOT / "GOVERNANCE.md").read_text(encoding="utf-8")
    assert text.count(HEAD) == 1
    return text.split(HEAD, 1)[1].split("\n## ", 1)[0]


def test_the_two_lines_are_marked_as_the_event_period():
    section = _section()
    period = section.index("会期中 (2026-11-02〜11-08)")
    morning = section.index("作者は毎朝")
    second = section.index("チームのもう 1 人")
    now = section.index("会期の後")
    assert period < morning < second < now


def test_the_current_form_is_written():
    section = _section()
    line = next(line for line in section.splitlines() if line.startswith("会期の後"))
    assert "検査" in line
    assert "AI が main に入れ" in line
    assert "作者が後から" in line and "決まりの差分" in line
    # 外から届く PR は、次の段のとおり世話役が merge する (足した行がその決まりを変えない)
    assert "外から届く PR" in line and "世話役" in line


def test_the_merge_rule_for_outside_prs_stays():
    assert "merge は世話役が行う。" in _section()
