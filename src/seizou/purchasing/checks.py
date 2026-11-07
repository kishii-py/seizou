"""発注のポカよけ (取適法の確かめ)。送った発注の単価を下げる・数を減らす・納期を早める・明細を取り消す変更と、送った発注の取消に、黄色の確かめを出す。
LawPriceDown・LawOrderChange は変更 (purchasing.change_purchase_order)、LawOrderCancel は取消 (purchasing.cancel_purchase_order) を見る。
LawSelfInvoicePriceDown は、相手の提案を受け入れて仕入明細の単価を下げる保存 (purchasing.accept_self_invoice_proposals。候補 C111) を見る。

mode は "warn" (知らせる) で、会社の設定の鍵を持たない (会社の設定で止めるにも通すにもしない。「このまま保存」で通す)。
法律に関わる作りは適法の側に倒し、当てはめは言い切らず、画面の字は「おそれがあります」にする。
data は入力の土台 (core.views.BaseFormView) が渡す物: object = 直す前の発注 (表の値)、lines = 変更の form の service_kwargs の明細
(既にある明細は鍵 line に表の PurchaseOrderLine。form は表を書き替えないので、line の値が前の値)。検査は読むだけで、表を書かない。
apps.py の ready が register_checks を呼んで検査の層に登録する (2 回呼んでも、同じ action の同じ name は置き換わるだけ)。
"""

from __future__ import annotations

from seizou.core.checks import Check, register
from seizou.core.templatetags.seizou_tags import yen

from .models import _qty_text

#: 単価を下げる変更の確かめ (頭の「確認してください。」は右上の黄の知らせの見出しが出すので、字に入れない)
PRICE_DOWN_TAIL = (
    "取適法の対象の取引では、発注した後の減額は、相手が合意しても違反になるおそれがあります。"
    "打ち間違いを合意した単価へ直すときは、このまま保存してください。"
)
#: 数を減らす・納期を早める・取り消す変更と、送った発注の取消の確かめ
ORDER_CHANGE_TAIL = (
    "取適法の対象の取引では、相手に責めの無い取消や変更は、相手が負った費用を負担しないと違反になるおそれがあります。"
    "相手と話して決めた後なら、このまま保存してください。"
)


def _price_text(value, unit) -> str:
    """単価の字 (例「¥1,180 / 個」。単位が空なら「/ 個」を付けない)。"""
    text = yen(value)
    return f"{text} / {unit}" if unit else text


def _kept(lines):
    """変更の明細のうち、既にある明細を指す物 (表の明細, 直した値の辞書) の並び。取り消してある明細は見ない。"""
    return [(line["line"], line) for line in lines or () if line.get("line") is not None and not line["line"].is_cancelled]


class LawPriceDown(Check):
    """送った発注の既にある明細の単価を下げる変更に、黄色の確かめを出す (止めない)。

    訳: 送った発注の単価を下げる保存に気づかせ、取適法 (2026-01-01 施行。旧下請法) の代金の減額の禁止に当たるおそれを知らせるため。打ち間違いの直しは止めない。出所 = 法令 (取適法) と決め LAW-1 (11-06 23:4x)。確かめた資料 = 法律事務所の解説と報道 (公正取引委員会の一次資料は未読)。
    変えてよい条件 = 公正取引委員会の資料で当てはめを確かめた後に、会社の決めで変える。
    """

    name = "law_price_down"
    action = "purchasing.change_purchase_order"
    mode = "warn"

    def run(self, data, user):
        texts = []
        for row, line in _kept(data.get("lines")):
            new = line.get("unit_price")
            if new is not None and new < row.unit_price:
                texts.append(f"明細 {row.line_no} の単価を {_price_text(row.unit_price, row.unit)} から {_price_text(new, row.unit)} に")
        if not texts:
            return None
        return "、".join(texts) + "下げます。" + PRICE_DOWN_TAIL


