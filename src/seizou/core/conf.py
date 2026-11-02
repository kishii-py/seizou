"""選べる設定 (01 の 9 節と 10.5)。

会社の settings.py の辞書 SEIZOU から読み、無ければ既定。鍵は 5 つと hidden_fields。
"""

from django.conf import settings

DEFAULTS = {
    "order_form": "multi",  # 受注の形: multi (1 件に明細が複数) / single (1 件 1 品)
    "partial_shipment": True,  # 分納を許す
    "over_shipment": "warn",  # 受注より多い出荷のポカよけ (R01・R04): warn / stop / off
    "over_workorder": "warn",  # 受注より多い指示書の数量と予定より多い実績 (R06・R07): warn / stop / off
    "order_number": "seq",  # 受注番号の形: seq (連番) / ym_seq (年月 + 連番)
    "hidden_fields": [],  # 使わない注文の項目 (決まりが見る項目は隠せない)
}

CHOICES = {
    "order_form": ("multi", "single"),
    "over_shipment": ("warn", "stop", "off"),
    "over_workorder": ("warn", "stop", "off"),
    "order_number": ("seq", "ym_seq"),
}


def setting(key: str):
    """SEIZOU の値。無い鍵は既定で補う。知らない鍵は KeyError。"""
    if key not in DEFAULTS:
        raise KeyError(f"知らない設定の鍵: {key}")
    values = getattr(settings, "SEIZOU", {}) or {}
    return values.get(key, DEFAULTS[key])


def company_id() -> str:
    """会社の id (組み立ての命令が SEIZOU に書く)。試験では空。"""
    values = getattr(settings, "SEIZOU", {}) or {}
    return values.get("id", "")
