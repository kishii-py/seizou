"""役の呼び方: 「世話役」は OSS の側の役だけの名前。会社の中の役は「アカウント管理の担当者」と書く。

訳: 同じ「世話役」が、OSS の側で Issue と PR を受け付けて merge する役 (GOVERNANCE.md) と、会社の中でフィードバックを
確かめて GitHub に送る役の 2 つを指していて、文書に「別の役である」の断りが要った。会社の中の役を別の名前にして、断りを無くす。
変えてよい条件 = 役の呼び方を本人の決めで取り直したとき。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORD = "世話役"

#: 会社の人が読む物と、会社のシステムのコード。ここでは「世話役」を、下の OSS_LINES の行のほかに書かない
COMPANY_SIDE = ["src/seizou", "examples/companies", "docs/operations.md", "docs/screens.md", "SECURITY.md"]
SUFFIXES = {".py", ".html", ".js", ".md", ".txt"}

#: 会社のシステムのコードの中で、OSS の側の役を指している行 (ファイル, 行の中の字)。この字を含む行だけは残す
OSS_LINES = [
    ("src/seizou/feedback/services.py", "GOVERNANCE.md の世話役の節"),
    ("src/seizou/feedback/services.py", "世話役 (作者) が Issue に書いた返事"),
    ("src/seizou/feedback/github.py", "世話役が付ける直る版"),
]

#: OSS の側の役を説明する文書。ここには「世話役」が残る
OSS_SIDE = ["GOVERNANCE.md", "CONTRIBUTING.md", "docs/roadmap.md", "docs/why.md", "docs/glossary.md"]


def _files():
    for name in COMPANY_SIDE:
        path = ROOT / name
        if path.is_file():
            yield path
            continue
        for found in sorted(path.rglob("*")):
            if found.is_file() and found.suffix in SUFFIXES and "__pycache__" not in found.parts:
                yield found


def _is_oss_line(rel, line):
    return any(rel == name and text in line for name, text in OSS_LINES)


def test_company_side_does_not_say_sewayaku():
    """会社の人が読む物と会社のシステムのコードは、会社の中の役を「世話役」と書かない。"""
    hits = []
    for path in _files():
        rel = path.relative_to(ROOT).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if WORD in line and not _is_oss_line(rel, line):
                hits.append(f"{rel}:{number}")
    assert hits == []


@pytest.mark.parametrize(("name", "text"), OSS_LINES)
def test_oss_lines_are_still_there(name, text):
    """残すと決めた行が、今もコードにある (無くなったら OSS_LINES から外す)。"""
    assert text in (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", OSS_SIDE)
def test_oss_side_still_says_sewayaku(name):
    """OSS の側の役を説明する文書には「世話役」が残る。"""
    assert WORD in (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["GOVERNANCE.md", "docs/glossary.md"])
def test_no_other_role_note(name):
    """呼び方を分けたので、「別の役」の断りは要らない。"""
    assert "別の役" not in (ROOT / name).read_text(encoding="utf-8")
