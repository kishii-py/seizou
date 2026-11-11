"""標準のポカよけ (RULES.md の「標準のポカよけ」。R01・R04・R06・R07・R24・R26・R27・R28・R29・R30)。

mode は会社の設定 (over_shipment・over_workorder・over_payment・progress_staff・billed_shipment・progress_inactive_staff・lump_completion・progress_defect_kind) の値をそのまま使う。既定は warn。
文は RULES.md の番号の文と同じ趣旨で、何が起きて何をすればよいかを書く。
data の鍵は各 action を呼ぶ画面の土台と services の約束 (部品の公開する関数の設計の決めの引数の名前) に合わせる。
R01 は 1 件の出荷・出荷の編集・一括出荷の 3 つの action に掛ける (一括出荷は開発の記録 docs/nights/20261104/N124.md)。
R07 は実績の入力と実績の編集の 2 つの action に掛ける。R26 は実績の入力だけに掛ける。
R27 は出荷の編集・出荷の削除・納品書の取消の 3 つの action に掛ける。
R28 は実績の入力と実績の編集の 2 つの action に掛ける (編集は、直す前と同じ社員のままなら見ない)。
R29 は発注の明細の「完了にする」と受入の入力 (「残りは受け入れずに完了にする」を選んだとき) の 2 つの action に掛ける。
R30 (会社の設定 progress_defect_kind) は実績の入力だけに掛ける。
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


#: R27 の知らせの「…と、」の前の字 (操作ごと・請求済みの種類ごと。billed_by の種類 = line・lump・outside)
_R27_DOING = {
    "update": {"line": "数量を直す", "lump": "出荷を直す", "outside": "数量を直す"},
    "delete": {"line": "出荷を消す", "lump": "出荷を消す", "outside": "出荷を消す"},
}


def _billed_text(shipment, billed, operation) -> str:
    """R27 の知らせの文 (請求済みの種類ごと)。どの請求書か入金かと、直すか消すと何と違ってくるかと、次にすることを書く。金額は書かない。"""
    kind, record = billed
    doing = _R27_DOING[operation][kind]
    number = shipment.shipment_no
    if kind == "line":
        return (
            f"出荷 {number} は請求書 {record.invoice.invoice_no} に入っています。{doing}と、請求書の数量 ({_num(record.qty)}) と違ってきます。"
            "請求書も直すときは、請求書を取り消して作り直してください。"
        )
    if kind == "lump":
        return (
            f"出荷 {number} の受注の明細は、請求書 {record.invoice.invoice_no} の一式の行で請求済みです。"
            f"{doing}と、一式の行の数量 ({_num(record.qty)}) と出荷の合計が違ってきます。"
            "請求書も直すときは、請求書を取り消し、一式の金額を決め直して載せ直してください。"
        )
    return (
        f"出荷 {number} には、請求書の外で受け取った入金 ({record.received_on:%Y-%m-%d}) が記録されています。"
        f"{doing}と、受け取った代金と出荷が違ってきます。入金も直すときは、出荷の詳細でその入金を取り消してから記録し直してください。"
    )


class BilledShipmentOnUpdate(Check):
    """R27: 請求済みの出荷 (請求書に出荷の行で入った出荷・一式の行で請求した明細の出荷・請求書の外で入金を受け取った出荷) を直そうとしたら (出荷の編集)。

    data の object は直す前の出荷 (土台の入力の画面が渡す)。請求済みかは shipping.services.billed_by を道具 (tools.billed_by) で読む。
    services の update_shipment が断る出荷 (受注が取消 = R05・発行の納品書に入っている = R08) には文を返さない
    (黄の後に赤を出さないため。R24 が取消の請求書を見ないのと同じ)。発行の納品書に入った請求済みの出荷は、直すと R08 の赤だけが出る
    (納品書を取り消すと、取消の R27 が知らせ、その後の直しでこの検査が知らせる)。数量を変えたかどうかは見ない (出荷日と備考だけの直しでも知らせる)。
    訳: 請求済みの出荷を直すか消すと、請求書の明細の写し (数量・一式の行の数量) や受け取った代金の記録と、出荷の記録が食い違うので、直す前に気づかせるため (止めるか知らせるかは会社が選ぶ。既定は知らせる)。納品書を請求の条件から外したので、納品書の取消の断りが間接に止めていた守りの代わりに置く。出所 = 本人の決め 11-11 12:5x〜13:0x (「止めない」「わかるようになっていればいい」「会社ごとにつけたければ自由にすればいい」)・会社の設定 billed_shipment。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R27"
    action = "shipping.update_shipment"
    setting_key = "billed_shipment"

    def run(self, data, user):
        shipment = data.get("object")
        if shipment is None:
            return None
        order = shipment.order_line.order
        if order.status == type(order).STATUS_CANCELLED:
            return None
        note = shipment.delivery_note
        if note is not None and note.status == type(note).STATUS_ISSUED:
            return None
        billed = tools.billed_by(shipment)
        if billed is None:
            return None
        return _billed_text(shipment, billed, "update")


