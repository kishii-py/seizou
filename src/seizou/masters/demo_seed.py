"""2 社の作り物の休日の種 (core/demo.py の PART_SEEDS が呼ぶ)。

load(seeds_dir, user) が、種のフォルダの holidays.yaml (休日) を読み、services の関数で入れる。
ファイルが無ければ何もせず {"入れた": 0, "飛ばした": 0} を返す (出力の行を出さない)。

- 書くのは services の関数だけ (masters.services.create_holiday)。表に直接書かない。標準出力に書かない。
- 2 回入れても重ならない。自然な鍵 = 日付。同じ日付の休日が既にあれば飛ばす (上書きしない)。
- 1 件でも services か読み方が断ったら、ファイルと「N 件目」を文に入れて ValueError を上げる
  (core の demo.load の transaction.atomic が全体を巻き戻す)。「注意」の文は足さない。
- 種類は日本語の字 (祝日・会社の休み) か値 (public・company) で書く。日付は YAML の日付か YYYY-MM-DD の字。
- 定休日の曜日 (土日) は種に書かない (設定 weekly_holidays)。
- YAML の読み方と鍵の確かめは core の公開の seeds の関数を使う (billing・connect・stock の demo_seed.py と同じ)。
"""

from __future__ import annotations

from pathlib import Path

from seizou.core.seeds import (
    check_keys as _check_keys,
    date as _date,
    guard as _guard,
    read as _read,
)

from . import services
from .models import Holiday

HOLIDAYS_FILE = "holidays.yaml"
HOLIDAYS_ROOT = "休日"
HOLIDAY_KEYS = ("日付", "名前", "種類")
KIND_WORDS = {"祝日": Holiday.KIND_PUBLIC, "会社の休み": Holiday.KIND_COMPANY}


def _kind(raw):
    """種類の字を値にする。日本語の字 (祝日・会社の休み) と値 (public・company) の両方を通す。"""
    text = "" if raw is None else str(raw).strip()
    if text in KIND_WORDS:
        return KIND_WORDS[text]
    if text in KIND_WORDS.values():
        return text
    raise ValueError(f"休日の種類「{text}」は使えません。祝日 か 会社の休み にしてください。")


def load(seeds_dir, user) -> dict:
    """種のフォルダの休日を入れる。返す物は {"入れた": n, "飛ばした": m}。"""
    path = Path(seeds_dir) / HOLIDAYS_FILE
    made = skipped = 0
    if not path.is_file():
        return {"入れた": made, "飛ばした": skipped}
    for number, entry in enumerate(_read(path, HOLIDAYS_ROOT), start=1):
        with _guard(path.name, number):
            _check_keys(entry, HOLIDAY_KEYS, "休日")
            day = _date(entry.get("日付"), "日付")
            kind = _kind(entry.get("種類"))
            if Holiday.objects.filter(date=day).exists():
                skipped += 1
                continue
            name = "" if entry.get("名前") is None else str(entry.get("名前")).strip()
            services.create_holiday({"date": day, "name": name, "kind": kind}, user)
            made += 1
    return {"入れた": made, "飛ばした": skipped}
