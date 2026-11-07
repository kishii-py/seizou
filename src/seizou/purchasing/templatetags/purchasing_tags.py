"""purchasing のテンプレートの tag (受入の支払の見込み)。読むだけで、表に書かない。

- payable_table: 受入の一覧の上の表 (取引先ごとの先月と今月の支払の見込み)。金額を見られない人には hidden が真。
金額の閲覧の判定は core の access.can_see_money (関数の中で import する)。
ダッシュボードのカード「今月の支払の見込み」は core の dashboard_stats() が数字のカードの並びに出す (11-03 の本体の直し)。
- live_state: 発注の一覧と詳細の 2 秒ごとの更新で、行の状態の字 (data-live) と、前の更新と字が違うか (seizou-changed を付けるか)。
- over_receipt_text・over_receipt_notices: 受入の数が発注の明細の数量を超えたときの黄の知らせの字 (発注の詳細の明細と、受入の一覧)。
- notice_rests: 発注の詳細の出荷の知らせの行ごとの、まだ受け入れていない数 (「受入を記録する」の数量)。
- next_receipt: 発注の詳細の頭の右の緑の「受入を記録する」の行き先 (まだ受け入れていない出荷の知らせのうち、明細の順で最初・知らせの古い順で最初の物)。
- purchase_order_aside: 発注の詳細の右の列 (案 3-D・8-B。N150) のカードの並び。形は orders の orders_detail の order_aside と同じ。
- purchase_order_head_chips: 発注の詳細の頭の要点の札 (N163)。形は orders の orders_detail の order_head_chips と同じ。
- purchase_order_modal_lines: 発注の小窓の明細の表に出す明細 (取り消した明細を外す。頭の札と同じ数え方)。
- reply_line_text: 発注の詳細の明細の下の黄の行に出す、相手の注文回答の明細の答え (条件付受諾の回答単価・回答数量・回答納期)。
- can_change_order・revision_state: 送った発注の変更 (中小企業共通 EDI の注文変更の方式 2) の「変更」のボタンを出すかと、今の版の送った記録と相手の答え。
- self_invoice_due_note・self_invoice_line_answer: 仕入明細の詳細の 60 日の黄の 1 行の字 (services.due_date_note) と、明細ごとの相手の答えの字 (候補 C111)。
- self_invoice_cancel_refusal: 仕入明細を取り消せない訳の文 (services.cancel_self_invoice_refusal と同じ文。取消の確かめのカードが黄で出す)。
"""

from decimal import Decimal

from django import template

from seizou.core import edi_codes
from seizou.core.templatetags.seizou_tags import comma, yen

register = template.Library()


def _user(context):
    request = context.get("request")
    return getattr(request, "user", None)


@register.simple_tag(takes_context=True)
def payable_table(context):
    """{% payable_table as payable %}。purchasing.services.payable_by_partner() の辞書に hidden (金額を隠すか) を足した物。"""
    from seizou.core import access

    from .. import services

    return {**services.payable_by_partner(), "hidden": not access.can_see_money(_user(context))}


# 2 秒ごとの更新の要求の見出し (前の更新で見せた行の状態の字を、空白で区切って並べた物)
_LIVE_HEADER = "X-Seizou-Live"


def _num_text(value) -> str:
    """数の字。末尾の 0 を落とす (Decimal("200.00") と Decimal("200") を同じ "200" に、"0.00" を "0" に)。無ければ "0"。"""
    if value is None:
        return "0"
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(dec.normalize(), "f")


def _live_seen(request) -> dict:
    """見出しの字を {鍵: 値} にする。1 つの要求で 1 回だけ作り、request に持たせる (行ごとに作り直さない)。"""
    seen = getattr(request, "_seizou_live_seen", None)
    if seen is None:
        seen = {}
        for part in request.headers.get(_LIVE_HEADER, "").split():
            key, sep, value = part.partition(":")
            if sep and key:
                seen[key] = value
        request._seizou_live_seen = seen
    return seen