class LawOrderChange(Check):
    """送った発注の既にある明細の数を減らす・納期を早める・明細を取り消す変更に、黄色の確かめを出す (止めない)。

    納期は前も後も日付があるときだけ比べる。取り消す明細 = 直す前の発注の取り消していない明細のうち、変更の明細に無い物。
    訳: 送った発注の数を減らす・納期を早める・明細を取り消す保存に気づかせ、取適法の不当な給付内容の変更の禁止 (発注の取消を含む) に当たるおそれを知らせるため。相手と合意した変更は止めない。出所 = 法令 (取適法) と決め LAW-4 (11-07 00:0x)。確かめた資料 = 作り手の知識 (公正取引委員会の文書は未読)。
    変えてよい条件 = 公正取引委員会の資料で当てはめを確かめた後に、会社の決めで変える。
    """

    name = "law_order_change"
    action = "purchasing.change_purchase_order"
    mode = "warn"

    def run(self, data, user):
        texts = []
        kept = _kept(data.get("lines"))
        for row, line in kept:
            qty = line.get("qty")
            if qty is not None and qty < row.qty:
                texts.append(f"明細 {row.line_no} の数を {_qty_text(row.qty)} から {_qty_text(qty)} に減らします。")
            due = line.get("due_date")
            if due is not None and row.due_date is not None and due < row.due_date:
                texts.append(f"明細 {row.line_no} の納期を {row.due_date:%Y-%m-%d} から {due:%Y-%m-%d} に早めます。")
        purchase_order = data.get("object")
        if purchase_order is not None and data.get("lines") is not None:
            kept_pks = {row.pk for row, _line in kept}
            for row in purchase_order.lines.order_by("line_no"):
                if not row.is_cancelled and row.pk not in kept_pks:
                    texts.append(f"明細 {row.line_no} を取り消します。")
        if not texts:
            return None
        return "".join(texts) + ORDER_CHANGE_TAIL


class LawOrderCancel(Check):
    """送った発注の取消 (相手へ取消を送れる発注) に、黄色の確かめを出す (止めない)。

    相手がどの明細も出荷していない「送った」「相手が受け入れた」の発注 (purchasing.services.can_cancel_sent_order が真) だけに出す。
    一部を出荷済みの発注には出さない (services が「変更」の道を案内する字ですぐ断る。確かめに「このまま保存」を押させてから断らないため)。
    訳: 送った発注の数を減らす・納期を早める・明細を取り消す保存に気づかせ、取適法の不当な給付内容の変更の禁止 (発注の取消を含む) に当たるおそれを知らせるため。相手と合意した変更は止めない。出所 = 法令 (取適法) と決め LAW-4 (11-07 00:0x)。確かめた資料 = 作り手の知識 (公正取引委員会の文書は未読)。
    変えてよい条件 = 公正取引委員会の資料で当てはめを確かめた後に、会社の決めで変える。
    """

    name = "law_order_cancel"
    action = "purchasing.cancel_purchase_order"
    mode = "warn"

    def run(self, data, user):
        from . import services

        purchase_order = data.get("object")
        if purchase_order is None or not services.can_cancel_sent_order(purchase_order):
            return None
        return "送った発注を取り消します。" + ORDER_CHANGE_TAIL


class LawSelfInvoicePriceDown(Check):
    """相手の提案を受け入れて仕入明細の明細の単価を下げる保存に、黄色の確かめを出す (止めない)。数だけを減らす提案には出さない。

    data は入力の土台が渡す物: object = 仕入明細 (表の値)、line_nos = 受け入れる明細の行番号の並び。字の形は LawPriceDown と同じ。
    訳: 相手の提案を受け入れて仕入明細の単価を下げる保存に気づかせ、取適法 (2026-01-01 施行。旧下請法) の代金の減額の禁止に当たるおそれを知らせるため。打ち間違いの直しは止めない。出所 = 法令 (取適法) と決め LAW-1 (送った発注の単価を下げるときの確かめを、同じ形で仕入明細に当てた AI の案)。確かめた資料 = 法律事務所の解説と報道 (公正取引委員会の一次資料は未読)。
    変えてよい条件 = 公正取引委員会の資料で当てはめを確かめた後に、LAW-1 と一緒に変える。
    """

    name = "law_self_invoice_price_down"
    action = "purchasing.accept_self_invoice_proposals"
    mode = "warn"

    def run(self, data, user):
        self_invoice = data.get("object")
        chosen = set(data.get("line_nos") or ())
        if self_invoice is None or not chosen:
            return None
        texts = []
        for row in self_invoice.lines.order_by("line_no"):
            new = row.reply_unit_price
            if row.line_no in chosen and new is not None and new < row.unit_price:
                texts.append(f"明細 {row.line_no} の単価を {_price_text(row.unit_price, row.unit)} から {_price_text(new, row.unit)} に")
        if not texts:
            return None
        return "、".join(texts) + "下げます。" + PRICE_DOWN_TAIL


CHECKS = (LawPriceDown(), LawOrderChange(), LawOrderCancel(), LawSelfInvoicePriceDown())


def register_checks() -> None:
    """発注のポカよけを検査の層に登録する (apps.py の ready が呼ぶ)。"""
    for check in CHECKS:
        register(check)
