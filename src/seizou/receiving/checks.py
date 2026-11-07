"""入荷のポカよけ (決め 11-06 18:3x)。支給品と預かり品の入荷 (引当) が無い受注の明細の、指示書の発行と出荷を見る。

mode は会社の設定 receiving_required (stop / warn / off) の値をそのまま使う。既定は off (通す) なので、設定を変えない会社の動きは変わらない。
data の鍵は標準のポカよけ (core.checks.builtin) の同じ action の検査と同じ (指示書の発行と 1 件の出荷は order_line、
一括出荷は lines = (受注の明細, 数量) の組の list)。検査は読むだけで、表を書かない。
apps.py の ready が register_checks を呼んで検査の層に登録する (2 回呼んでも、同じ action の同じ name は置き換わるだけ)。
"""

from __future__ import annotations

from seizou.core.checks import Check, register

from . import services

#: 知らせの文 (止めるの赤には、検査の層が「保存できません。」を頭に付ける)
MESSAGE = "この明細には支給品・預かり品の入荷がありません。先に「入荷」で記録してください。"
#: 会社の設定の鍵
SETTING_KEY = "receiving_required"


def _missing(line) -> bool:
    """その明細に入荷の引当が 1 つも無いか。"""
    return services.allocated_total(line) <= 0


class ReceivingRequiredOnIssue(Check):
    """入荷の引当が無い受注の明細に、指示書を発行しようとしたら。

    訳: 支給品・預かり品の入荷 (引当) が無いまま指示書を発行する間違いに気づかせるため (止めるか知らせるかは会社が選ぶ)。出所 = 会社の設定 receiving_required (本人の決め 11-06 18:3x「表面処理はワークがないと何もできないので必ず入荷がある 逆に機械加工は入荷するものは有償、無償かはあるものの材料」)。
    変えてよい条件 = 会社の設定で選ぶ (既定は通す)。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "receiving_required"
    action = "workorders.issue_workorder"
    setting_key = SETTING_KEY

    def run(self, data, user):
        line = data.get("order_line")
        if line is None or not _missing(line):
            return None
        return MESSAGE


class ReceivingRequiredOnShip(Check):
    """入荷の引当が無い受注の明細を、出荷しようとしたら (1 件の出荷)。

    訳: 支給品・預かり品の入荷 (引当) が無いまま出荷する間違いに気づかせるため (止めるか知らせるかは会社が選ぶ)。出所 = 会社の設定 receiving_required (本人の決め 11-06 18:3x「表面処理はワークがないと何もできないので必ず入荷がある 逆に機械加工は入荷するものは有償、無償かはあるものの材料」)。
    変えてよい条件 = 会社の設定で選ぶ (既定は通す)。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "receiving_required"
    action = "shipping.ship"
    setting_key = SETTING_KEY

    def run(self, data, user):
        line = data.get("order_line")
        if line is None or not _missing(line):
            return None
        return MESSAGE


class ReceivingRequiredOnShipLines(Check):
    """入荷の引当が無い受注の明細を、一括出荷 (shipping.ship_lines) で出そうとしたら。

    引当の無い行の頭に受注番号と行の番号を付け、1 つの文につなぐ (標準のポカよけの R01 の一括出荷と同じ形)。

    訳: 一括出荷で、支給品・預かり品の入荷 (引当) が無い明細を出す間違いに気づかせるため (止めるか知らせるかは会社が選ぶ)。出所 = 会社の設定 receiving_required (本人の決め 11-06 18:3x「表面処理はワークがないと何もできないので必ず入荷がある 逆に機械加工は入荷するものは有償、無償かはあるものの材料」)。
    変えてよい条件 = 会社の設定で選ぶ (既定は通す)。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "receiving_required"
    action = "shipping.ship_lines"
    setting_key = SETTING_KEY

    def run(self, data, user):
        texts = [
            f"{line.order.order_no} の {line.line_no} 行目: {MESSAGE}"
            for line, _qty in data.get("lines") or ()
            if _missing(line)
        ]
        return " ".join(texts) or None


CHECKS = (ReceivingRequiredOnIssue(), ReceivingRequiredOnShip(), ReceivingRequiredOnShipLines())


def register_checks() -> None:
    """入荷のポカよけを検査の層に登録する (apps.py の ready が呼ぶ)。"""
    for check in CHECKS:
        register(check)