class BilledShipmentOnDelete(Check):
    """R27: 請求済みの出荷を消そうとしたら (出荷の削除)。

    data の object は消す出荷 (土台の入力の画面が渡す)。services の delete_refusal が断る出荷 (請求書の明細か入金が指す・発行の納品書に入っている・
    取引先につながる・明細を出し終えた) には文を返さない (黄の後に赤を出さないため)。今は、一式の行で請求した明細の出荷だけが当たる。
    請求書の明細と入金が指す出荷を消せるようになったら、同じ式のまま、その出荷にも当たる。
    訳: 請求済みの出荷を直すか消すと、請求書の明細の写し (数量・一式の行の数量) や受け取った代金の記録と、出荷の記録が食い違うので、直す前に気づかせるため (止めるか知らせるかは会社が選ぶ。既定は知らせる)。納品書を請求の条件から外したので、納品書の取消の断りが間接に止めていた守りの代わりに置く。出所 = 本人の決め 11-11 12:5x〜13:0x (「止めない」「わかるようになっていればいい」「会社ごとにつけたければ自由にすればいい」)・会社の設定 billed_shipment。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R27"
    action = "shipping.delete_shipment"
    setting_key = "billed_shipment"

    def run(self, data, user):
        shipment = data.get("object")
        if shipment is None:
            return None
        if tools.delete_refusal(shipment):
            return None
        billed = tools.billed_by(shipment)
        if billed is None:
            return None
        return _billed_text(shipment, billed, "delete")


class BilledShipmentOnNoteCancel(Check):
    """R27: 請求済みの出荷の入った納品書を取り消そうとしたら (納品書の取消)。

    data の object は取り消す納品書 (土台の入力の画面が渡す)。文は shipping.services.billed_notice の 1 か所 (取消の確かめのカードと同じ文。
    金額は書かない)。services の cancel_delivery_note が断る納品書 (取り消し済み) には文を返さない (黄の後に赤を出さないため)。
    文は検査の道具 tools.billed_notice で読む (tools.billed_by と同じく、core を読み込む時に shipping を読まない)。
    訳: 請求済みの出荷を直すか消すと、請求書の明細の写し (数量・一式の行の数量) や受け取った代金の記録と、出荷の記録が食い違うので、直す前に気づかせるため (止めるか知らせるかは会社が選ぶ。既定は知らせる)。納品書は出荷の記録を刷った物なので取り消しても請求書は変わらないが、取り消した後に出荷を直すと請求書と違ってくるので、取り消す前に知らせる。出所 = 本人の決め 11-11 12:5x〜13:0x (「止めない」「わかるようになっていればいい」「会社ごとにつけたければ自由にすればいい」)・本人の決め 11-11 13:0x (請求書に入った出荷の納品書の取消も止めずに知らせる。「それでOK」)・会社の設定 billed_shipment。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R27"
    action = "shipping.cancel_delivery_note"
    setting_key = "billed_shipment"

    def run(self, data, user):
        note = data.get("object")
        if note is None or note.status == type(note).STATUS_CANCELLED:
            return None
        return tools.billed_notice(note) or None


