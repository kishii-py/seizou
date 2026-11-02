"""プラグインの目録と /seizou:build の本文を確かめる (標準の library だけ。Django を読まない)。"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "plugin" / ".claude-plugin" / "plugin.json"
SKILL = ROOT / "plugin" / "skills" / "build" / "SKILL.md"


def _read_skill():
    return SKILL.read_text(encoding="utf-8")


def _split_frontmatter(text):
    """冒頭の --- と --- の間 (見出し) と、その後 (本文) に分ける。"""
    lines = text.splitlines()
    assert lines and lines[0].strip() == "---", "冒頭の見出しが --- で始まっていない"
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1 :])
    raise AssertionError("冒頭の見出しの終わりの --- が無い")


def test_manifest_is_json_named_seizou():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["name"] == "seizou"


def test_manifest_has_only_name_version_description():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert set(data) == {"name", "version", "description"}
    assert data["version"] == "0.1.0"
    assert data["description"].strip()


def test_skill_exists_and_not_empty():
    assert SKILL.is_file()
    assert _read_skill().strip()


def test_skill_frontmatter_has_name_and_description():
    head, _ = _split_frontmatter(_read_skill())
    fields = {}
    for line in head:
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    assert fields.get("name") == "build"
    assert fields.get("description")


def test_skill_has_two_fixed_questions():
    _, body = _split_frontmatter(_read_skill())
    assert "会社の名前と業種" in body
    assert "部品マスターに足したい項目" in body


def test_skill_names_build_command_and_answers_file():
    _, body = _split_frontmatter(_read_skill())
    assert "seizou new" in body
    assert "answers.yaml" in body


def test_skill_tells_to_reopen_after_installing_uv():
    _, body = _split_frontmatter(_read_skill())
    assert "開き直" in body


def test_skill_has_no_later_version_questions():
    _, body = _split_frontmatter(_read_skill())
    for word in ("ロゴ", "振込先", "本番か"):
        assert word not in body, word


def test_skill_has_no_html_comment():
    text = _read_skill()
    assert "<!--" not in text
