"""標準のポカよけ (RULES.md の「標準のポカよけ」。R01・R04・R06・R07・R24・R26)。

mode は会社の設定 (over_shipment・over_workorder・over_payment・progress_staff) の値をそのまま使う。既定は warn。
文は RULES.md の番号の文と同じ趣旨で、何が起きて何をすればよいかを書く。
data の鍵は各 action を呼ぶ画面の土台と services の約束 (部品の公開する関数の設計の決めの引数の名前) に合わせる。
R01 は 1 件の出荷・出荷の編集・一括出荷の 3 つの action に掛ける (一括出荷は開発の記録 docs/nights/20261104/N124.md)。
R07 は実績の入力と実績の編集の 2 つの action に掛ける。R26 は実績の入力だけに掛ける。
"""

from __future__ import annotations

from decimal import Decimal

from seizou.core.checks import Check, register
from seizou.core.checks import tools
from seizou.core.templatetags.seizou_tags import yen


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _num(value) -> str:
    """知らせに出す数の字。末尾の 0 を落とす (10.00 → 10、4.50 → 4.5)。桁がそろわず読みにくいため (作者の決め)。

    訳: 知らせに出す数の桁をそろえて読みやすくするため。出所 = 未確認 (本人の初見テスト 2 回目の所見の直し = docs/nights/20261103.md の「初見テスト 2 回目の所見の直し 6 件」。本人の言葉は記録に無い)。
    変えてよい条件 = Issue「決まりの提案」でこの訳に対して議論してから。
    """
    dec = _dec(value).normalize()
    if dec == dec.to_integral_value():
        return str(dec.quantize(Decimal(1)))
    return format(dec, "f")


class OverShipment(Check):
    """R01: 出荷の数の合計が、受注の明細の数を超えたら。

    出荷済みが 0 より多いときは、文の頭に合計の内訳 (「出荷済み 4 と今回の 7 で、」) を付ける。合計が何の足し算か分からない、
    の声があったため (作者の決め)。出荷済みが 0 なら合計は今回の数なので付けない。一括出荷の R01 も同じ文を使う。
    訳: 出荷の数の合計が受注の明細の数を超えた間違いに気づかせるため (止めるか知らせるかは会社が選ぶ。既定は知らせる。残はマイナスも見せる)。出所 = 会社の設定 over_shipment (RULES.md R01・作者の会期前の決まりの表 (2026-11-01))。
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
            breakdown = f"出荷済み {_num(shipped)} と今回の {_num(qty)} で、" if shipped > 0 else ""
            return (
                f"{breakdown}出荷の数の合計 ({_num(total)}) が受注の数 ({_num(line.qty)}) を超えます。"
                f"出荷できる残りは {_num(_dec(line.qty) - shipped)} です。数を確かめてください。"
            )
        return None


class OverShipmentOnUpdate(Check):
    """R01: 出荷を直した数で、出荷の数の合計が受注の明細の数を超えたら (出荷の編集)。

    data の object は直す前の出荷 (土台の入力の画面が渡す)。合計は、出荷済みの数から直す前の数を引き、新しい数を足す。
    訳: 出荷を直した数で、出荷の数の合計が受注の明細の数を超えた間違いに気づかせるため (R01 を出荷の編集にも掛ける)。出所 = 会社の設定 over_shipment (RULES.md R01・作者の会期前の決まりの表 (2026-11-01))。
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
    """R01: 一括出荷 (shipping.ship_lines) の明細ごとに、出荷の数の合計が受注の明細の数を超えたら (開発の記録 docs/nights/20261104/N124.md)。

    data の lines は (受注の明細, 数量) の組の list (shipping の一括出荷の form の service_kwargs)。
    1 行ずつ OverShipment と同じ式で見て、超える行の文の頭に受注番号と行の番号を付け、1 つの文につなぐ。
    止めるなら全部の行を保存しない (services の ship_lines は 1 つの atomic)。
    訳: 一括出荷の明細ごとに、出荷の数の合計が受注の明細の数を超えた間違いに気づかせるため (R01 を一括出荷にも掛ける)。出所 = 会社の設定 over_shipment (RULES.md R01・作者の会期前の決まりの表 (2026-11-01))。
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

    訳: 出荷済みの数より少ない数に受注の明細を変えた間違いに気づかせるため (R01 を裏から見た同じ事なので、R01 と同じ設定の鍵を使う)。出所 = 会社の設定 over_shipment (RULES.md R04・作者の会期前の決まりの表 (2026-11-01)。止めるから移したのは決め 11-02 13:1x)。
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

    訳: 指示書の数量の合計が受注の明細の数を超えた間違いに気づかせるため (多めに作る会社があるので、止めるか知らせるかは会社が選ぶ)。出所 = 会社の設定 over_workorder (RULES.md R06・作者の会期前の決まりの表 (2026-11-01))。
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

    訳: 実績の数が工程の予定の数を超えた間違いに気づかせるため (歩留まりで超える実数は記録する = 現物優先)。出所 = 会社の設定 over_workorder (RULES.md R07・作者の会期前の決まりの表 (2026-11-01)。R06 と同じ鍵にしたのは決め 11-02 07:3x。現物優先は本人の決め 11-02 13:0x「基本は現物の動きを記録していくものと捉えて」)。
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


class OverProgressOnUpdate(Check):
    """R07: 実績を直した数で、その工程の実績の合計が予定の数を超えたら (実績の編集)。

    data の object は直す前の実績 (土台の入力の画面が渡す)。合計は、工程の実績の合計から直す前の数を引き、新しい数を足す。
    数は良品の数 (実績の数) だけを比べ、不良の数は比べない (OverProgress と同じ。作者の決め)。
    訳: 実績を直した数が工程の予定の数を超えた間違いに気づかせるため (R07 を実績の編集にも掛ける。歩留まりで超える実数は記録する = 現物優先)。出所 = 会社の設定 over_workorder (RULES.md R07・作者の会期前の決まりの表 (2026-11-01)) と AI の案 (実績を直す画面 = 作者の決め (11-07 07:04) に、入れるときと同じ R07 も当てる形。11-07)。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R07"
    action = "progress.update_progress"
    setting_key = "over_workorder"

    def run(self, data, user):
        progress = data.get("object")
        qty = data.get("qty")
        if progress is None or qty is None:
            return None
        step = progress.workorder_step
        others = tools.recorded_qty(step) - _dec(progress.qty)
        total = others + _dec(qty)
        if total > _dec(step.planned_qty):
            return (
                f"実績の合計 ({_num(total)}) が予定の数 ({_num(step.planned_qty)}) を超えます。"
                f"この実績に入れられる数は {_num(_dec(step.planned_qty) - others)} までです。数を確かめてください。"
            )
        return None