def _inactive_staff_message(check, staff) -> str:
    """R28 の文。続けて足す 1 文は mode で分ける (止めるの文の頭には、検査の層が「保存できません。」を付ける)。"""
    head = f"社員番号 {staff.code} ({staff.name}) は使用停止の社員です。"
    if check.current_mode() == "stop":
        return head + "使用中の社員の番号を入れるか、社員の編集で「使用中」に戻してください。"
    return head + "番号に間違いが無ければ、このまま保存できます。"


def _inactive_staff(data):
    """data の staff が使用停止の社員ならその社員、ほかは None。

    staff の鍵が無い・None・progress.services.KEEP (作業した人を変えない編集) と使用中の社員は None
    (active を持たない物は使用中と見る。core から progress を import しないため)。
    """
    staff = data.get("staff")
    if staff is None or getattr(staff, "active", True):
        return None
    return staff


class ProgressInactiveStaff(Check):
    """R28: 実績の作業した人に、使用停止の社員を入れたら (実績の入力)。

    data の staff は作業した社員 (masters の Staff。progress.services.record_progress の引数)。社員が無い実績 (名前だけと空) は見ない (空は R26 が見る)。
    文は「社員番号 <番号> (<名前>) は使用停止の社員です。」に、知らせるなら「番号に間違いが無ければ、このまま保存できます。」、
    止めるなら「使用中の社員の番号を入れるか、社員の編集で「使用中」に戻してください。」を続ける。
    訳: 打ち間違いで辞めた人の番号に当たったときに気づかせるため。前は使用停止の社員の実績を断ったが、運用の誤りを防ぐ物は、まず分かるようにして、止めるかは会社が選ぶ (北極星のポカよけの考え方 11-11 12:5x)。既定は知らせる。直すときに同じ社員のままなら見ない (前の断りと同じ範囲)。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M02) と本人の決め 11-11 13:1x。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R28"
    action = "progress.record_progress"
    setting_key = "progress_inactive_staff"

    def run(self, data, user):
        staff = _inactive_staff(data)
        if staff is None:
            return None
        return _inactive_staff_message(self, staff)


class ProgressInactiveStaffOnUpdate(Check):
    """R28: 実績の作業した人を、使用停止の社員に直したら (実績の編集)。

    data の object は直す前の実績 (土台の入力の画面が渡す)。staff の鍵が無いか KEEP か None なら見ない。
    社員が使用停止で、直す前の実績の社員と違うときだけ文を返す (同じ社員のままなら何も出さない)。文は ProgressInactiveStaff と同じ。
    訳: 打ち間違いで辞めた人の番号に当たったときに気づかせるため。前は使用停止の社員の実績を断ったが、運用の誤りを防ぐ物は、まず分かるようにして、止めるかは会社が選ぶ (北極星のポカよけの考え方 11-11 12:5x)。既定は知らせる。直すときに同じ社員のままなら見ない (前の断りと同じ範囲)。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M02) と本人の決め 11-11 13:1x。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R28"
    action = "progress.update_progress"
    setting_key = "progress_inactive_staff"

    def run(self, data, user):
        staff = _inactive_staff(data)
        if staff is None:
            return None
        progress = data.get("object")
        if progress is not None and staff.pk == getattr(progress, "staff_id", None):
            return None
        return _inactive_staff_message(self, staff)


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


def _defect_without_kind(data) -> Decimal:
    """区分を選んでいない不良の数。data に不良の行 (defects) があれば区分の無い行の数の合計、無ければ不良の数 (defect_qty)。

    defects が None なら渡していないのと同じに見る。不良の数だけの入力 (区分の欄の無い入力) は、services が区分の無い 1 行にするため、全部を数える。
    """
    defects = data.get("defects")
    if defects is not None:
        return sum((_dec(line.get("qty") or 0) for line in defects if line.get("kind") is None), Decimal("0"))
    return _dec(data.get("defect_qty") or 0)


