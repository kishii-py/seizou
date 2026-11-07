"""状態の色の表 (案 4-C・8-A。N152)。状態の値 → 意味の色を 1 か所に持つ。

印 (template tag status_badge と core/_status_badge.html) がこの表を読む。土台の詳細の見出しの印・一覧の状態の列の印・
最初の画面の最近の受注の印が使う。意味の色は 5 つで 1 組 (決め 11-05 16:2x。N164)。名前は daisyUI の印の色の名前と同じ字:
- ghost   = 灰 (作成・下書き・まだ)
- info    = 青 (進行中 = 受注・発行・送った・受信した・読み取った・相手が受け入れた)
- warning = 橙 (注意 = 一部出荷・一部納品・相手が条件付で受けた・相手が保留した・遅れ・未入金・一部入金)
- success = 緑 (済み = 出荷済・納品された・入金済み・入金の記録・確認済み・直した・受信した注文の受け入れた・受注にした)
- error   = 赤の線 (止まった = 取消・断った・断られた・捨てた。塗らずに線と字だけ)
表に無い値は FALLBACK (primary = 今の主色のまま)。class の丸ごとの字は core/_status_badge.html が色ごとに書く
(R19。Tailwind はテンプレートの字だけを読み、.py の字は CSS に入らない)。

鍵は状態の値の字 (STATUS_TONES)。部品をまたいで同じ字はふつう同じ意味 (issued = 発行・cancelled = 取消・draft = 作成と下書き・
sent = 送った・rejected = 断った)。部品で意味が分かれる字は "<app>.<値>" の鍵 (APP_TONES) に置き、tone_for がそちらを先に読む
(accepted: 発注の「相手が受け入れた」は進行中 = 青、受信した注文の「受け入れた」は終わり = 緑)。
部品の model に状態の値を足したら、ここにも 1 行足す (tests/test_core_status_colors.py が、表に無い値を見つけると落ちる)。
状態 (status) の列でない状態の色も同じ 5 つから選ぶ: 請求書の入金の状態は PAYMENT_TONES (billing の tag payment_badge が読む)、
指示書の進みの状態は WORKORDER_TONES (workorders の tag workorder_badge が読む。初見テスト R5 の候補 C27)。
"""

#: 意味の色の名前 (core/_status_badge.html の枝の字と同じ)
TONES = ("ghost", "info", "warning", "success", "error")

#: 表に無い値の色 (今の主色のまま)
FALLBACK = "primary"

#: 状態の値 → 意味の色 (注釈は値を持つ部品と画面の字)
STATUS_TONES = {
    # 灰 = 作成・下書き・まだ
    "draft": "ghost",  # purchasing 作成・feedback 下書き
    # 青 = 進行中 (受注・発行・送った・受信した・読み取った・相手が受け入れた)
    "open": "info",  # orders 受注
    "issued": "info",  # workorders・shipping・billing 発行
    "sent": "info",  # purchasing・feedback 送った
    "received": "info",  # connect 届いた
    "extracted": "info",  # intake 読み取った
    "accepted": "info",  # purchasing 相手が受け入れた (connect は APP_TONES)
    # 橙 = 注意 (一部出荷・一部納品・相手が条件付で受けた・相手が保留した。発注元に人の手が要る)
    "partial": "warning",  # orders 一部出荷・purchasing 一部納品
    "conditional": "warning",  # purchasing 相手が条件付で受けた
    "on_hold": "warning",  # purchasing 相手が保留した
    # 緑 = 済み (出荷済・納品された・入金の記録・確認済み・直した)
    "shipped": "success",  # orders 出荷済
    "delivered": "success",  # purchasing 納品された
    "recorded": "success",  # billing 入金の記録
    "confirmed": "success",  # feedback 確認済み
    "fixed": "success",  # feedback 直した
    "ordered": "success",  # intake 受注にした
    "inbox": "success",  # intake 受信した注文へ回した
    # 赤の線 = 止まった (取消・断った・捨てた)
    "dismissed": "error",  # feedback 見送った (GitHub で対応しない・重複として閉じた。N322)
    "cancelled": "error",  # orders・purchasing・shipping・workorders・billing 取消
    "rejected": "error",  # purchasing 断られた・connect 断った
    "discarded": "error",  # intake 捨てた
}

#: 部品で意味が分かれる字 ("<app>.<値>" → 意味の色)。tone_for が STATUS_TONES より先に読む
APP_TONES = {
    "connect.accepted": "success",  # 受信した注文 受け入れた (受注になった = 終わりの状態。本体の選び 11-05 09:0x)
}

#: 請求書の入金の状態 (billing の Invoice.payment_status の値。表の列でなく、取消でない入金の合計から出す) → 意味の色。
#: 請求書の印 (billing の tag payment_badge) が読む。取消の請求書は状態の色 (STATUS_TONES の cancelled = 赤の線)。N164
PAYMENT_TONES = {
    "unpaid": "warning",  # 未入金
    "partial": "warning",  # 一部入金
    "paid": "success",  # 入金済み
}

#: 指示書の進みの状態 (workorders の WorkOrder.progress_state の値。表の列でなく、工程の実績から出す) → 意味の色。
#: 指示書の印 (workorders の tag workorder_badge) が読む。取消は状態の色と同じ赤の線。C27
WORKORDER_TONES = {
    "not_started": "ghost",  # 未着手 (まだ)
    "started": "info",  # 着手済 (進行中)
    "done": "success",  # 完了 (済み)
    "cancelled": "error",  # 取消 (止まった)
}


def tone_for(value, app_label=None) -> str:
    """状態の値の意味の色 (TONES の 1 つ)。app_label があれば "<app>.<値>" (APP_TONES) を先に読む。表に無い値と None は FALLBACK。"""
    if app_label:
        tone = APP_TONES.get(f"{app_label}.{value}")
        if tone is not None:
            return tone
    return STATUS_TONES.get(value, FALLBACK)
