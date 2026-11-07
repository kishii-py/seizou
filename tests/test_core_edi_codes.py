"""会社どうしで送り合うコードの値 (core.edi_codes) が、中小企業共通 EDI の付表 4 の値と名前のとおりかを見る。

値は字のまま持つ (前の 0 を落とさない・数にしない)。名前は付表 4 の字から、既定の印と注の印を除き、括弧を半角にした物。
"""

from seizou.core import edi_codes


def test_purpose_codes():
    assert (edi_codes.PURPOSE_NEW, edi_codes.PURPOSE_CHANGE, edi_codes.PURPOSE_CANCEL) == ("9", "4", "1")
    assert dict(edi_codes.PURPOSE_CHOICES) == {"9": "新規", "4": "変更・修正 (買い手主導)", "1": "打切り"}


def test_line_status_codes():
    assert (edi_codes.LINE_NEW, edi_codes.LINE_ADDED, edi_codes.LINE_DELETED, edi_codes.LINE_CHANGED) == ("39", "1", "2", "3")
    assert dict(edi_codes.LINE_STATUS_CHOICES) == {"39": "新規", "1": "明細行追加", "2": "明細行取消", "3": "明細行変更"}


def test_line_reason_codes():
    assert (
        edi_codes.REASON_DUE,
        edi_codes.REASON_QTY,
        edi_codes.REASON_DUE_QTY,
        edi_codes.REASON_PRICE,
        edi_codes.REASON_OTHER,
    ) == ("3001", "3002", "3003", "3006", "3099")
    assert dict(edi_codes.LINE_REASON_CHOICES) == {
        "3001": "納期変更",
        "3002": "数量変更",
        "3003": "納期・数量変更",
        "3006": "金額変更",
        "3099": "その他の変更",
    }


def test_order_reply_types():
    assert (
        edi_codes.REPLY_ACCEPTED,
        edi_codes.REPLY_CONDITIONAL,
        edi_codes.REPLY_REJECTED,
        edi_codes.REPLY_PENDING,
    ) == ("AP", "CA", "RE", "AJ")
    assert [value for value, _label in edi_codes.ORDER_REPLY_TYPES] == ["AP", "CA", "RE", "AJ"]


def test_every_value_is_text():
    groups = (
        edi_codes.PURPOSE_CHOICES,
        edi_codes.LINE_STATUS_CHOICES,
        edi_codes.LINE_REASON_CHOICES,
        edi_codes.ORDER_REPLY_TYPES,
    )
    for group in groups:
        for value, label in group:
            assert isinstance(value, str) and value
            assert isinstance(label, str) and label
            assert "準拠" not in label
