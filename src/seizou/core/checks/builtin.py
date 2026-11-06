"""同梱のポカよけ (RULES.md の「同梱のポカよけ」。R01・R04・R06・R07)。

mode は会社の設定 (over_shipment・over_workorder) の値をそのまま使う。既定は warn。
文は RULES.md の番号の文と同じ趣旨で、何が起きて何をすればよいかを書く。
data の鍵は各 action を呼ぶ画面の土台と services の約束 (01 の 10.4 の引数の名前) に合わせる。
R01 は 1 件の出荷・出荷の編集・一括出荷の 3 つの action に掛ける (一括出荷は N124)。
"""

from __future__ import annotations

from decimal import Decimal

from seizou.core.checks import Check, register
from seizou.core.checks import tools


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _num(value) -> str:
    """知らせに出す数の字。末尾の 0 を落とす (10.00 → 10、4.50 → 4.5)。桁がそろわず読みにくいため (初見テスト 2 回目の所見)。

    訳: 知らせに出す数の桁をそろえて読みやすくするため。出所 = 未確認 (本人の初見テスト 2 回目の所見の直し = docs/nights/20261103.md の「初見テスト 2 回目の所見の直し 6 件」。本人の言葉は記録に無い)。
    変えてよい条件 = Issue「決まりの提案」でこの訳に対して議論してから。
    """
    dec = _dec(value).normalize()
    if dec == dec.to_integral_value():
        return str(dec.quantize(Decimal(1)))
    return format(dec, "f")


class OverShipment(Check):
    """R01: 出荷の数の合計が、受注の明細の数を超えたら。

    訳: 出荷の数の合計が受注の明細の数を超えた間違いに気づかせるため (止めるか知らせるかは会社が選ぶ。既定は知らせる。残はマイナスも見せる)。出所 = 会社の設定 over_shipment (RULES.md R01・仕様 01 の表 (2026-11-01))。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R01"
    action = "shipping.ship"
    setting_key = "over_shipment"

    def run(self, data, user):
        line = data.get("order_line")
        qty = data.get("qty")
        if line is None or qty is None:
            return None
        shipped = tools.shipped_qty(line)
        total = shipped + _dec(qty)
        if total > _dec(line.qty):
            return (
                f"出荷の数の合計 ({_num(total)}) が受注の数 ({_num(line.qty)}) を超えます。"
                f"出荷できる残りは {_num(_dec(line.qty) - shipped)} です。数を確かめてください。"
            )
        return None


class OverShipmentOnUpdate(Check):
    """R01: 出荷を直した数で、出荷の数の合計が受注の明細の数を超えたら (出荷の編集)。

    data の object は直す前の出荷 (土台の入力の画面が渡す)。合計は、出荷済みの数から直す前の数を引き、新しい数を足す。
    訳: 出荷を直した数で、出荷の数の合計が受注の明細の数を超えた間違いに気づかせるため (R01 を出荷の編集にも掛ける)。出所 = 会社の設定 over_shipment (RULES.md R01・仕様 01 の表 (2026-11-01))。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R01"
    action = "shipping.update_shipment"
    setting_key = "over_shipment"

    def run(self, data, user):
        shipment = data.get("object")
        qty = data.get("qty")
        if shipment is None or qty is None:
            return None
        line = shipment.order_line
        others = tools.shipped_qty(line) - _dec(shipment.qty)
        total = others + _dec(qty)
        if total > _dec(line.qty):
            return (
                f"出荷の数の合計 ({_num(total)}) が受注の数 ({_num(line.qty)}) を超えます。"
                f"この出荷に入れられる数は {_num(_dec(line.qty) - others)} までです。数を確かめてください。"
            )
        return None


