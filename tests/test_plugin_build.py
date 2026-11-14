"""プラグインの目録と /seizou:build の本文を確かめる (標準の library だけ。Django を読まない)。"""

import json
import re
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
    assert data["version"] == "0.2.0"
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


def test_skill_demo_uses_the_given_answers_path():
    # --demo の種は答えの YAML の横の <id>/ にある。写した後の answers.yaml の道では見つからない。
    # cd しないので、例の答えは clone した seizou のフォルダ (${CLAUDE_PLUGIN_ROOT}/..) から数える。
    _, body = _split_frontmatter(_read_skill())
    assert (
        'seizou new "${CLAUDE_PLUGIN_ROOT}/../examples/companies/<id>.yaml" --demo --password-file ~/seizou-<id>-admin.txt'
        in body
    )


def test_skill_prepares_seizou_from_plugin_root_with_lock():
    # seizou の命令が PATH に無くても通るように、clone した seizou で lock のとおりに入れ、uv run --project で打つ (決め 11-08 00:5x)。
    _, body = _split_frontmatter(_read_skill())
    prepare = body[body.index("## 2. 前提を確かめる") : body.index("## 3. 答えを集める")]
    assert 'uv sync --locked --extra pdf --project "${CLAUDE_PLUGIN_ROOT}/.."' in prepare
    assert "uvx" not in body
    commands = _new_commands(body)
    assert len(commands) >= 2
    for line in commands:
        assert 'uv run --project "${CLAUDE_PLUGIN_ROOT}/.." seizou new ' in line, line


def _new_commands(body):
    """seizou new を打つ行 (5 の段の命令の行と、作り物の会社の --demo の行)。"""
    return [line for line in body.splitlines() if "seizou new " in line and ("UV_SYSTEM_CERTS" in line or "--demo" in line)]


def test_skill_keeps_admin_password_out_of_the_conversation():
    _, body = _split_frontmatter(_read_skill())
    commands = _new_commands(body)
    assert len(commands) >= 2
    for line in commands:
        assert "--password-file ~/seizou-<id>-admin.txt" in line, line
    assert "出力のとおりに伝えます" not in body
    assert "読みません・cat しません" in body


def test_skill_installs_uv_with_versioned_url():
    # uv の入れ方は版つきの公式の URL と、古ければ uv self update (案 A。brew と winget は書かない。決め 11-08 00:5x)
    _, body = _split_frontmatter(_read_skill())
    urls = re.findall(r"https://astral\.sh/uv/[^\s`\"|]+", body)
    assert urls
    for url in urls:
        assert re.fullmatch(r"https://astral\.sh/uv/\d+\.\d+\.\d+/install\.(sh|ps1)", url), url
    assert "uv self update" in body and "0.11.0" in body
    for word in ("brew", "winget"):
        assert word not in body, word
