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
- reply_line_text: 発注の詳細の明細の下の黄の行に出す、相手の注文回答の明細の答え (条件付受諾の回答単価・回答数量・回答納期)。
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
        if line.remaining_total <= 0:
            continue
        for entry in services.notice_rests(line):
            if entry["rest"] > 0:
                return {"line": line, "rest_text": entry["rest_text"]}
    return None


@register.simple_tag
def purchase_order_aside(purchase_order, fields, hide_money=False):
    """{% purchase_order_aside object fields seizou.hide_money as aside %}。発注の詳細の右の列 (案 3-D・8-B。N150)。読むだけ。

    協力企業 (名前は小窓を開く字・取引先コード)・日付 (発注日・明細の一番早い納期)・金額 (明細の金額の合計。明細があり、
    金額を見られる人 = hide_money が偽のときだけ)・基本情報 (土台の項目 fields から取引先と発注日を除いた物)。
    カードと行の形は orders の orders_detail の order_aside と同じ。金額の閲覧は seizou.hide_money を読むだけ (core の access の判定を呼ばない = 金額の閲覧の記録を増やさない)。
    """
    partner = purchase_order.partner
    lines = list(purchase_order.lines.all())
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
    lines = list(purchase_order.lines.all())
    dues = sorted(line.due_date for line in lines if line.due_date)
    chips = [{"label": "協力企業", "value": purchase_order.partner.name}]
    if dues:
        chips.append({"label": "一番早い納期", "value": f"{dues[0]:%Y-%m-%d}"})
    if lines and not hide_money:
        chips.append({"label": "合計", "value": yen(sum((line.amount for line in lines), Decimal("0")))})
    return chips


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
