"""billing のテンプレートの tag。読むだけで、表に書かない。

- invoice_cancel_refusal: 請求書を取り消せない理由の文 (services.cancel_refusal と同じ文。取消の確かめの画面が黄の知らせで出す)。
- invoice_reissue_plan: 請求書の作り直しの中身 (services.reissue_plan と同じ物。作り直しの画面が取り消す請求書と入る出荷を出す)。
- payment_badge: 請求書の入金の状態の印 (core/_status_badge.html。色は core.status_colors の PAYMENT_TONES。開発の記録 docs/nights/20261105/N164.md)。
- invoice_head_chips: 請求書の詳細の頭の要点の札「合計」「残」。形は orders の orders_detail の order_head_chips と同じ。
  取消の請求書と金額の閲覧の無い人には出さない (作者の決め)。
- invoice_changed_note: 請求した後に出荷の数量が変わった明細の黄色の 1 行の字 (services.changed_lines。請求書の詳細が読む。依頼書 N403b)。
"""

from django import template

from seizou.core.status_colors import PAYMENT_TONES
from seizou.core.templatetags.seizou_tags import comma, yen

register = template.Library()


@register.simple_tag
def invoice_cancel_refusal(invoice, hide_money=False):
    """{% invoice_cancel_refusal object seizou.hide_money as refusal %}。取り消せない理由の文 (取り消せるなら空の字)。

    hide_money は seizou.hide_money (金額の閲覧の無い人で真。log の会社では読んだ時に記録に 1 行)。
    """
    from .. import services

    return services.cancel_refusal(invoice, hide_money=bool(hide_money))


@register.simple_tag
def invoice_reissue_plan(invoice, hide_money=False):
    """{% invoice_reissue_plan object seizou.hide_money as plan %}。作り直しの中身 (取り消す請求書・入る出荷・作り直せない理由の文)。

    services.reissue_invoice と同じ reissue_plan を読む (画面を開いた時は、取り消す請求書の出荷がまだ請求済みなので、合わせた並びをここで出す)。
    """
    from .. import services

    return services.reissue_plan(invoice, hide_money=bool(hide_money))


@register.inclusion_tag("core/_status_badge.html")
def payment_badge(invoice, dot=False, small=False):
    """{% payment_badge invoice %}。請求書の入金の状態の印 (決め 11-05 16:2x。開発の記録 docs/nights/20261105/N164.md)。発行の請求書だけに使う (取消は呼ぶ側が status_badge)。

    字は入金の状態の字 (未入金・一部入金・入金済み)、色は core.status_colors の PAYMENT_TONES (未入金と一部入金 = 橙、入金済み = 緑)。
    入金の状態は Invoice.payment_status (取消でない入金の合計。一覧の「残のある物だけ」= views._unpaid_only と同じ数え方)。
    """
    state = invoice.payment_status()
    return {"tone": PAYMENT_TONES[state], "label": invoice.PAYMENT_LABELS[state], "dot": dot, "small": small}


@register.simple_tag
def invoice_head_chips(invoice, hide_money=False) -> list[dict]:
    """{% invoice_head_chips object seizou.hide_money as head_chips %}。請求書の詳細の頭の要点の札。読むだけ。

    札 = {"label", "value"} (値は字)。「合計」(請求書の合計。名は model の項目の verbose_name) と「残」(合計 − 取消でない入金の合計 =
    Invoice.balance。名は入金の枠の字と同じ) の 2 つで、値は受注の「合計」の札と同じ yen の字 (頭に ¥。マイナスの残は「-¥1,000」)。
    取消の請求書と、金額を見られない人 (hide_money が真) には空の並び (札を出さない)。残がマイナスでも札に色を付けない。
    請求書の詳細 billing/invoice_detail.html の block heading がこの形で置いてから block.super を呼び、土台の core/detail.html が
    head_chips を読んで題の横 (入金の状態の印と「?」の後) に並べる (受注の order_head_chips と同じ形)。
    要点の札は受注と発注 (決め 11-05 16:2x。開発の記録 docs/nights/20261105/N163.md) と出荷 (作者の決め) だけだったのを、決め 11-06 22:2x (札「合計」「残」を
    出す) で請求書にも広げた (作者の決めの前半。上の基本情報に入金の額が無く、phone では入金の枠が窓の外に残るため)。
    入金は Invoice.balance が self.payments.all() を読むので、詳細の get_queryset が prefetch した物を使う。
    """
    if invoice.status == invoice.STATUS_CANCELLED or hide_money:
        return []
    return [
        {"label": invoice._meta.get_field("total").verbose_name, "value": yen(invoice.total)},
        {"label": "残", "value": yen(invoice.balance())},
    ]


@register.simple_tag
def invoice_changed_note(invoice) -> str:
    """{% invoice_changed_note object as changed_note %}。請求した後に出荷の数量が変わった明細の黄色の 1 行の字 (読むだけ)。無ければ空の字。

    services.changed_lines(invoice) を 1 回だけ引く (明細のタブの行ごとには引かない)。並びは changed_lines のまま (明細の pk の順)。
    出荷の行は「S-000031 (請求 50・今 48)」、一式の行は「受注 SO-… の n 行目の一式 (請求 300・今 280)」。数は comma の字。
    字は「請求した後に出荷の数量が変わった明細があります: <並び (「・」でつなぐ)>。請求書も直すときは、取り消して作り直してください。」。
    金額は書かない (金額の閲覧の無い人にも同じ字)。取消の請求書は空の字 (changed_lines が空を返す)。
    """
    from .. import services

    parts = []
    for line, billed, now in services.changed_lines(invoice):
        if line.shipment_id is not None:
            name = line.shipment.shipment_no
        else:
            name = f"受注 {line.order_line.order.order_no} の {line.order_line.line_no} 行目の一式"
        parts.append(f"{name} (請求 {comma(billed)}・今 {comma(now)})")
    if not parts:
        return ""
    return f"請求した後に出荷の数量が変わった明細があります: {'・'.join(parts)}。請求書も直すときは、取り消して作り直してください。"