class OverPayment(Check):
    """R24: 入金の金額が、請求書の残を超えたら (入金の記録)。

    data の object は入金を記録する請求書 (土台の入力の画面が URL の pk の請求書を渡す)、amount は今回の入金の金額。
    請求書は渡された物の属性 (total・status・paid_total()・balance()) で読み、billing の model を import しない。
    残と同じ額 (「残の全額を入れる」で入れた額) は知らせない。残が 0 以下 (入金済み・多い入金の後) なら、どの額でも知らせる。
    取消の請求書は見ない (service の R21 が先に赤で止める。黄の後に赤を出さないため)。
    訳: 残を超える入金 (同じ入金の 2 重の記録など) に、保存の前に気づかせるため (止めるか知らせるかは会社が選ぶ。既定は知らせる。残はマイナスも見せる)。出所 = 会社の設定 over_payment (RULES.md R24・決め 11-07 00:38)。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R24"
    action = "billing.record_payment"
    setting_key = "over_payment"

    def run(self, data, user):
        invoice = data.get("object")
        amount = data.get("amount")
        if invoice is None or amount is None:
            return None
        if getattr(invoice, "status", None) == "cancelled":
            return None
        balance = invoice.balance()
        if _dec(amount) > balance:
            total = invoice.paid_total() + _dec(amount)
            return (
                f"入金の合計 ({yen(total)}) が請求書の合計 ({yen(invoice.total)}) を超えます。"
                f"この請求書の残は {yen(balance)} です。同じ入金を 2 回記録していないか、金額を確かめてください。"
            )
        return None


class ProgressWithoutStaff(Check):
    """R26: 社員の一覧に使用中の社員が居る会社で、作業した人 (社員番号か、社員の一覧に居ない人の名前) の無い実績を入れたら (実績の入力)。

    data の staff は作業した社員 (masters の Staff)、staff_name は社員の一覧に居ない人の名前 (progress.services.record_progress の引数)。
    staff が無く、staff_name が空 (空白だけも空と見る) なら知らせる。実績の編集 (progress.update_progress) には掛けない
    (社員を登録する前に入れた実績を直すたびに、知らせが出ないようにするため)。
    訳: 共用の端末で実績を入れる現場で、誰が作ったかの無い実績に気づかせるため (止めるか知らせるかは会社が選ぶ。既定は知らせる)。社員の一覧に使用中の社員が 1 人も居ない会社では何もしない (各自のアカウントでログインして入れる会社の動きを変えない)。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M02)・会社の設定 progress_staff・本人の決め 11-11 09:0x。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R26"
    action = "progress.record_progress"
    setting_key = "progress_staff"

    def run(self, data, user):
        if not tools.has_active_staff():
            return None
        if data.get("staff") is not None:
            return None
        if str(data.get("staff_name") or "").strip():
            return None
        return "作業した人が入っていません。社員番号か、社員の一覧に居ない人の名前を入れてください。"


