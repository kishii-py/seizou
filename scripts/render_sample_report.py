"""見本の納品書を PDF に書き出す。WeasyPrint が使えなければ印刷用の HTML を書く。

中身は全部作り物の文字 (実在の会社・人・品番ではない)。試験用の settings (tests.settings) で Django を立てる。
試験 (tests/test_core_reports.py) も、この sample_context を読んで同じ見本を描く。

使い方:
    uv run python scripts/render_sample_report.py <出力のパス>
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = "core/sample_delivery_note.html"


def sample_context() -> dict:
    """見本の納品書の context (作り物)。金額は数量 × 単価で、端数は出ない値にした。"""
    rows = [
        ("MIHON-0001", "見本のフランジ 外径 80", 20, 1200),
        ("MIHON-0002", "見本のシャフト φ20 × 150", 15, 2350),
        ("MIHON-0003", "見本のブラケット (めっき済み)", 40, 480),
        ("MIHON-0004", "見本のスペーサー 厚さ 5", 100, 65),
        ("MIHON-0005", "見本のカバー 板厚 1.6", 8, 3900),
    ]
    lines = [
        {
            "item_code": code,
            "item_name": name,
            "quantity": quantity,
            "unit_price": unit_price,
            "amount": quantity * unit_price,
        }
        for code, name, quantity, unit_price in rows
    ]
    return {
        "company": {
            "name": "架空精機株式会社",
            "address": "〒000-0000 見本県架空市見本町 1-2-3",
            "phone": "000-000-0000",
            "bank_account": "見本銀行 架空支店 普通 0000000",
        },
        "customer": {"name": "架空商事株式会社"},
        "note": {"number": "0001", "date": "2026年11月2日"},
        "lines": lines,
        "total": sum(line["amount"] for line in lines),
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("使い方: uv run python scripts/render_sample_report.py <出力のパス>", file=sys.stderr)
        return 2
    output = Path(args[0])

    sys.path.insert(0, str(ROOT))
    os.environ["DJANGO_SETTINGS_MODULE"] = "tests.settings"
    import django

    django.setup()
    from seizou.core.reports import PdfUnavailable, render_html, render_pdf

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = render_pdf(TEMPLATE, sample_context())
    except PdfUnavailable:
        output.write_text(render_html(TEMPLATE, sample_context()), encoding="utf-8")
        print(f"PDF の部品 (WeasyPrint) が使えないので、印刷用の HTML を書きました: {output}")
        return 0
    output.write_bytes(data)
    print(f"見本の納品書の PDF を書きました: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
