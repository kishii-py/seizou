"""標準の説明書の断片 (src/seizou/core/manual_default/) の、前の版の中身の印の一覧を、git の履歴から作り直す。

使い方 (リポジトリの根で。版を出す前に打つ):
    uv run python scripts/manual_history.py          一覧を作り直して書く
    uv run python scripts/manual_history.py --check  今の一覧が履歴と同じかを見る (違えば終わりの番号 1)

作る物: src/seizou/core/manual_default_history.txt。1 行が「<断片の名前> <sha256>」。
読む物: 今の HEAD からたどれる全部の commit のうち、標準の組の *.html を足したか変えた commit の中身。
印は、改行の CRLF を LF にそろえた中身の sha256 (seizou.core.manual_defaults.content_hash と同じ作り方)。

訳: 命令 manage.py seizou_manual_defaults は、置き場の断片がこの一覧の印と同じなら、会社が手を入れていない前の版の標準と見て、
新しい標準に替える (本人の決め 11-14 12:1x)。版を出すたびに作り直すと、その版までの標準の中身が全部載る。
作り物の会社の種の断片 (examples/companies/*/manual/) は入れない (種はわざと標準と違う文を持つ断片があり、入れると
作り物の置き場のその断片が標準に替わってしまうため)。
git の履歴が要るので、CI (履歴なしで取り出す) では動かさない。標準の library だけで書く。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = "src/seizou/core/manual_default"
TARGET = ROOT / "src" / "seizou" / "core" / "manual_default_history.txt"
#: これより小さい中身は載せない (空や書きかけの断片を、会社が空にした断片と取り違えないため。今までの最小は 599 バイト)
MIN_BYTES = 200
HEADER = (
    "# 標準の説明書の断片の、前の版の中身の印の一覧。1 行が「<断片の名前> <改行を LF にそろえた中身の sha256>」。\n"
    "# 命令 manage.py seizou_manual_defaults が、置き場の断片が会社の手の入っていない前の版の標準かを見分けるのに使う。\n"
    "# 手で直さない。版を出す前に uv run python scripts/manual_history.py で作り直す。\n"
)


def content_hash(data: bytes) -> str:
    """改行の CRLF を LF にそろえた中身の sha256 (seizou.core.manual_defaults.content_hash と同じ)。"""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _git(*args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, input=data, capture_output=True, check=True).stdout


def blobs() -> set[tuple[str, str]]:
    """履歴の中の (断片の名前, blob の id) の集まり。消した行は入れない。"""
    raw = _git("log", "--format=", "--raw", "--no-abbrev", "--no-renames", "--", SOURCE).decode("utf-8")
    found = set()
    for line in raw.splitlines():
        if not line.startswith(":"):
            continue
        meta, _, path = line.partition("\t")
        fields = meta.split()
        blob, status = fields[3], fields[4]
        if status.startswith("D") or not path.endswith(".html"):
            continue
        found.add((Path(path).name, blob))
    return found


def read_blobs(ids: list[str]) -> dict[str, bytes]:
    """blob の id → 中身。git cat-file --batch を 1 回だけ呼ぶ。"""
    out = _git("cat-file", "--batch", data=("\n".join(ids) + "\n").encode("ascii"))
    contents = {}
    position = 0
    for blob in ids:
        end = out.index(b"\n", position)
        name, kind, size = out[position:end].decode("ascii").split()
        if name != blob or kind != "blob":
            raise SystemExit(f"blob を読めない: {blob}")
        start = end + 1
        contents[blob] = out[start : start + int(size)]
        position = start + int(size) + 1
    return contents


def build() -> str:
    pairs = blobs()
    contents = read_blobs(sorted({blob for _, blob in pairs}))
    lines = sorted({f"{name} {content_hash(contents[blob])}" for name, blob in pairs if len(contents[blob]) >= MIN_BYTES})
    return HEADER + "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    text = build()
    count = len([line for line in text.splitlines() if line and not line.startswith("#")])
    if "--check" in argv:
        if TARGET.is_file() and TARGET.read_text(encoding="utf-8") == text:
            print(f"一覧は履歴と同じです: {count} 行 ({TARGET})")
            return 0
        print(f"一覧が履歴と違います。uv run python scripts/manual_history.py で作り直してください ({TARGET})", file=sys.stderr)
        return 1
    TARGET.write_text(text, encoding="utf-8")
    print(f"書いた: {count} 行 ({TARGET})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
