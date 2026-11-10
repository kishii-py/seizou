"""公開する文書 (根の 5 つと、README から分けた docs/ の文書) の言い回しを確かめる。

標準の library だけを使い、Django を読まない。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOCS = [
    "README.md",
    "CONTRIBUTING.md",
    "GOVERNANCE.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    # README から分けた文書 (2026-11-10)
    "docs/getting-started.md",
    "docs/plugin.md",
    "docs/screens.md",
    "docs/settings.md",
    "docs/connect.md",
    "docs/rules.md",
    "docs/production.md",
    "docs/development.md",
    "docs/roadmap.md",
    "docs/why.md",
    "docs/hackathon.md",
]

# 書かない言い回し (文書の決まり)
BANNED_PHRASES = ["壊れない", "準拠"]

# README の主役の 1 文
MAIN_SENTENCE = (
    "町工場や中小の製造業が、1 つの命令で自社専用のシステムを持ち、"
    "部品マスターを中心に社内の情報を 1 つにつなぎ、"
    "同じ仕組みで会社どうしもつながる。"
)


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_docs_exist_and_not_empty():
    for name in DOCS:
        path = ROOT / name
        assert path.is_file(), f"{name} がリポジトリの根に無い"
        assert _read(name).strip(), f"{name} が空"


def test_docs_have_no_banned_phrases():
    for name in DOCS:
        text = _read(name)
        for phrase in BANNED_PHRASES:
            assert phrase not in text, f"{name} に「{phrase}」がある"


def test_readme_has_main_sentence():
    assert MAIN_SENTENCE in _read("README.md"), "README に主役の 1 文が無い"


def test_docs_have_no_html_comments():
    for name in DOCS:
        assert "<!--" not in _read(name), f"{name} に HTML のコメントがある"
