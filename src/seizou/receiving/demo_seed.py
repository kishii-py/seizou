"""2 社の作り物の入荷と引当の種 (core/demo.py の PART_SEEDS が呼ぶ)。

load_after_orders(seeds_dir, user) が、種のフォルダの receivings.yaml (入荷) を読み、services の関数で入れる
(core の demo.load が受注の後に呼ぶ。引当が受注の明細を指すため)。load(seeds_dir, user) は何もしない
(部品の種の段で呼ばれ、{"入れた": 0, "飛ばした": 0} を返す。数は受注の後の口の数が同じ種類に足される)。
ファイルが無ければ何もせず {"入れた": 0, "飛ばした": 0} を返す (出力の行を出さない)。

- 1 件の入荷は 1 つの受注の明細を 得意先の取引先コード × 得意先の注文番号 × 行番号 で指す (ほかの種と同じ指し方。
  受注番号は会社の連番が付けるので種に書かない)。部品は明細の部品、支給元は受注の得意先から引く。
  入荷は明細に引き当てる (services の create_receiving に order_line を渡す自動の引当)。
- 書くのは services の関数だけ (receiving.services)。表に直接書かない。標準出力に書かない。
- 2 回入れても重ならない。自然な鍵 = 受注の明細 × 入荷日 × 出所 の引当のある入荷が既にあれば飛ばす (上書きしない)。
- 1 件でも services が断ったら、ファイルと「N 件目」を文に入れて ValueError を上げる
  (core の demo.load の transaction.atomic が全体を巻き戻す)。「注意」の文は足さない。
- 数える物: 入荷 1 件を 1 件と数える (引当は数えない)。
- ほかの部品の model は関数の中で import する。YAML の読み方と取引先・受注の明細の引き方は stock/demo_seed.py の物を写した
  (core の private な関数を import しない)。
"""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from seizou.core.exceptions import RuleViolation

from . import services
from .models import SOURCE_CHOICES, Receiving

RECEIVINGS_FILE = "receivings.yaml"
RECEIVINGS_ROOT = "入荷"

#: 出所の字 → 値
SOURCE_WORDS = {label: value for value, label in SOURCE_CHOICES}
RECEIVING_KEYS = ("得意先の取引先コード", "得意先の注文番号", "行番号", "出所", "数量", "入荷日", "ロット", "備考")


@contextmanager
def _guard(name, number):
    """services と変換の誤りに、ファイルと位置を付けて ValueError に上げ直す (巻き戻しは呼んだ側の atomic)。"""
    try:
        yield
    except (ValueError, RuleViolation) as exc:
        raise ValueError(f"{name} の {number} 件目: {exc}") from exc


def _read(path, root_key):
    """YAML を読み、根の鍵の下の並びを返す。"""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name} を YAML として読めません: {exc}") from exc
    if not isinstance(data, dict) or root_key not in data:
        raise ValueError(f"{path.name} に「{root_key}」の並びがありません。")
    entries = data[root_key] or []
    if not isinstance(entries, list):
        raise ValueError(f"{path.name} の「{root_key}」は並びで書いてください。")
    return entries


def _check_keys(entry, allowed, what):
    if not isinstance(entry, dict):
        raise ValueError(f"{what}の形が違います (鍵と値の並びではありません)。")
    unknown = [key for key in entry if key not in allowed]
    if unknown:
        raise ValueError(f"{what}の鍵「{'」「'.join(unknown)}」が分かりません。")


def _decimal(raw, label):
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label}「{raw}」を数として読めません。") from exc
    if not value.is_finite():
        raise ValueError(f"{label}「{raw}」を数として読めません。")
    return value


def _date(raw, label):
    import datetime

    if isinstance(raw, datetime.date):
        return raw
    try:
        return datetime.date.fromisoformat(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{label}「{raw}」を日付 (YYYY-MM-DD) として読めません。") from exc


def _text(raw):
    return "" if raw is None else str(raw).strip()


# --- 引き方 (stock/demo_seed.py の _partner_by_code・_order_line_by_key を写した) ---


def _partner_by_code(code):
    from seizou.masters.models import Partner

    partner = Partner.objects.filter(code=str(code).strip()).first()
    if partner is None:
        raise ValueError(f"取引先コード {code} が取引先にありません。取引先の種に行を足してください。")
    return partner


def _order_line_by_key(customer, customer_order_no, line_no):
    """得意先 × 得意先の注文番号 × 行番号で、受注の明細を 1 つ引く。無い・2 つ以上は日本語の ValueError。"""
    from seizou.orders.models import OrderLine

    order_no = _text(customer_order_no)
    if not order_no:
        raise ValueError("得意先の注文番号が空です。受注の明細を指すので、書いてください。")
    try:
        number = int(str(line_no).strip())
    except ValueError as exc:
        raise ValueError(f"行番号「{line_no}」を数として読めません。") from exc
    found = list(
        OrderLine.objects.select_related("order", "item").filter(
            order__customer=customer, order__customer_order_no=order_no, line_no=number
        )[:2]
    )
    where = f"取引先コード {customer.code}・得意先の注文番号 {order_no}・行番号 {number}"
    if not found:
        raise ValueError(f"受注の明細が見つかりません ({where})。受注の種に行を足してください。")
    if len(found) > 1:
        raise ValueError(f"受注の明細が 2 つ以上あります ({where})。")
    return found[0]


# --- 入荷 ---


def _load_receiving(entry, user):
    """1 件の入荷 (と明細への引当)。入れたら真、自然な鍵で既にあれば偽。"""
    _check_keys(entry, RECEIVING_KEYS, "入荷")
    source = SOURCE_WORDS.get(_text(entry.get("出所")))
    if source is None:
        raise ValueError(f"出所は {'・'.join(SOURCE_WORDS)} のどれかにしてください。")
    customer = _partner_by_code(entry.get("得意先の取引先コード"))
    line = _order_line_by_key(customer, entry.get("得意先の注文番号"), entry.get("行番号"))
    received_on = _date(entry.get("入荷日"), "入荷日")
    exists = Receiving.objects.filter(
        allocations__order_line=line, received_on=received_on, source=source
    ).exists()
    if exists:
        return False
    services.create_receiving(
        {
            "item": line.item,
            "partner": line.order.customer,
            "source": source,
            "qty": _decimal(entry.get("数量"), "数量"),
            "received_on": received_on,
            "lot": _text(entry.get("ロット")),
            "note": _text(entry.get("備考")),
        },
        user,
        order_line=line,
    )
    return True


def _load_receivings(path, user):
    made = skipped = 0
    for number, entry in enumerate(_read(path, RECEIVINGS_ROOT), start=1):
        with _guard(path.name, number):
            if _load_receiving(entry, user):
                made += 1
            else:
                skipped += 1
    return made, skipped


def load_after_orders(seeds_dir, user) -> dict:
    """種のフォルダの入荷 (receivings.yaml) を入れる。core の demo.load が受注の後に呼ぶ (引当が受注の明細を指すため)。

    返す物は {"入れた": n, "飛ばした": m}。
    """
    seeds_dir = Path(seeds_dir)
    made = skipped = 0
    receivings = seeds_dir / RECEIVINGS_FILE
    if receivings.is_file():
        made, skipped = _load_receivings(receivings, user)
    return {"入れた": made, "飛ばした": skipped}


def load(seeds_dir, user) -> dict:
    """部品の種の段の口。入荷は受注の後の口 (load_after_orders) で入れるので、ここでは何もしない。"""
    return {"入れた": 0, "飛ばした": 0}
