"""2 社の作り物の入金の種 (core/demo.py の PART_SEEDS が呼ぶ)。

load(seeds_dir, user) が、種のフォルダの payments.yaml (入金) を読み、services の関数で入れる。
ファイルが無ければ何もせず {"入れた": 0, "飛ばした": 0} を返す (出力の行を出さない)。
入金は請求書の種 (invoices.yaml) の請求書を 得意先の取引先コード × 発行日 で指す。

- 書くのは services の関数だけ (billing.services)。表に直接書かない。標準出力に書かない。
- 2 回入れても重ならない。自然な鍵で既にある物は飛ばす (上書きしない): 入金 = 請求書 × 入金日 × 金額 (取消でない物)。
- 1 件でも services が断ったら、ファイルと「N 件目」を文に入れて ValueError を上げる
  (core の demo.load の transaction.atomic が全体を巻き戻す)。「注意」の文は足さない。
- 入金の方法は日本語の字 (振込・現金・手形・その他) か値で書く。空なら振込。
- 数える物: 入金をそれぞれ 1 件と数える。
- ほかの部品の model は関数の中で import する。取引先の引き方と YAML の読み方は stock/demo_seed.py の物を写した
  (core の private な関数を import しない)。
"""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from seizou.core.exceptions import RuleViolation

from . import services
from .models import Invoice, Payment

PAYMENTS_FILE = "payments.yaml"
PAYMENTS_ROOT = "請求書"
INVOICE_KEYS = ("得意先の取引先コード", "発行日", "入金")
PAYMENT_KEYS = ("入金日", "金額", "入金の方法", "備考")


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


def _method(raw):
    """入金の方法の字を値にする。空なら振込。日本語の字と値 (transfer など) の両方を通す。"""
    text = _text(raw)
    if not text:
        return Payment.METHOD_TRANSFER
    by_label = {label: value for value, label in Payment.METHOD_CHOICES}
    if text in by_label:
        return by_label[text]
    if text in by_label.values():
        return text
    raise ValueError(f"入金の方法「{text}」は使えません。振込・現金・手形・その他 のどれかにしてください。")


# --- 引き方 (stock/demo_seed.py の _partner_by_code を写した) ---


def _partner_by_code(code):
    from seizou.masters.models import Partner

    partner = Partner.objects.filter(code=str(code).strip()).first()
    if partner is None:
        raise ValueError(f"取引先コード {code} が取引先にありません。取引先の種に行を足してください。")
    return partner


def _invoice_of(partner, issued_on):
    """得意先 × 発行日 の取消でない請求書を 1 つ引く。無い・2 つ以上は日本語の ValueError。"""
    found = list(
        Invoice.objects.filter(customer=partner, issued_on=issued_on)
        .exclude(status=Invoice.STATUS_CANCELLED)
        .order_by("pk")[:2]
    )
    if not found:
        raise ValueError(
            f"請求書が見つかりません (得意先の取引先コード {partner.code}・発行日 {issued_on})。"
            "請求書の種 (invoices.yaml) に行を足してください。"
        )
    if len(found) > 1:
        raise ValueError("同じ得意先に同じ発行日の請求書が 2 つ以上あります。")
    return found[0]


# --- 入金 ---


def _load_invoice(entry, user):
    """1 件の請求書への入金の並び。(入れた数, 飛ばした数) を返す。"""
    _check_keys(entry, INVOICE_KEYS, "請求書")
    partner = _partner_by_code(entry.get("得意先の取引先コード"))
    invoice = _invoice_of(partner, _date(entry.get("発行日"), "発行日"))
    made = skipped = 0
    for index, raw in enumerate(entry.get("入金") or [], start=1):
        _check_keys(raw, PAYMENT_KEYS, f"{index} 件目の入金")
        received_on = _date(raw.get("入金日"), "入金日")
        amount = _decimal(raw.get("金額"), "金額")
        method = _method(raw.get("入金の方法"))
        exists = Payment.objects.filter(
            invoice=invoice, received_on=received_on, amount=amount, status=Payment.STATUS_RECORDED
        ).exists()
        if exists:
            skipped += 1
            continue
        services.record_payment(invoice, received_on, amount, user, method=method, note=_text(raw.get("備考")))
        made += 1
    return made, skipped


def load(seeds_dir, user) -> dict:
    """種のフォルダの入金を入れる。返す物は {"入れた": n, "飛ばした": m}。"""
    path = Path(seeds_dir) / PAYMENTS_FILE
    made = skipped = 0
    if not path.is_file():
        return {"入れた": made, "飛ばした": skipped}
    for number, entry in enumerate(_read(path, PAYMENTS_ROOT), start=1):
        with _guard(path.name, number):
            entry_made, entry_skipped = _load_invoice(entry, user)
        made += entry_made
        skipped += entry_skipped
    return {"入れた": made, "飛ばした": skipped}