class DefectWithoutKind(Check):
    """R30: 不良の区分の一覧に使用中の区分がある会社で、区分を選んでいない不良のある実績を入れたら (実績の入力)。

    data の defects は不良の行の並び (1 行 = {"kind": 区分か None, "qty": 数, …})、defect_qty は不良の数 (progress.services.record_progress の引数)。
    区分を選んでいない不良の数 (defects があれば区分の無い行の数の合計、無ければ defect_qty) が 0 なら何も出さない。
    文は「区分を選んでいない不良が <数> あります。」に、知らせるなら「不良の集計では「未設定」の行に入ります。このまま保存できます。」、
    止めるなら「不良の行の区分を選んでください。」を続ける (止めるの文の頭には、検査の層が「保存できません。」を付ける)。
    実績の編集 (progress.update_progress) には掛けない (区分を作る前に入れた実績を直すたびに、知らせが出ないようにするため。R26 と同じ)。
    訳: 区分を選ばずに入れた不良に気づかせるため (区分の無い不良は、集計の「未設定」の行に入り、どの不良が多いかを数えられない)。止めるか知らせるかは会社が選ぶ。既定は知らせる。区分の一覧に使用中の区分が 1 つも無い会社では何もしない (区分を使わない会社の動きを変えない)。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M09)・会社の設定 progress_defect_kind・北極星のポカよけの考え方 (11-11 12:5x)。変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R30"
    action = "progress.record_progress"
    setting_key = "progress_defect_kind"

    def run(self, data, user):
        if not tools.has_active_defect_kinds():
            return None
        without_kind = _defect_without_kind(data)
        if not without_kind > 0:
            return None
        head = f"区分を選んでいない不良が {_num(without_kind)} あります。"
        if self.current_mode() == "stop":
            return head + "不良の行の区分を選んでください。"
        return head + "不良の集計では「未設定」の行に入ります。このまま保存できます。"


#: R31 の文に並べる受注番号の数 (これより多ければ「 ほか <n> 件」を付ける)
_R31_LISTED = 3


def _same_order_no_message(check, customer_order_no, order_nos) -> str:
    """R31 の文。番号は入れた字の前後の空白を落とした字 (照合の字にしない)。受注番号は 3 件までを「、」でつなぐ。金額は書かない。

    続けて足す 1 文は mode で分ける (止めるの文の頭には、検査の層が「保存できません。」を付けるので、「このまま保存できます」と出さない)。
    """
    listed = "、".join(order_nos[:_R31_LISTED])
    if len(order_nos) > _R31_LISTED:
        listed += f" ほか {len(order_nos) - _R31_LISTED} 件"
    head = f"得意先の注文番号 {str(customer_order_no).strip()} は、同じ得意先の受注 {listed} にもあります。"
    if check.current_mode() == "stop":
        return head + "同じ注文書を 2 回入れていないか、既にある受注を確かめてください。"
    return head + "同じ注文書を 2 回入れていないか確かめてください。1 枚の注文書を受注に分けて入れたのなら、このまま保存できます。"


def _same_order_no_text(check, customer, customer_order_no) -> str | None:
    """得意先と番号から、同じ番号の受注があれば R31 の文、無ければ None。得意先が無いか、番号が字でないか空なら見ない。"""
    if customer is None or not isinstance(customer_order_no, str) or not customer_order_no.strip():
        return None
    found = tools.same_customer_order_no(customer, customer_order_no)
    if not found:
        return None
    return _same_order_no_message(check, customer_order_no, found)


class SameCustomerOrderNo(Check):
    """R31: 同じ得意先に、得意先の注文番号が同じ受注が既にあったら (受注の新規登録)。

    data の customer は得意先、customer_order_no は得意先の注文番号 (受注の入力の form の service_kwargs と、MCP の道具と AI チャットの提案の create_order が同じ鍵で渡す)。
    比べるのは照合の字 (orders.services.customer_order_no_key) で、見方は tools.same_customer_order_no。
    文は「得意先の注文番号 <番号> は、同じ得意先の受注 <受注番号の並び> にもあります。」に、知らせるなら「同じ注文書を 2 回入れていないか確かめてください。
    1 枚の注文書を受注に分けて入れたのなら、このまま保存できます。」、止めるなら「同じ注文書を 2 回入れていないか、既にある受注を確かめてください。」を続ける。
    訳: 同じ注文書を 2 人が入れる・同じ注文書を 2 回読み取りに置く・同じ注文が手入力と受信した注文の両方から入る間違いに、保存の前に気づかせるため。1 枚の注文書の部品を受注に分けて入れる会社では同じ番号が正しいので、止めずに知らせる (止めるか知らせるか通すかは会社が選ぶ。既定は知らせる)。別の得意先の同じ番号・番号の空の受注・取消の受注は見ない。直すときは、番号か得意先を変えたときだけ見る。services が断る物 (取消の受注の編集・受注にした読み取り・未処理でない受信した注文・変更と取消の版) には文を返さない。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M35) と北極星のポカよけの考え方 (本人の決め 11-11 12:5x)。変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R31"
    action = "orders.create_order"
    setting_key = "same_customer_order_no"

    def run(self, data, user):
        return _same_order_no_text(self, data.get("customer"), data.get("customer_order_no"))


