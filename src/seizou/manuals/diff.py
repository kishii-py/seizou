"""版と今の中身の差分 (読むだけ)。Markdown の字の行のまま比べる (画像は `![説明](image:12)` の行のまま)。

difflib の SequenceMatcher で行を 4 種類 (同じ・変えた・消した・足した) に分ける。
表の形と class はテンプレート (manualversion_detail.html) が決める。
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

SAME = "same"
CHANGED = "changed"
REMOVED = "removed"
ADDED = "added"


@dataclass(frozen=True)
class DiffRow:
    """差分の 1 行。left は版の行、right は今の行 (無い側は番号も字も None)。"""

    kind: str
    left_no: int | None
    left: str | None
    right_no: int | None
    right: str | None


def diff_rows(old_text: str, new_text: str) -> list[DiffRow]:
    """old_text (その版) と new_text (今の中身) の行の差分。"""
    old = (old_text or "").splitlines()
    new = (new_text or "").splitlines()
    rows: list[DiffRow] = []
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                rows.append(DiffRow(SAME, i + 1, old[i], j + 1, new[j]))
        elif tag == "replace":
            pairs = min(i2 - i1, j2 - j1)
            for k in range(pairs):
                rows.append(DiffRow(CHANGED, i1 + k + 1, old[i1 + k], j1 + k + 1, new[j1 + k]))
            for i in range(i1 + pairs, i2):
                rows.append(DiffRow(REMOVED, i + 1, old[i], None, None))
            for j in range(j1 + pairs, j2):
                rows.append(DiffRow(ADDED, None, None, j + 1, new[j]))
        elif tag == "delete":
            for i in range(i1, i2):
                rows.append(DiffRow(REMOVED, i + 1, old[i], None, None))
        elif tag == "insert":
            for j in range(j1, j2):
                rows.append(DiffRow(ADDED, None, None, j + 1, new[j]))
    return rows
