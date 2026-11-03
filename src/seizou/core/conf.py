"""選べる設定 (01 の 9 節と 10.5)。

会社の settings.py の辞書 SEIZOU から読み、無ければ既定。鍵は 7 つと hidden_fields。
"""

from django.conf import settings

DEFAULTS = {
    "order_form": "multi",  # 受注の形: multi (1 件に明細が複数) / single (1 件 1 品)
    "partial_shipment": True,  # 分納を許す
    "over_shipment": "warn",  # 受注より多い出荷のポカよけ (R01・R04): warn / stop / off
    "over_workorder": "warn",  # 受注より多い指示書の数量と予定より多い実績 (R06・R07): warn / stop / off
    "order_number": "seq",  # 受注番号の形: seq (連番) / ym_seq (年月 + 連番)
    "hidden_fields": [],  # 使わない注文の項目 (決まりが見る項目は隠せない)
    "production_plan": True,  # 生産計画の画面 (メニューの「生産計画」と工程ごとの盤) を使う
    "access_control": {"department": "log", "write": "log", "money": "log"},  # 権限の判定: 部署・編集の権限・金額の閲覧のそれぞれ off / log / on
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


#: 権限の判定の状態 (off = 使わない / log = 記録だけ / on = 有効。seizou.core.access)
ACCESS_MODES = ("off", "log", "on")


def access_control(kind: str) -> str:
    """権限の判定 kind (department / write / money) の状態。

    access_control が辞書でなければ既定、鍵が無ければ log。真偽の True は on・False は off
    (YAML 1.1 は引用符の無い on と off を真偽で読む)。ACCESS_MODES の外の値は log。知らない kind は KeyError。
    """
    defaults = DEFAULTS["access_control"]
    if kind not in defaults:
        raise KeyError(f"知らない権限の判定: {kind}")
    values = setting("access_control")
    if not isinstance(values, dict):
        values = defaults
    value = values.get(kind, "log")
    if value is True:
        return "on"
    if value is False:
        return "off"
    return value if isinstance(value, str) and value in ACCESS_MODES else "log"


def company_id() -> str:
    """会社の id (組み立ての命令が SEIZOU に書く)。試験では空。"""
    values = getattr(settings, "SEIZOU", {}) or {}
    return values.get("id", "")