class SameCustomerOrderNoOnUpdate(Check):
    """R31: 受注の得意先の注文番号か得意先を直して、同じ得意先に同じ番号の受注が既にあるようにしたら (受注の編集)。

    data の object は直す受注 (土台の入力の画面が渡す)。取消の受注は見ない (services の update_order が R05 で先に断る)。
    新しい得意先は data の customer (無ければ受注の得意先)、新しい番号は data の customer_order_no (無ければ受注の番号。設定 hidden_fields で欄を外した会社は鍵が来ない)。
    受注の画面の form は ModelForm で、検証の中で object の得意先と番号を送った値に書き換える (core.history.saved_state の注記) ので、object から直す前の値は読めない。
    そこで、新しい得意先と番号で同じ番号の受注を引き、直す受注そのものが並びに入っていれば (表の今の値が新しい値と同じ = 番号も得意先も変えていない) 何も出さない。
    入っていなければ、並びは直す受注のほかの受注だけなので、その並びで知らせる (直している受注は数えない)。受注番号と状態は form の欄でないので object から読む。
    訳: 同じ注文書を 2 人が入れる・同じ注文書を 2 回読み取りに置く・同じ注文が手入力と受信した注文の両方から入る間違いに、保存の前に気づかせるため。1 枚の注文書の部品を受注に分けて入れる会社では同じ番号が正しいので、止めずに知らせる (止めるか知らせるか通すかは会社が選ぶ。既定は知らせる)。別の得意先の同じ番号・番号の空の受注・取消の受注は見ない。直すときは、番号か得意先を変えたときだけ見る。services が断る物 (取消の受注の編集・受注にした読み取り・未処理でない受信した注文・変更と取消の版) には文を返さない。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M35) と北極星のポカよけの考え方 (本人の決め 11-11 12:5x)。変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R31"
    action = "orders.update_order"
    setting_key = "same_customer_order_no"

    def run(self, data, user):
        order = data.get("object")
        if order is None:
            return None
        if order.status == type(order).STATUS_CANCELLED:
            return None
        customer = data["customer"] if "customer" in data else order.customer
        customer_order_no = data["customer_order_no"] if "customer_order_no" in data else order.customer_order_no
        if customer is None or not isinstance(customer_order_no, str) or not customer_order_no.strip():
            return None
        found = tools.same_customer_order_no(customer, customer_order_no)
        if order.order_no in found or not found:
            return None
        return _same_order_no_message(self, customer_order_no, found)


class SameCustomerOrderNoOnExtraction(Check):
    """R31: 注文書の読み取りを受注にするとき、同じ得意先に、読み取った注文番号と同じ番号の受注が既にあったら (読み取りの受け入れ)。

    data の object は受注にする読み取り (土台の入力の画面が URL の pk の読み取りを渡す)。状態が「読み取った」でない読み取り
    (受注にした・捨てた・受信した注文へ回した。services の accept_extracted が断る) と、得意先の空の読み取りは見ない。
    番号は読み取った中身の order_no (字でなければ見ない)。intake の model は import しない (属性と、object の class の状態の定数で読む)。
    訳: 同じ注文書を 2 人が入れる・同じ注文書を 2 回読み取りに置く・同じ注文が手入力と受信した注文の両方から入る間違いに、保存の前に気づかせるため。1 枚の注文書の部品を受注に分けて入れる会社では同じ番号が正しいので、止めずに知らせる (止めるか知らせるか通すかは会社が選ぶ。既定は知らせる)。別の得意先の同じ番号・番号の空の受注・取消の受注は見ない。直すときは、番号か得意先を変えたときだけ見る。services が断る物 (取消の受注の編集・受注にした読み取り・未処理でない受信した注文・変更と取消の版) には文を返さない。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M35) と北極星のポカよけの考え方 (本人の決め 11-11 12:5x)。変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R31"
    action = "intake.accept_extracted"
    setting_key = "same_customer_order_no"

    def run(self, data, user):
        extraction = data.get("object")
        if extraction is None:
            return None
        if extraction.status != type(extraction).STATUS_EXTRACTED:
            return None
        extracted = extraction.extracted if isinstance(extraction.extracted, dict) else {}
        return _same_order_no_text(self, extraction.customer, extracted.get("order_no"))


