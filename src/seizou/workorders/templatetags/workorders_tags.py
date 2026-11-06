"""workorders のテンプレートの tag。読むだけで、表に書かない。

- workorder_badge: 指示書の進みの状態の印 (core/_status_badge.html。色は core.status_colors の WORKORDER_TONES。初見テスト R5 の候補 C27)。
"""

from django import template

from seizou.core.status_colors import WORKORDER_TONES

register = template.Library()


@register.inclusion_tag("core/_status_badge.html")
def workorder_badge(workorder, dot=False, small=False):
    """{% workorder_badge workorder %}。指示書の進みの状態の印 (C27-1 = a)。題の横・一覧の「状態」の列・小窓・QR を読んだ先が使う。

    字は WorkOrder.PROGRESS_LABELS (未着手・着手済・完了・取消)、色は WORKORDER_TONES (未着手 = 灰、着手済 = 青、完了 = 緑、取消 = 赤の線)。
    状態は WorkOrder.progress_state (実績から出す。工程を実績の合計付きで prefetch してあれば問い合わせをしない)。
    """
    state = workorder.progress_state()
    return {"tone": WORKORDER_TONES[state], "label": workorder.PROGRESS_LABELS[state], "dot": dot, "small": small}
