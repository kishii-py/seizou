"""配布物の static に同梱する物 (htmx と Tom Select) を取って置く。

scripts/vendor.toml の path を持つ項目を、版を入れた URL から取り、sha256 が一覧と合うときだけ置く。
置いてある物の sha256 が一覧と合えば取り直さない。標準の library だけで動く。

使い方:
    uv run python scripts/fetch_vendor.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_css import ROOT, BuildError, download, load_items, sha256_of


def main() -> int:
    try:
        for item in load_items():
            if "path" not in item:
                continue
            dest = ROOT / item["path"]
            if dest.exists() and sha256_of(dest) == item["sha256"]:
                print(f"そのまま: {item['path']} ({item['name']} {item['version']})")
                continue
            download(item["url"], dest, item["sha256"])
            dest.chmod(0o644)
            print(f"取りました: {item['path']} ({item['name']} {item['version']})")
    except BuildError as e:
        print(f"同梱物を取れませんでした。\n{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
