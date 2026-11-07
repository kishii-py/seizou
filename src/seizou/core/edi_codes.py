"""会社どうしで送り合う JSON と表に持つコードの値。中小企業共通 EDI (ver.4.3) の付表 4 識別コード定義表の項目を元にした。

値は EDI のコードの値をそのまま持つ (将来 EDI の基盤に乗せるときに読み替えなくてよいように)。画面には日本語の名前を出す。
名前は付表 4 の字から、既定の印「（デフォルト）」と注の印「※」を除き、括弧を半角にした物。
使わない値と訳: 注文書目的コードの 19 変更 (売り手依頼)・6 確定 (作品に当たる流れが無い)。明細状態コードの 37 明細行打切り
(出した明細の残りを止めるのは 3 明細行変更と 3002 数量変更で数を出荷済みの数に直す形にした。受ける側が数を直す変更と同じ形で受け、
出した分の受入・検収・請求はそのまま続くため)。明細状態理由コードの 3004 分納指示・3005 仕様変更 (作品の変更は明細の部品と分納を変えない)。
発注 (purchasing) と送り合い (connect) は互いを頭で import しないので、両方が読める土台 (core) に置く。
注文回答タイプコードと注文回答明細回答タイプコードも付表 4 の値のまま持つ。ガイドブック 3.2.2 の注文回答理由識別コード (1〜4) は ver.4.3 のコード表に無いので使わない。注文回答明細回答理由コード (UN01015602。任意) は使わない。
"""

#: 注文書目的コード (UN01006524。UNCL 1225)
PURPOSE_NEW = "9"  # 新規
PURPOSE_CHANGE = "4"  # 変更・修正 (買い手主導)
PURPOSE_CANCEL = "1"  # 打切り
PURPOSE_CHOICES = ((PURPOSE_NEW, "新規"), (PURPOSE_CHANGE, "変更・修正 (買い手主導)"), (PURPOSE_CANCEL, "打切り"))
#: 注文明細状態コード (UN01006605。UNCL 1229)
LINE_NEW = "39"  # 新規
LINE_ADDED = "1"  # 明細行追加
LINE_DELETED = "2"  # 明細行取消
LINE_CHANGED = "3"  # 明細行変更
LINE_STATUS_CHOICES = ((LINE_NEW, "新規"), (LINE_ADDED, "明細行追加"), (LINE_DELETED, "明細行取消"), (LINE_CHANGED, "明細行変更"))
#: 注文明細状態理由コード (UN01006606)
REASON_DUE = "3001"  # 納期変更
REASON_QTY = "3002"  # 数量変更
REASON_DUE_QTY = "3003"  # 納期・数量変更
REASON_PRICE = "3006"  # 金額変更
REASON_OTHER = "3099"  # その他の変更 (変更内容は明細注釈 = 備考)
LINE_REASON_CHOICES = (
    (REASON_DUE, "納期変更"), (REASON_QTY, "数量変更"), (REASON_DUE_QTY, "納期・数量変更"),
    (REASON_PRICE, "金額変更"), (REASON_OTHER, "その他の変更"),
)
#: 注文回答タイプコード (UN01015497。注文回答のヘッダ。付表 4 の注文回答書ヘッダの値)
REPLY_ACCEPTED = "AP"  # 受諾回答
REPLY_CONDITIONAL = "CA"  # 条件付受諾回答
REPLY_REJECTED = "RE"  # 辞退回答
REPLY_PENDING = "AJ"  # 保留
ORDER_REPLY_TYPES = (
    (REPLY_ACCEPTED, "受諾回答"),
    (REPLY_CONDITIONAL, "条件付受諾回答"),
    (REPLY_REJECTED, "辞退回答"),
    (REPLY_PENDING, "保留"),
)
#: 注文回答明細回答タイプコード (UN01015601。注文回答の明細。付表 4 の注文回答書明細行の値。保留 AJ は明細に無い)
ORDER_REPLY_LINE_TYPES = (
    (REPLY_ACCEPTED, "明細行受諾"),
    (REPLY_CONDITIONAL, "明細行条件付き受諾"),
    (REPLY_REJECTED, "明細行受託不可"),
)
