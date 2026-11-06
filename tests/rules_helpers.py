"""決まりの試験 (tests/test_r13〜r19) が共通に使う道具。

RULES.md の決まりの字と、見本のファイルの中で字が出る行の番号を返す。
"""

from pathlib import Path

RULES_MD = Path(__file__).parent.parent / "RULES.md"


def line_of(path: Path, text: str) -> int:
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if text in line:
            return number
    raise AssertionError(f"{path} に {text} が無い")


def rules_md_text(code: str) -> str:
    for line in RULES_MD.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {code} |"):
            return line.split("|")[2].strip().replace("`", "")
    raise AssertionError(f"RULES.md に {code} の行が無い")