class OverShipmentLines(Check):
    """R01: 一括出荷 (shipping.ship_lines) の明細ごとに、出荷の数の合計が受注の明細の数を超えたら (N124)。

    data の lines は (受注の明細, 数量) の組の list (shipping の一括出荷の form の service_kwargs)。
    1 行ずつ OverShipment と同じ式で見て、超える行の文の頭に受注番号と行の番号を付け、1 つの文につなぐ。
    止めるなら全部の行を保存しない (services の ship_lines は 1 つの atomic)。
    訳: 一括出荷の明細ごとに、出荷の数の合計が受注の明細の数を超えた間違いに気づかせるため (R01 を一括出荷にも掛ける)。出所 = 会社の設定 over_shipment (RULES.md R01・仕様 01 の表 (2026-11-01))。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R01"
    action = "shipping.ship_lines"
    setting_key = "over_shipment"

    def run(self, data, user):
        texts = []
        for line, qty in data.get("lines") or ():
            text = OverShipment().run({"order_line": line, "qty": qty}, user)
            if text:
                texts.append(f"{line.order.order_no} の {line.line_no} 行目: {text}")
        return " ".join(texts) or None


class OrderBelowShipped(Check):
    """R04: 出荷済みの数より少ない数量に、受注の明細を変えたら。

    訳: 出荷済みの数より少ない数に受注の明細を変えた間違いに気づかせるため (R01 を裏から見た同じ事なので、R01 と同じ設定の鍵を使う)。出所 = 会社の設定 over_shipment (RULES.md R04・仕様 01 の表 (2026-11-01)。止めるから移したのは決め 11-02 13:1x)。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R04"
    action = "orders.update_order"
    setting_key = "over_shipment"

    def run(self, data, user):
        # 受注の入力は明細が複数 (lines = [{"order_line": 明細, "qty": 数}, ...])。1 行の形 (order_line・qty) も受ける。
        pairs = []
        for item in data.get("lines") or ():
            if isinstance(item, dict) and item.get("order_line") is not None and item.get("qty") is not None:
                pairs.append((item["order_line"], item["qty"]))
        if data.get("order_line") is not None and data.get("qty") is not None:
            pairs.append((data["order_line"], data["qty"]))
        for line, qty in pairs:
            shipped = tools.shipped_qty(line)
            if _dec(qty) < shipped:
                return (
                    f"受注の数 ({_num(qty)}) が出荷済みの数 ({_num(shipped)}) より少なくなります。"
                    "数を確かめてください。"
                )
        return None


class OverWorkorder(Check):
    """R06: 指示書の数量の合計が、受注の明細の数を超えたら。

    訳: 指示書の数量の合計が受注の明細の数を超えた間違いに気づかせるため (多めに作る会社があるので、止めるか知らせるかは会社が選ぶ)。出所 = 会社の設定 over_workorder (RULES.md R06・仕様 01 の表 (2026-11-01))。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R06"
    action = "workorders.issue_workorder"
    setting_key = "over_workorder"

    def run(self, data, user):
        line = data.get("order_line")
        qty = data.get("qty")
        if line is None or qty is None:
            return None
        if getattr(getattr(line, "order", None), "status", None) == "cancelled":
            # 取消の受注の明細は、service の R05 (取消した受注には作れない) が先に赤で止める。R06 の数の超えは見ない。
            return None
        issued = tools.issued_qty(line)
        total = issued + _dec(qty)
        if total > _dec(line.qty):
            return (
                f"指示書の数量の合計 ({_num(total)}) が受注の数 ({_num(line.qty)}) を超えます。"
                f"指示書にできる残りは {_num(_dec(line.qty) - issued)} です。数を確かめてください。"
            )
        return None


class OverProgress(Check):
    """R07: 進捗の実績の数が、その工程の予定の数を超えたら。

    訳: 実績の数が工程の予定の数を超えた間違いに気づかせるため (歩留まりで超える実数は記録する = 現物優先)。出所 = 会社の設定 over_workorder (RULES.md R07・仕様 01 の表 (2026-11-01)。R06 と同じ鍵にしたのは決め 11-02 07:3x。現物優先は本人の決め 11-02 13:0x「基本は現物の動きを記録していくものと捉えて」)。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R07"
    action = "progress.record_progress"
    setting_key = "over_workorder"

    def run(self, data, user):
        step = data.get("workorder_step")
        qty = data.get("qty")
        if step is None or qty is None:
            return None
        recorded = tools.recorded_qty(step)
        total = recorded + _dec(qty)
        if total > _dec(step.planned_qty):
            return (
                f"実績の合計 ({_num(total)}) が予定の数 ({_num(step.planned_qty)}) を超えます。"
                f"予定までの残りは {_num(_dec(step.planned_qty) - recorded)} です。数を確かめてください。"
            )
        return None


BUILTIN = (OverShipment(), OverShipmentOnUpdate(), OverShipmentLines(), OrderBelowShipped(), OverWorkorder(), OverProgress())

for _check in BUILTIN:
    register(_check)