@register.simple_tag(takes_context=True)
def live_state(context, obj):
    """{% live_state obj as live %}。live.token = 状態の字 (data-live)、live.changed = 前の更新と字が違うか。

    字は「鍵:値」。発注 = po-<pk>:<状態の値>:<出荷の知らせの数>:<受入の数>、発注の明細 = line-<pk>:<出荷の知らせの数>:<受入の数>。
    前の更新の字は、2 秒ごとの更新の要求の見出し X-Seizou-Live (空白で区切った字の並び) から読む。見出しが無い
    (画面を開いた時) か、同じ鍵が無い行は changed が偽。読むだけで、問い合わせをしない (数は queryset の annotate と
    明細の shipping_notice の要約を読む)。
    """
    from ..models import PurchaseOrder, PurchaseOrderLine

    if isinstance(obj, PurchaseOrder):
        key = f"po-{obj.pk}"
        value = (
            f"{obj.status}:{_num_text(getattr(obj, 'notice_count', 0))}:{_num_text(getattr(obj, 'received_total', 0))}"
        )
        # 版が 2 以上 (変更と取消) なら末尾に版を足す (2 秒ごとの取り直しで版が変わったことに気づくため。第 1 版の字は今のまま)。
        if obj.revision_no >= 2:
            value += f":r{obj.revision_no}"
    elif isinstance(obj, PurchaseOrderLine):
        key = f"line-{obj.pk}"
        value = f"{_num_text(obj.shipping_notice['count'])}:{_num_text(getattr(obj, 'received_total', 0))}"
    else:
        raise ValueError(f"live_state は発注か発注の明細だけを受けます ({type(obj).__name__} を受けた)。")
    request = context.get("request")
    seen = _live_seen(request) if request is not None else {}
    changed = key in seen and seen[key] != value
    return {"token": f"{key}:{value}", "changed": changed}


@register.simple_tag
def over_receipt_text(line):
    """{% over_receipt_text line as over %}。明細 (received_total つき) の受けすぎの知らせの字。超えていなければ空の字。

    発注の詳細の明細の表 (lines_with_received の明細) が行の下に黄の知らせを出すのに使う。問い合わせをしない。
    """
    from .. import services

    return services.over_receipt_message(line)


@register.simple_tag
def over_receipt_notices(rows):
    """{% over_receipt_notices rows as over %}。受入の一覧の行 (土台の rows) の明細のうち、受けすぎの明細の並び。

    1 つ = {"line", "message"}。集計 1 回 (purchasing.services.over_receipt_lines)。行が無ければ問い合わせをしない。
    """
    from .. import services

    ids = {row["obj"].line_id for row in rows or ()}
    if not ids:
        return []
    return [{"line": line, "message": services.over_receipt_message(line)} for line in services.over_receipt_lines(ids)]


@register.simple_tag
def notice_rests(line):
    """{% notice_rests line as rests %}。purchasing.services.notice_rests の並び。問い合わせをしない (受入の数は lines_with_received の received_total)。"""
    from .. import services

    return services.notice_rests(line)


@register.simple_tag
def next_receipt(purchase_order):
    """{% next_receipt object as receipt %}。頭の右の緑の「受入を記録する」の行き先 (UX-2 a。初見テスト R5 の候補 C93)。読むだけ。

    取消と断られた発注は None。ほかは lines_with_received (行番号の順) を回し、受入の残 (remaining_total) が 0 より大きい明細で、
    notice_rests (出荷日の古い順) の rest が 0 より大きい最初の知らせが見つかったら {"line", "rest_text"} を返す。無ければ None。
    条件は明細の表の「受入を記録する」と同じなので、表の一番上の「受入を記録する」と同じ行き先になる。権限は見ない (テンプレートが見る)。
    """
    from .. import services

    if purchase_order.status in (purchase_order.STATUS_CANCELLED, purchase_order.STATUS_REJECTED):
        return None
    for line in purchase_order.lines_with_received:
        # 取り消した明細 (明細状態コード 2) は受入の行き先にしない。
        if line.remaining_total <= 0 or line.is_cancelled:
            continue
        for entry in services.notice_rests(line):
            if entry["rest"] > 0:
                return {"line": line, "rest_text": entry["rest_text"]}
    return None


def _counted_lines(purchase_order) -> list:
    """納期と金額の合計に数える明細。取り消した明細 (明細状態コード 2) を外す。

    状態が「取消」の発注は全部の明細を数える (取消を送った発注は全部の明細が 2 になるので、外すと金額のカードと合計の札が消える。
    下書きを取り消した発注も今のまま金額を出す)。
    """
    lines = list(purchase_order.lines.all())
    if purchase_order.status == purchase_order.STATUS_CANCELLED:
        return lines
    return [line for line in lines if not line.is_cancelled]


