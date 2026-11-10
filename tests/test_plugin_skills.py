"""プラグインの skill の本文 (plugin/skills/*/SKILL.md) の形と言い回しを確かめる (標準の library だけ。Django を読まない)。

全部の SKILL.md を見るテストは、後から入る skill にも同じ決まりで効く。
skill ごとの中身のテストは、その skill の名前を付けて分ける。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "plugin" / "skills"

# このテストのファイルが中身を見る skill
SKILLS = ["connect", "manual"]

# 書かない言い回し (文書の決まり)
BANNED_PHRASES = ["壊れない", "準拠"]

# 鍵の値に見える字の並び: base64 の字が 32 字以上続き、英大文字・英小文字・数字が混ざる物。
# URL は外してから探す (URL の道の部分は base64 の字だけで長く続くことがあるため)。
# tests/test_docs_readme.py の見方を写した。
SECRET_LIKE = re.compile(r"[A-Za-z0-9+/]{32,}={0,2}")
URL = re.compile(r"https?://\S+")

# 鍵の置き場の名前の = の直後に、値の字が続く所 (.env の行に値を書いた形)。
SECRET_ASSIGNED = re.compile(r"_SECRET=[A-Za-z0-9+/_-]")


def _skill_path(name):
    return SKILLS_DIR / name / "SKILL.md"


def _all_skill_paths():
    paths = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    assert paths, "plugin/skills/ の下に SKILL.md が無い"
    return paths


def _read(path):
    return path.read_text(encoding="utf-8")


def _split_frontmatter(text):
    """冒頭の --- と --- の間 (見出し) と、その後 (本文) に分ける。"""
    lines = text.splitlines()
    assert lines and lines[0].strip() == "---", "冒頭の見出しが --- で始まっていない"
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1 :])
    raise AssertionError("冒頭の見出しの終わりの --- が無い")


def _body(name):
    _, body = _split_frontmatter(_read(_skill_path(name)))
    return body


def _looks_secret(run):
    return (
        re.search(r"[A-Z]", run) is not None
        and re.search(r"[a-z]", run) is not None
        and re.search(r"[0-9]", run) is not None
    )


def test_connect_and_manual_exist():
    for name in SKILLS:
        path = _skill_path(name)
        assert path.is_file(), f"plugin/skills/{name}/SKILL.md が無い"
        assert _read(path).strip(), f"plugin/skills/{name}/SKILL.md が空"


def test_frontmatter_name_matches_folder():
    for path in _all_skill_paths():
        head, _ = _split_frontmatter(_read(path))
        fields = {}
        for line in head:
            if not line.strip():
                continue
            key, sep, value = line.partition(":")
            assert sep, f"{path.parent.name} の見出しに「鍵: 値」でない行がある: {line!r}"
            fields[key.strip()] = value.strip()
        assert set(fields) == {"name", "description"}, (
            f"{path.parent.name} の見出しの鍵が name と description の 2 つでない: {sorted(fields)}"
        )
        assert fields["name"] == path.parent.name, (
            f"{path.parent.name} の name がフォルダの名前と違う: {fields['name']!r}"
        )
        assert fields["description"], f"{path.parent.name} の description が空"


def test_no_banned_phrases():
    for path in _all_skill_paths():
        text = _read(path)
        for phrase in BANNED_PHRASES:
            assert phrase not in text, f"{path.parent.name} の SKILL.md に「{phrase}」がある"


def test_no_secret_like_values():
    for path in _all_skill_paths():
        text = _read(path)
        found = [run for run in SECRET_LIKE.findall(URL.sub(" ", text)) if _looks_secret(run)]
        assert not found, (
            f"{path.parent.name} の SKILL.md に鍵の値に見える字の並びがある: {[run[:8] + '…' for run in found]}"
        )
        assert SECRET_ASSIGNED.search(text) is None, (
            f"{path.parent.name} の SKILL.md に、鍵の置き場の名前の = の後に値を書いた所がある"
        )


def test_no_html_comment():
    for path in _all_skill_paths():
        assert "<!--" not in _read(path), f"{path.parent.name} の SKILL.md に HTML のコメントがある"


def test_connect_uses_service_and_env_name():
    body = _body("connect")
    for word in ("register_connection", ".env", "SEIZOU_PARTNER_", "SECURITY.md"):
        assert word in body, f"connect の本文に {word} が無い"
    for word in ("Connection.objects", "objects.create"):
        assert word not in body, f"connect の本文に {word} がある (表に直接書かず、register_connection を通す)"


def test_manual_uses_screen_catalog():
    body = _body("manual")
    for word in ("seizou_screens", "--format tsv", "manual/", "images/"):
        assert word in body, f"manual の本文に {word} が無い"


def test_connect_registers_both_directions():
    """connect は 1 つの取引先に送る向きと受ける向きの 2 行を登録させ、入口を今の画面の道で書く。

    片向きだけを登録すると、注文は届くが注文回答・出荷の知らせ・検収が返らない (往復には 2 社とも両方の向きが要る。connect/forms.py の向きの説明)。
    つながりの一覧の入口は、右上の利用者のメニューの「設定」の「普段は触らない物」にあり、左のメニューには無い (core/settings_screen.py)。
    """
    body = _body("connect")
    assert "2 行" in body, "connect の本文に「2 行」が無い (送る向きと受ける向きの 2 行を登録する)"
    for word in ("向きは、この会社と逆", "左のメニューの「つながり」"):
        assert word not in body, f"connect の本文に「{word}」がある"