class SameCustomerOrderNoOnInbox(Check):
    """R31: 受信した注文を受け入れるとき、同じ得意先に、相手の発注番号と同じ得意先の注文番号の受注が既にあったら (受信した注文の受け入れ)。

    data の object は受け入れる受信した注文 (土台の入力の画面が URL の pk の行を渡す)。変更と取消の版 (revises_id がある行。受注を作らず元の受注を変える)・
    状態が未処理でない行 (受け入れた = R11・断った)・取引先の空の行 (差出人の確認待ち) は、services が断るか受注を作らないので見ない。
    番号は buyer_order_no()、得意先は partner。connect の model は import しない (属性と、object の class の状態の定数で読む)。
    訳: 同じ注文書を 2 人が入れる・同じ注文書を 2 回読み取りに置く・同じ注文が手入力と受信した注文の両方から入る間違いに、保存の前に気づかせるため。1 枚の注文書の部品を受注に分けて入れる会社では同じ番号が正しいので、止めずに知らせる (止めるか知らせるか通すかは会社が選ぶ。既定は知らせる)。別の得意先の同じ番号・番号の空の受注・取消の受注は見ない。直すときは、番号か得意先を変えたときだけ見る。services が断る物 (取消の受注の編集・受注にした読み取り・未処理でない受信した注文・変更と取消の版) には文を返さない。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M35) と北極星のポカよけの考え方 (本人の決め 11-11 12:5x)。変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R31"
    action = "connect.accept"
    setting_key = "same_customer_order_no"

    def run(self, data, user):
        inbox = data.get("object")
        if inbox is None:
            return None
        if inbox.revises_id is not None or inbox.partner_id is None:
            return None
        if inbox.status != type(inbox).STATUS_RECEIVED:
            return None
        return _same_order_no_text(self, inbox.partner, inbox.buyer_order_no())


class SameCustomerOrderNoOnConditionalInbox(SameCustomerOrderNoOnInbox):
    """R31: 受信した注文を、単価・数量・納期を直して受け入れるとき (条件付受諾)。見方と文は SameCustomerOrderNoOnInbox と同じ。

    訳: 同じ注文書を 2 人が入れる・同じ注文書を 2 回読み取りに置く・同じ注文が手入力と受信した注文の両方から入る間違いに、保存の前に気づかせるため。1 枚の注文書の部品を受注に分けて入れる会社では同じ番号が正しいので、止めずに知らせる (止めるか知らせるか通すかは会社が選ぶ。既定は知らせる)。別の得意先の同じ番号・番号の空の受注・取消の受注は見ない。直すときは、番号か得意先を変えたときだけ見る。services が断る物 (取消の受注の編集・受注にした読み取り・未処理でない受信した注文・変更と取消の版) には文を返さない。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M35) と北極星のポカよけの考え方 (本人の決め 11-11 12:5x)。変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    action = "connect.accept_with_conditions"


