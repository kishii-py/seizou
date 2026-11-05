"""状態の色の表 (案 4-C・8-A。N152)。状態の値 → 意味の色を 1 か所に持つ。

印 (template tag status_badge と core/_status_badge.html) がこの表を読む。土台の詳細の見出しの印・一覧の状態の列の印・
最初の画面の最近の受注の印が使う。意味の色の名前は daisyUI の印の色の名前と同じ字:
- ghost   = 灰 (作成・下書き)
- info    = 青 (発行・送った・受信した・受注)
- warning = 黄 (着手・受け入れた・一部)
- success = 緑 (完了・出荷済・納品された・入金の記録・確認済み・直した・受信した注文の受け入れた)
- outline = 灰の線 (取消・断った・捨てた)
表に無い値は FALLBACK (primary = 今の主色のまま)。class の丸ごとの字は core/_status_badge.html が色ごとに書く
(R19。Tailwind はテンプレートの字だけを読み、.py の字は CSS に入らない)。

鍵は状態の値の字 (STATUS_TONES)。部品をまたいで同じ字はふつう同じ意味 (issued = 発行・cancelled = 取消・draft = 作成と下書き・
sent = 送った・rejected = 断った)。部品で意味が分かれる字は "<app>.<値>" の鍵 (APP_TONES) に置き、tone_for がそちらを先に読む
(accepted: 発注の「相手が受け入れた」は途中 = 黄、受信した注文の「受け入れた」は終わり = 緑)。
部品の model に状態の値を足したら、ここにも 1 行足す (tests/test_core_status_colors.py が、表に無い値を見つけると落ちる)。
"""

#: 意味の色の名前 (core/_status_badge.html の枝の字と同じ)
TONES = ("ghost", "info", "warning", "success", "outline")

#: 表に無い値の色 (今の主色のまま)
FALLBACK = "primary"

#: 状態の値 → 意味の色 (注釈は値を持つ部品と画面の字)
STATUS_TONES = {
    # 灰 = 作成・下書き
    "draft": "ghost",  # purchasing 作成・feedback 下書き
    # 青 = 発行・送った・受信した
    "open": "info",  # orders 受注
    "issued": "info",  # workorders・shipping・billing 発行
    "sent": "info",  # purchasing・feedback 送った
    "received": "info",  # connect 届いた
    "extracted": "info",  # intake 読み取った
    # 黄 = 着手・受け入れた・一部
    "accepted": "warning",  # purchasing 相手が受け入れた (connect は APP_TONES)
    "partial": "warning",  # orders 一部出荷
    # 緑 = 完了・出荷済・入金済・確認済み
    "shipped": "success",  # orders 出荷済
    "delivered": "success",  # purchasing 納品された
    "recorded": "success",  # billing 入金の記録
    "confirmed": "success",  # feedback 確認済み
    "fixed": "success",  # feedback 直した
    "ordered": "success",  # intake 受注にした
    "inbox": "success",  # intake 受信した注文へ回した
    # 灰の線 = 取消・断った
    "cancelled": "outline",  # orders・purchasing・shipping・workorders・billing 取消
    "rejected": "outline",  # purchasing 断られた・connect 断った
    "discarded": "outline",  # intake 捨てた
}

#: 部品で意味が分かれる字 ("<app>.<値>" → 意味の色)。tone_for が STATUS_TONES より先に読む
APP_TONES = {
    "connect.accepted": "success",  # 受信した注文 受け入れた (受注になった = 終わりの状態。本体の選び 11-05 09:0x)
}


def tone_for(value, app_label=None) -> str:
    """状態の値の意味の色 (TONES の 1 つ)。app_label があれば "<app>.<値>" (APP_TONES) を先に読む。表に無い値と None は FALLBACK。"""
    if app_label:
        tone = APP_TONES.get(f"{app_label}.{value}")
        if tone is not None:
            return tone
    return STATUS_TONES.get(value, FALLBACK)