@register.simple_tag
def purchase_order_aside(purchase_order, fields, hide_money=False):
    """{% purchase_order_aside object fields seizou.hide_money as aside %}。発注の詳細の右の列 (案 3-D・8-B。N150)。読むだけ。

    協力企業 (名前は小窓を開く字・取引先コード)・日付 (発注日・明細の一番早い納期)・金額 (明細の金額の合計。明細があり、
    金額を見られる人 = hide_money が偽のときだけ)・基本情報 (土台の項目 fields から取引先と発注日を除いた物)。
    カードと行の形は orders の orders_detail の order_aside と同じ。金額の閲覧は seizou.hide_money を読むだけ (core の access の判定を呼ばない = 金額の閲覧の記録を増やさない)。
    """
    partner = purchase_order.partner
    lines = _counted_lines(purchase_order)
    dues = sorted(line.due_date for line in lines if line.due_date)

    def label(name):
        return str(purchase_order._meta.get_field(name).verbose_name)

    def row(text, value=None, master=None, money=False):
        # money は core/detail.html の yen_if の引数 (全部の行が鍵を持つ。orders の _row と同じ形)
        return {"label": text, "value": value, "master": master, "file": None, "money": money}

    moved = (label("partner"), label("order_date"))
    cards = [
        {"title": "協力企業", "rows": [row("名前", master=partner), row("取引先コード", partner.code)]},
        {"title": "日付", "rows": [row(label("order_date"), purchase_order.order_date), row("一番早い納期", dues[0] if dues else None)]},
    ]
    if lines and not hide_money:
        cards.append({"title": "金額", "rows": [row("明細の合計", sum((line.amount for line in lines), Decimal("0")), money=True)]})
    cards.append(
        {
            "title": "基本情報",
            "rows": [field for field in fields if field["label"] not in moved],
            "help": "basic",
            "help_label": "基本情報の説明",
        }
    )
    return {"cards": cards}


@register.simple_tag
def purchase_order_head_chips(purchase_order, hide_money=False) -> list[dict]:
    """{% purchase_order_head_chips object seizou.hide_money as head_chips %}。発注の詳細の頭の要点の札 (N163)。読むだけ。

    札 = {"label", "value"} (値は字)。協力企業 (名前)・一番早い納期 (明細の納期の一番早い日。無ければ札を出さない)・
    合計 (明細の金額の合計。頭に ¥。明細があり、金額を見られる人 = hide_money が偽のときだけ)。右の列のカード (purchase_order_aside) は残す (札はその要約)。
    """
    lines = _counted_lines(purchase_order)
    dues = sorted(line.due_date for line in lines if line.due_date)
    chips = [{"label": "協力企業", "value": purchase_order.partner.name}]
    if dues:
        chips.append({"label": "一番早い納期", "value": f"{dues[0]:%Y-%m-%d}"})
    # 版が 2 以上なら「版」の札を「合計」の札の前に入れる (土台は札を 3 つまで出すので、後ろに足すと出ない。押し出された合計は右の列の「金額」のカードに出る)。
    state = revision_state(purchase_order)
    if state is not None:
        chips.append({"label": "版", "value": f"{state['label']}・{state['answer']}"})
    if lines and not hide_money:
        chips.append({"label": "合計", "value": yen(sum((line.amount for line in lines), Decimal("0")))})
    return chips


@register.simple_tag
def purchase_order_modal_lines(purchase_order) -> list:
    """{% purchase_order_modal_lines object as lines %}。発注の小窓 (purchasing/_modal.html) の明細の表に出す明細。読むだけ。

    取り消した明細 (明細状態コード 2) を外す (頭の札 purchase_order_head_chips と同じ数え方 = _counted_lines。状態が「取消」の発注は全部の明細)。
    明細は小窓の口 (PurchaseOrderModalView.get_queryset) が受入の数を付けて行番号の順に読んである (問い合わせを足さない)。
    """
    return _counted_lines(purchase_order)


@register.simple_tag
def reply_line_text(line, hide_money=False):
    """{% reply_line_text line seizou.hide_money as reply_text %}。明細の下の黄の行に出す、相手の最後の注文回答の明細の答えの字。

    条件付受諾 (CA) の明細は「相手の回答: 単価 ¥24 (注文 ¥25.5)・数量 10 個・納期 2026-11-25 (注文 2026-11-20)」の形。
    注文と違う所だけ「(注文 …)」を付ける。金額の閲覧の無い人 (hide_money が真) には単価の所を出さない。回答の納期が空なら「納期 —」。
    受託不可 (RE) の明細は「相手の回答: この明細は受けられない」。受諾 (AP) と空は空の字。読むだけで、問い合わせをしない。
    """
    if line.reply_type == edi_codes.REPLY_REJECTED:
        return "相手の回答: この明細は受けられない"
    if line.reply_type != edi_codes.REPLY_CONDITIONAL:
        return ""
    parts = []
    if not hide_money and line.reply_unit_price is not None:
        text = f"単価 {yen(line.reply_unit_price)}"
        if line.reply_unit_price != line.unit_price:
            text += f" (注文 {yen(line.unit_price)})"
        parts.append(text)
    if line.reply_qty is not None:
        text = f"数量 {comma(line.reply_qty)}{' ' + line.unit if line.unit else ''}"
        if line.reply_qty != line.qty:
            text += f" (注文 {comma(line.qty)})"
        parts.append(text)
    text = f"納期 {_date_text(line.reply_due_date)}"
    if line.reply_due_date != line.due_date:
        text += f" (注文 {_date_text(line.due_date)})"
    parts.append(text)
    return "相手の回答: " + "・".join(parts)


