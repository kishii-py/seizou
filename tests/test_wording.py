"""言葉の決まり (CLAUDE.md の「言葉」): 「外注」と書かない。「協力企業」と書く。

本人の決め 11-03「協力企業にしよう」「外注という言葉の使用を禁止しよう」。
取引先の種類の表示名・画面の字・README・skill・作り物の種・テストの全部が対象。
見ない物: migrations (履歴は書き換えない)・docs/ (記録)・CLAUDE.md (決まりそのものが語を名指す)・この試験。

「届いた注文」と書かない。「受信した注文」と書く (本人の決め 11-04 14:4x「受信した注文がいいかな」)。
見る物は src/・tests/・examples/・README.md・docs/。見ない物は migrations と docs/nights/ (記録) とこの試験。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BANNED = "外注"
SCAN = ("src/seizou", "README.md", "plugin", "examples", "tests", "scripts", "SECURITY.md", "CONTRIBUTING.md", "GOVERNANCE.md", "AGENTS.md", "RULES.md")
SUFFIXES = {".py", ".html", ".js", ".md", ".yaml", ".yml", ".csv", ".txt", ".toml", ".json"}
SKIP_PARTS = {"migrations", "__pycache__", ".tools", "node_modules"}


def _files():
    for base in SCAN:
        path = ROOT / base
        if path.is_file():
            yield path
            continue
        for file in sorted(path.rglob("*")):
            if file.is_file() and file.suffix in SUFFIXES and not (SKIP_PARTS & set(file.parts)) and file.name != "test_wording.py":
                yield file


def test_banned_word_is_not_used():
    hits = []
    for file in _files():
        text = file.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), 1):
            if BANNED in line:
                hits.append(f"{file.relative_to(ROOT)}:{number}")
    assert not hits, "「外注」は書かない (「協力企業」と書く): " + ", ".join(hits)


RECEIVED_OLD = "届いた注文"
RECEIVED_SCAN = ("src", "README.md", "examples", "tests", "docs")


def _received_files():
    for base in RECEIVED_SCAN:
        path = ROOT / base
        if path.is_file():
            yield path
            continue
        for file in sorted(path.rglob("*")):
            parts = file.relative_to(ROOT).parts
            if parts[:2] == ("docs", "nights") or SKIP_PARTS & set(parts) or file.name == "test_wording.py":
                continue
            if file.is_file() and file.suffix in SUFFIXES:
                yield file


def test_received_orders_word_is_not_used():
    hits = []
    for file in _received_files():
        text = file.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), 1):
            if RECEIVED_OLD in line:
                hits.append(f"{file.relative_to(ROOT)}:{number}")
    assert not hits, "「届いた注文」は書かない (「受信した注文」と書く): " + ", ".join(hits)