def _subcontract_return_message(check, body) -> str:
    """R33 の文。続けて足す 1 文は mode で分ける (止めるの文の頭には検査の層が「保存できません。」を付けるので、止めるに「このまま保存できます」を出さない)。金額は書かない。"""
    if check.current_mode() == "stop":
        return body + "数と渡した日を確かめてから、もう一度保存してください。"
    return body + "数と記録に間違いが無ければ、このまま保存できます。"


def _short_return_text(label, qty, short) -> str:
    """R33 の目減りで完了の文 (受入の「残りは受け入れずに完了にする」と、発注の明細の「完了にする」で同じ文)。"""
    return f"{label} は、渡した {_num(qty)} のうち {_num(short)} が戻らないまま完了になります。"


class SubcontractReturnOnReceipt(Check):
    """R33: 指示書の工程を指す発注の明細の受入で、渡した日が空・受入の合計が数量を超える・渡した数に届かないまま「残りは受け入れずに完了にする」(受入の入力)。

    data の line は発注の明細、qty は今回の受入の数、close_rest は「残りは受け入れずに完了にする」(purchasing.services.receive の引数。受入の form の service_kwargs)。
    比べ方は purchasing.services.receipt_return_gap (tools.receipt_return_gap)。指示書の工程を指さない明細と取消の発注の明細は見ない (services が断るので、黄の後に赤を出さない)。
    当たる文を全部つなぐ: 渡した日が空 →「<発注番号>-<行番号> は、まだ協力企業に渡した日が入っていません。」/ 合計が数量を超える →
    「… は、この受入で戻りが <合計> になり、発注の数 <数量> より <差> 多くなります。」/ 目減りで完了 →「… は、渡した <数量> のうち <差> が戻らないまま完了になります。」。
    訳: 協力企業に出した工程の戻りの数の食い違い (渡す前の戻り・多い戻り・目減りで完了) と、戻りを受入と実績で 2 回入れる間違いに気づかせるため (止めるか知らせるかは会社が選ぶ。既定は知らせる)。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M01。目減りと多い戻りを知らせる本は 2 本、多い戻りを止める本は 2 本)・本人の決め 11-11 12:5x (ポカよけはまず分かるようにし、止めるかは会社が選ぶ)・会社の設定 subcontract_return。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R33"
    action = "purchasing.receive"
    setting_key = "subcontract_return"

    def run(self, data, user):
        line = data.get("line")
        qty = data.get("qty")
        if line is None or qty is None:
            return None
        gap = tools.receipt_return_gap(line, qty, close_rest=bool(data.get("close_rest")))
        if gap is None:
            return None
        parts = []
        if gap.not_handed:
            parts.append(f"{gap.label} は、まだ協力企業に渡した日が入っていません。")
        if gap.over > 0:
            parts.append(
                f"{gap.label} は、この受入で戻りが {_num(gap.total)} になり、発注の数 {_num(gap.qty)} より {_num(gap.over)} 多くなります。"
            )
        if gap.short > 0:
            parts.append(_short_return_text(gap.label, gap.qty, gap.short))
        return _subcontract_return_message(self, "".join(parts))


class SubcontractReturnOnComplete(Check):
    """R33: 指示書の工程を指し、渡した日のある発注の明細を、受入の合計が数量に届かないまま完了にしたら (発注の明細の「完了にする」)。

    data の object は完了にする発注の明細 (土台の入力の画面が URL の pk の明細を渡す)。比べ方は purchasing.services.short_return_qty
    (tools.short_return_qty。完了の印が真の明細・取消の発注・取り消した明細は 0)。文は受入の目減りで完了と同じ。
    訳: 協力企業に出した工程の戻りの数の食い違い (渡す前の戻り・多い戻り・目減りで完了) と、戻りを受入と実績で 2 回入れる間違いに気づかせるため (止めるか知らせるかは会社が選ぶ。既定は知らせる)。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M01。目減りと多い戻りを知らせる本は 2 本、多い戻りを止める本は 2 本)・本人の決め 11-11 12:5x (ポカよけはまず分かるようにし、止めるかは会社が選ぶ)・会社の設定 subcontract_return。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R33"
    action = "purchasing.mark_completed"
    setting_key = "subcontract_return"

    def run(self, data, user):
        line = data.get("object")
        if line is None:
            return None
        short = tools.short_return_qty(line)
        if not short > 0:
            return None
        label = f"{line.purchase_order.po_no}-{line.line_no}"
        return _subcontract_return_message(self, _short_return_text(label, line.qty, short))