class LumpCompletion(Check):
    """R29: 一式の発注の明細を、受入があるのに一式の金額の載った受入が無いうちに完了にしたら (発注の明細の「完了にする」)。

    data の object は完了にする発注の明細 (土台の入力の画面が URL の pk の明細を渡す)。見方は tools.completes_without_lump_amount
    (purchasing.services の同じ名前の関数。受入が 0 件の一式の明細・取消の発注・取り消した明細は偽)。
    訳: 一式の発注の明細を、一式の金額が載らないまま完了にした間違いに気づかせるため (止めるか知らせるかは会社が選ぶ。既定は知らせる)。出所 = 本人の決め 11-11 12:5x (ポカよけはまず分かるようにし、止めるかは会社が選ぶ)・13:1x (今朝の断る処理を知らせるに変える)・会社の設定 lump_completion。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R29"
    action = "purchasing.mark_completed"
    setting_key = "lump_completion"
    #: R29 の文 (受入の入力の LumpCompletionOnReceipt も同じ文。止めるのときは run が頭に「保存できません。」を付ける)
    message = (
        "一式の明細で、受け入れた物に一式の金額がまだ載っていません。完了にすると、この明細の支払の金額は 0 円のまま残ります。"
        "全部を受け入れたことにするときは、残りの数で受入を入れてください。"
    )

    def run(self, data, user):
        line = data.get("object")
        if line is None:
            return None
        if tools.completes_without_lump_amount(line):
            return self.message
        return None


class LumpCompletionOnReceipt(Check):
    """R29: 受入の入力の「残りは受け入れずに完了にする」で、一式の発注の明細を一式の金額の載った受入が無いまま完了にしたら。

    data の line は発注の明細、qty は今回の受入の数、close_rest は「残りは受け入れずに完了にする」(purchasing.services.receive の引数。受入の form の service_kwargs)。
    close_rest が真のときだけ見る。今回の数で数量に届く受入は一式の全額が載るので知らせない (tools.completes_without_lump_amount の adding_qty)。
    訳: 一式の発注の明細を、一式の金額が載らないまま完了にした間違いに気づかせるため (止めるか知らせるかは会社が選ぶ。既定は知らせる。R29 を受入の入力にも掛ける)。出所 = 本人の決め 11-11 12:5x (ポカよけはまず分かるようにし、止めるかは会社が選ぶ)・13:1x (今朝の断る処理を知らせるに変える)・会社の設定 lump_completion。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R29"
    action = "purchasing.receive"
    setting_key = "lump_completion"

    def run(self, data, user):
        if not data.get("close_rest"):
            return None
        line = data.get("line")
        qty = data.get("qty")
        if line is None or qty is None:
            return None
        if tools.completes_without_lump_amount(line, adding_qty=qty):
            return LumpCompletion.message
        return None


BUILTIN = (OverShipment(), OverShipmentOnUpdate(), OverShipmentLines(), OrderBelowShipped(), OverWorkorder(), OverProgress(), OverProgressOnUpdate(), OverPayment(), ProgressWithoutStaff(), LumpCompletion(), LumpCompletionOnReceipt())

for _check in BUILTIN:
    register(_check)