def _date_text(value) -> str:
    """日付の字 (YYYY-MM-DD)。空なら「—」。"""
    return f"{value:%Y-%m-%d}" if value else "—"


@register.simple_tag
def can_change_order(purchase_order):
    """{% can_change_order object as can_change %}。発注の詳細の「変更」のボタンを出すか (purchasing.services.can_change_order)。読むだけ。"""
    from .. import services

    return services.can_change_order(purchase_order)


#: 版 2 からの相手の答え (EDI の注文回答タイプコード) の字。答えが無ければ「相手の答え待ち」
_REPLY_WORDS = {"AP": "相手が受け入れた", "RE": "相手が断った"}


@register.simple_tag
def revision_state(purchase_order):
    """{% revision_state object as revision %}。版が 2 以上の発注の今の版の送った記録と相手の答え。版 1 は None。

    返す物 = {"label": 「第 n 版 (変更)」か「第 n 版 (取消)」, "kind": 「変更」か「取消」, "delivered": その版の送った注文に「受け取った」の行があるか,
    "response": その版の最後の返事, "reply": 最初の行の reply_type, "reply_note", "replied_at", "answer": 頭の札の答えの字,
    "alert": 詳細の黄色の 1 行の字 (purchasing.services.revision_alert)}。
    逆の関係 outbox_messages を読むだけ (purchasing は connect を import しない)。1 回の問い合わせ。
    """
    if purchase_order.revision_no < 2:
        return None
    outbox = getattr(purchase_order, "outbox_messages", None)
    rows = list(outbox.filter(revision_no=purchase_order.revision_no).order_by("pk")) if outbox is not None else []
    from .. import services

    kind = "取消" if purchase_order.purpose_code == "1" else "変更"
    first = rows[0] if rows else None
    reply = first.reply_type if first is not None else ""
    state = {
        "label": f"第 {purchase_order.revision_no} 版 ({kind})",
        "kind": kind,
        "delivered": any(row.response == "受け取った" for row in rows),
        "response": rows[-1].response if rows else "",
        "reply": reply,
        "reply_note": first.reply_note if first is not None else "",
        "replied_at": first.replied_at if first is not None else None,
        "answer": _REPLY_WORDS.get(reply, "相手の答え待ち"),
    }
    # 詳細の黄色の 1 行の字 (届いていない・相手が断った。無ければ空の字)
    state["alert"] = services.revision_alert(purchase_order, state)
    return state


@register.simple_tag
def self_invoice_due_note(self_invoice):
    """{% self_invoice_due_note object as due_note %}。支払期日が受領日から 60 日を超えるときの黄の 1 行の字 (services.due_date_note)。ほかは空の字。"""
    from .. import services

    return services.due_date_note(self_invoice)


@register.simple_tag
def self_invoice_line_answer(line, hide_money=False):
    """仕入明細の明細の相手の答えの字 (コードは出さない)。答えが無ければ空の字。

    承諾 (AP) は「承諾」、直した内容の承諾 (AC) は「直した内容を承諾」、提案 (UV) は「提案: 数 40 → 38・単価 ¥25.5 → ¥26」
    (違う所だけ。金額の閲覧が無い人には単価を出さない)、確認できない (RE) は「確認できない: 単価が違う (相手の字)」。
    """
    code = line.reply_type
    if not code:
        return ""
    if code == edi_codes.SI_REPLY_PROPOSED:
        parts = []
        if line.reply_qty is not None and line.reply_qty != line.qty:
            parts.append(f"数 {comma(line.qty)} → {comma(line.reply_qty)}")
        if not hide_money and line.reply_unit_price is not None and line.reply_unit_price != line.unit_price:
            parts.append(f"単価 {yen(line.unit_price)} → {yen(line.reply_unit_price)}")
        text = "提案" + (": " + "・".join(parts) if parts else "")
    elif code == edi_codes.SI_REPLY_REJECTED:
        reason = dict(edi_codes.SI_REASON_CHOICES).get(line.reply_reason_code, "")
        text = "確認できない" + (f": {reason}" if reason else "")
    else:
        text = dict(edi_codes.SI_REPLY_LINE_TYPES).get(code, code)
    if line.reply_note:
        text += f" ({line.reply_note})"
    return text


@register.simple_tag
def self_invoice_cancel_refusal(self_invoice):
    """{% self_invoice_cancel_refusal object as refusal %}。仕入明細を取り消せない訳の文 (取り消せるなら空の字)。"""
    from .. import services

    return services.cancel_self_invoice_refusal(self_invoice)