class SubcontractReturnOnProgress(Check):
    """R33: 協力企業に出していて終わっていない明細 (出ている数かまだ渡していない数がある明細) のある指示書の工程に、実績の入力で実績を入れたら。

    data の workorder_step は指示書の工程 (progress.services.record_progress の引数)。明細は purchasing.services.unreturned_lines
    (tools.unreturned_lines。取り消していない明細の受入の残が 0 より大きい明細) を発注番号 → 行番号の順に並べ、「・」でつなぐ。
    実績の編集 (progress.update_progress) には掛けない (受入から作った実績を直すたびに知らせが出ないようにするため)。
    訳: 協力企業に出した工程の戻りの数の食い違い (渡す前の戻り・多い戻り・目減りで完了) と、戻りを受入と実績で 2 回入れる間違いに気づかせるため (止めるか知らせるかは会社が選ぶ。既定は知らせる)。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M01。目減りと多い戻りを知らせる本は 2 本、多い戻りを止める本は 2 本)・本人の決め 11-11 12:5x (ポカよけはまず分かるようにし、止めるかは会社が選ぶ)・会社の設定 subcontract_return。
    変えてよい条件 = 会社の設定で選ぶ。既定を変える提案は Issue「決まりの提案」で。
    """

    name = "R33"
    action = "progress.record_progress"
    setting_key = "subcontract_return"

    def run(self, data, user):
        step = data.get("workorder_step")
        if step is None:
            return None
        lines = tools.unreturned_lines(step)
        if not lines:
            return None
        labels = "・".join(f"{line.purchase_order.po_no}-{line.line_no}" for line in lines)
        body = (
            f"指示書 {step.workorder.wo_no} の工程 {step.seq} {step.process.name} は、協力企業に出しています ({labels})。"
            "戻りは受入で入れると、この工程の実績になります。"
        )
        return _subcontract_return_message(self, body)


BUILTIN = (
    OverShipment(),
    OverShipmentOnUpdate(),
    OverShipmentLines(),
    OrderBelowShipped(),
    OverWorkorder(),
    OverProgress(),
    OverProgressOnUpdate(),
    OverPayment(),
    ProgressWithoutStaff(),
    BilledShipmentOnUpdate(),
    BilledShipmentOnDelete(),
    BilledShipmentOnNoteCancel(),
    ProgressInactiveStaff(),
    ProgressInactiveStaffOnUpdate(),
    LumpCompletion(),
    LumpCompletionOnReceipt(),
    DefectWithoutKind(),
    SameCustomerOrderNo(),
    SameCustomerOrderNoOnUpdate(),
    SameCustomerOrderNoOnExtraction(),
    SameCustomerOrderNoOnInbox(),
    SameCustomerOrderNoOnConditionalInbox(),
    SubcontractReturnOnReceipt(),
    SubcontractReturnOnComplete(),
    SubcontractReturnOnProgress(),
)

for _check in BUILTIN:
    register(_check)
