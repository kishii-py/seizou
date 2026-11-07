"""協力企業への発注書の帳票の context を作る読むだけの関数。表に書かない。

発注書は、発注の項目 (発注番号・発注日・備考) と明細 (品番・品名・工程・数量・単位・単価・金額・納期) と、取引先の支払の条件の字、
受領場所の既定の 1 文を出す。金額は明細の写し (発注の明細の amount) のまま、合計は明細の金額を足しただけ (ここで丸めない)。
税は扱わない (発注に税の列が無いため。帳票には「金額の合計 (税抜)」と出す)。会社は納品書と同じ鍵 (名前・住所・電話・ロゴ) だけを渡す
(振込先と登録番号は渡さないので、帳票の土台が出さない)。
取り消した明細 (明細状態コード 2) は出さない。発注そのものが取消なら全部の明細を出す (発注の詳細の頭の合計の札と同じ数え方)。

訳: 協力企業への発注の内容を、seizou を使わない相手にも紙か PDF で渡せるようにするため。取適法 (2026-01-01 施行) は発注の内容を
書面か電磁的方法で示させる (確かめた資料は作業場の中に無いので、条文の当てはめは未確認)。受領場所は直送の取引で会社の住所が
誤った明示になるので、共通事項を指す 1 文を既定にする。
出所 = 本人の決め 11-07 23:4x (作る) とエンタープライズ化の調べ 第一弾の候補 13 の直し方の直し (d)(h)。
変えてよい条件 = 本人が決め直したとき、または受領場所と支払期日を発注の正式な列で持つ形 (purchasing の migration。次の版で) にしたとき。
画面と帳票の字では法令の当てはめを言い切らない。
"""

from __future__ import annotations

from decimal import Decimal

from seizou.core.services import company

#: 発注書の帳票のテンプレート (core/report_base.html を継ぐ)
ORDER_SHEET_TEMPLATE = "purchasing/purchaseorder.html"

#: 受領場所の既定の字。発注に受領場所の列が無いので、取引の共通事項を指す 1 文にする (会社の住所で埋めると直送の取引で誤った明示になる)
DELIVERY_PLACE_DEFAULT = "別に定めた取引の共通事項による場所"

#: 納期の空の明細があるときの知らせ (行の番号を「・」でつなぐ)
NO_DUE_DATE_NOTICE = "納期の決まっていない明細があります (行 {lines})。納期を決めて相手に伝えてください。"

#: 取引先に支払の条件が無いときの知らせ
NO_PAYMENT_TERMS_NOTICE = "取引先に支払の条件 (締日・支払の月・支払日) が登録されていません。取引先を直してから刷ってください。"

#: 締日が空の取引先の締日の字 (billing.services.closing_period と同じく月末で締める)
MONTH_END_TEXT = "月末"


def payment_terms_text(partner) -> str:
    """取引先の支払の条件の字「締日 月末・支払日 翌月末」(読むだけ)。支払期日が出ない取引先なら空の字。

    締日は Partner.get_closing_day_display (締日が空なら「月末」= billing.services.closing_period と同じ約束)、
    支払日は Partner.get_payment_day_display (支払の月を含めた字)。支払期日が出るかは billing.services.payment_due_date と同じ分かれ
    (支払の月が当日なら出る。ほかは支払の月と支払日の両方があるときだけ出る)。
    """
    month = partner.payment_month
    if month != partner.PAYMENT_SAME_DAY and (not month or partner.payment_day is None):
        return ""
    closing = partner.get_closing_day_display() or MONTH_END_TEXT
    return f"締日 {closing}・支払日 {partner.get_payment_day_display()}"


def _sheet_lines(order) -> list:
    """発注書に出す明細 (行番号の順)。取り消した明細を外す。発注が取消なら全部。"""
    rows = list(order.lines.select_related("item", "process").order_by("line_no", "pk"))
    if order.status == order.STATUS_CANCELLED:
        return rows
    return [row for row in rows if not row.is_cancelled]


def purchase_order_context(order) -> dict:
    """発注書 1 枚の帳票の context。

    company = 発注元の {"name", "address", "phone", "logo"} (振込先と登録番号は渡さない)、order = {"number", "date", "note"}、
    partner = 宛名の取引先、lines = 明細ごとの {"line_no", "item_code", "item_name", "process" (工程の名前。空なら空の字), "quantity", "unit",
    "unit_price", "price_unit" (明細の price_unit_text), "amount", "due_date"}、total = 明細の金額の合計 (税抜)、
    payment_terms = 取引先の支払の条件の字 (payment_terms_text。出ないなら空の字)、delivery_place = 受領場所の既定の字、
    notices = 知らせの字の並び (納期の空の明細・支払の条件の空)、cancelled = 状態が取消なら真。
    """
    partner = order.partner
    lines = []
    total = Decimal("0")
    missing_due = []
    for row in _sheet_lines(order):
        lines.append(
            {
                "line_no": row.line_no,
                "item_code": row.item.part_no,
                "item_name": row.item.name,
                "process": row.process.name if row.process_id else "",
                "quantity": row.qty,
                "unit": row.unit,
                "unit_price": row.unit_price,
                "price_unit": row.price_unit_text,
                "amount": row.amount,
                "due_date": row.due_date,
            }
        )
        total += row.amount
        if row.due_date is None:
            missing_due.append(str(row.line_no))
    payment_terms = payment_terms_text(partner)
    notices = []
    if missing_due:
        notices.append(NO_DUE_DATE_NOTICE.format(lines="・".join(missing_due)))
    if not payment_terms:
        notices.append(NO_PAYMENT_TERMS_NOTICE)
    row = company()
    return {
        "company": {"name": row.name, "address": row.address, "phone": row.phone, "logo": row.logo},
        "order": {"number": order.po_no, "date": order.order_date, "note": order.note},
        "partner": partner,
        "lines": lines,
        "total": total,
        "payment_terms": payment_terms,
        "delivery_place": DELIVERY_PLACE_DEFAULT,
        "notices": notices,
        "cancelled": order.status == order.STATUS_CANCELLED,
    }
