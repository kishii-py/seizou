"""中小企業共通 EDI のコードの値 (connect と purchasing が同じ字を使う 1 か所)。

purchasing と connect は互いを頭で import しないので、2 つが読むコードの組は core に置く。
値は付表 4 の字のまま持つ (大文字の 2 字。数や小文字にしない)。日本語の名前は付表 4 の字から既定の印「（デフォルト）」を除いた物。
画面には日本語の名前を出し、コードの字は出さない (コードは表と JSON にだけ持つ)。
注文回答タイプコードと注文回答明細回答タイプコードも付表 4 の値のまま持つ。ガイドブック 3.2.2 の注文回答理由識別コード (1〜4) は ver.4.3 のコード表に無いので使わない。注文回答明細回答理由コード (UN01015602。任意) は使わない。
"""

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
