"""workorders のテンプレートの tag。読むだけで、表に書かない。

- workorder_badge: 指示書の進みの状態の印 (core/_status_badge.html。色は core.status_colors の WORKORDER_TONES。作者の決め)。
- workorder_cancel_refusal: 指示書を取り消せない理由の文 (services.cancel_refusal と同じ文。取消の確かめのカード workorders/_workorder_cancel_card.html が黄の断りで出す)。
"""

from django import template

from seizou.core.status_colors import WORKORDER_TONES

from .. import services

register = template.Library()


@register.inclusion_tag("core/_status_badge.html")
def workorder_badge(workorder, dot=False, small=False):
    """{% workorder_badge workorder %}。指示書の進みの状態の印 (作者の決め)。題の横・一覧の「状態」の列・小窓・QR を読んだ先が使う。

    字は WorkOrder.PROGRESS_LABELS (未着手・着手済・完了・取消)、色は WORKORDER_TONES (未着手 = 灰、着手済 = 青、完了 = 緑、取消 = 赤の線)。
    状態は WorkOrder.progress_state (実績から出す。工程を実績の合計付きで prefetch してあれば問い合わせをしない)。
    """
    state = workorder.progress_state()
    return {"tone": WORKORDER_TONES[state], "label": workorder.PROGRESS_LABELS[state], "dot": dot, "small": small}


@register.simple_tag
def workorder_cancel_refusal(workorder):
    """{% workorder_cancel_refusal object as refusal %}。指示書を取り消せない理由の文 (services.cancel_refusal と同じ文。取り消せるなら空の字)。"""
    return services.cancel_refusal(workorder)
