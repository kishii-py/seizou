"""/seizou:fix の本文を確かめる (標準の library だけ。Django を読まない)。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "plugin" / "skills" / "fix" / "SKILL.md"


def _split_frontmatter(text):
    """冒頭の --- と --- の間 (見出し) と、その後 (本文) に分ける。"""
    lines = text.splitlines()
    assert lines and lines[0].strip() == "---", "冒頭の見出しが --- で始まっていない"
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1 :])
    raise AssertionError("冒頭の見出しの終わりの --- が無い")


def _body():
    return _split_frontmatter(SKILL.read_text(encoding="utf-8"))[1]


def _section(body, title):
    """見出し (## で始まる行) が title を含む節の本文。"""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## ") and title in line:
            rest = lines[i + 1 :]
            end = next((j for j, other in enumerate(rest) if other.startswith("## ")), len(rest))
            return "\n".join(rest[:end])
    raise AssertionError(f"{title} の節が無い")


def test_fix_skill_frontmatter():
    head, _ = _split_frontmatter(SKILL.read_text(encoding="utf-8"))
    fields = {}
    for line in head:
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    assert set(fields) == {"name", "description"}
    assert fields["name"] == "fix"
    assert fields["description"]


def test_fix_reads_three_sources():
    body = _body()
    assert "この文を写す" in body
    assert ".seizou/violations.md" in body
    assert "manage.py seizou_check" in body


def test_fix_names_check_command_and_limit():
    body = _body()
    assert "seizou_check" in body
    assert "違反 0 件" in body
    assert "3 回" in body
    assert "manage.py check" in body


def test_fix_forbids_parts_and_silencing():
    body = _body()
    forbidden = _section(body, "してはいけないこと")
    assert "R20" in forbidden
    assert "seizou: ignore" in forbidden
    assert "RULES.md" in forbidden
    assert "R20" in _section(body, "直す")


def test_no_banned_phrases_or_comment():
    text = SKILL.read_text(encoding="utf-8")
    for phrase in ("壊れない", "準拠", "<!--"):
        assert phrase not in text, phrase
