"""発注の表 (01 の 4 節)。協力企業管理の最小の形。

文字数の上限は固定 (識別子 35・備考 500・コード 10)。
金額は purchasing.services が数量 × 単価 (単価の単位が式なら単価のまま) を取引先の小計の計算方法で丸めて書く (DB で計算しない)。
受入 (Receipt) の金額も、purchasing.services が受け入れた数 × 発注の単価を同じ方法で丸めて書く (一式の明細は全部を受け入れた回で一式の全額、ほかの回は 0。決め PRICE-1c)。検収は受入の後に connect.services.send_acceptance が相手へ送る (列は Receipt の acceptance_*)。支給材は会期後。書くときは purchasing.services を通す。
受入の数と残は列に持たず、purchasing.services の集計 (received_qty・remaining_qty・annotate_received) で出す。
注文回答のタイプ (reply_type) は中小企業共通 EDI の注文回答タイプコードと注文回答明細回答タイプコードの項目を元にした値 (core.edi_codes)。
注文履歴番号・注文書目的コード・明細状態コード・明細状態理由コードは中小企業共通 EDI の項目を元にした (注文変更の方式 2。値は core.edi_codes)。取り消した明細 (明細状態コード 2) は行を残し、受入の候補と数と金額の合計に入れない。
"""

from decimal import Decimal
from functools import cached_property

from django.conf import settings
from django.db import models
from django.utils import timezone

from seizou.core import edi_codes
from seizou.masters.services import PRICE_BASIS_CHOICES, PRICE_BASIS_UNIT, price_basis_text


class PurchaseOrder(models.Model):
    """発注。1 件に明細が複数。発注番号は core の連番が付ける。"""

    STATUS_DRAFT = "draft"
    STATUS_SENT = "sent"
    STATUS_ON_HOLD = "on_hold"
    STATUS_CONDITIONAL = "conditional"
    STATUS_ACCEPTED = "accepted"
    STATUS_PARTIAL = "partial"
    STATUS_REJECTED = "rejected"
    STATUS_DELIVERED = "delivered"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "下書き"),
        (STATUS_SENT, "送った"),
        (STATUS_ON_HOLD, "相手が保留した"),
        (STATUS_CONDITIONAL, "相手が条件付で受けた"),
        (STATUS_ACCEPTED, "相手が受け入れた"),
        (STATUS_PARTIAL, "一部納品"),
        (STATUS_REJECTED, "断られた"),
        (STATUS_DELIVERED, "納品された"),
        (STATUS_CANCELLED, "取消"),
    ]

    po_no = models.CharField("発注番号", max_length=35, unique=True)
    partner = models.ForeignKey(
        "masters.Partner",
        verbose_name="取引先",
        on_delete=models.PROTECT,
        related_name="purchase_orders",
    )
    order_date = models.DateField("発注日")
    status = models.CharField(
        "状態", max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT
    )
    note = models.CharField("備考", max_length=500, blank=True)
    # 注文回答 (相手の答え)。connect の受け口が purchasing.services の mark_accepted・mark_rejected・mark_conditional・mark_on_hold で書く。
    # 日時はこちらに届いて当てた時刻 (相手の時計は使わない)。
    replied_at = models.DateTimeField("注文回答の日時", null=True, blank=True)
    reply_note = models.CharField("断りの理由", max_length=500, blank=True)
    # 当てた注文回答の番号。同じ番号が 2 回届いたら 2 回目は何もしない (R11 の考え。DB の一意の制約も)。
    reply_message_id = models.UUIDField("注文回答の番号", null=True, blank=True, unique=True)
    # 第 1 版に最後に当てた注文回答の EDI のコード。connect の受け口が purchasing.services の mark_* で書く。版 2 からの答えは connect の送った注文の行 (Outbox.reply_type)。
    reply_type = models.CharField("注文回答のタイプ", max_length=2, choices=edi_codes.ORDER_REPLY_TYPES, blank=True)
    # 今の版の番号 (注文履歴番号) と目的 (注文書目的コード)。変更と取消を相手へ送るたびに版を 1 上げる (EDI の注文変更の方式 2)。
    revision_no = models.PositiveIntegerField("注文履歴番号", default=1)
    purpose_code = models.CharField(
        "注文書目的コード", max_length=2, choices=edi_codes.PURPOSE_CHOICES, default=edi_codes.PURPOSE_NEW
    )

    # 詳細と小窓に出さない列 (core/views.py の hidden_detail_fields が読む)。uuid は画面に出さない。
    detail_hidden_fields = ("reply_message_id",)

    class Meta:
        verbose_name = "発注"
        verbose_name_plural = "発注"
        ordering = ["-order_date", "-id"]

    def __str__(self):
        return self.po_no

    @property
    def receipts(self):
        """この発注の受入 (読むだけ。詳細の「受入」のタブが .all() と .count() を読む)。"""
        return Receipt.objects.filter(line__purchase_order=self).select_related("line__purchase_order", "line__item")

    @property
    def lines_with_received(self):
        """明細に受入の数 (received_total) と受入の残 (remaining_total) を足した物 (読むだけ。詳細の明細の表が回す)。"""
        from . import services

        return services.annotate_received(self.lines.select_related("item").order_by("line_no", "pk"))


class PurchaseOrderLine(models.Model):
    """発注の明細。金額は purchasing.services が数量 × 単価 (単価の単位が式なら単価のまま) を丸めて書く。"""

    purchase_order = models.ForeignKey(
        PurchaseOrder,
        verbose_name="発注",
        on_delete=models.CASCADE,
        related_name="lines",
    )
    line_no = models.PositiveIntegerField("行番号")
    item = models.ForeignKey(
        "masters.Item",
        verbose_name="部品",
        on_delete=models.PROTECT,
        related_name="purchase_order_lines",
    )
    qty = models.DecimalField("数量", max_digits=12, decimal_places=2)
    unit = models.CharField("単位", max_length=10, blank=True)
    unit_price = models.DecimalField("単価", max_digits=14, decimal_places=2)
    # 単価の単位 (決め PRICE-1b)。部品の単位 (1 つあたり) か式 (一式。金額に数量を掛けない)。書くのは purchasing.services だけ。
    # 数量の単位の列 (unit) とは別。式は数量の単位でないので unit に入れない (数量は実際の個数のまま)。
    price_basis = models.CharField("単価の単位", max_length=10, choices=PRICE_BASIS_CHOICES, default=PRICE_BASIS_UNIT)
    amount = models.DecimalField("金額", max_digits=14, decimal_places=2)
    due_date = models.DateField("納期", null=True, blank=True)
    note = models.CharField("備考", max_length=500, blank=True)
    # 相手の最後の注文回答の、この明細の答え (EDI の注文回答明細回答タイプコードと、条件付受諾の回答の値)。
    # connect の受け口が purchasing.services の mark_conditional・record_line_replies で書き、ほかの答えが届くと空に戻す。
    reply_type = models.CharField("回答のタイプ", max_length=2, choices=edi_codes.ORDER_REPLY_LINE_TYPES, blank=True)
    reply_unit_price = models.DecimalField("回答単価", max_digits=14, decimal_places=2, null=True, blank=True)
    reply_qty = models.DecimalField("回答数量", max_digits=12, decimal_places=2, null=True, blank=True)
    reply_due_date = models.DateField("回答納期", null=True, blank=True)
    # 今の版でこの明細に何をしたか (明細状態コード) と、変えた訳 (明細状態理由コード)。取り消した明細は行を残して 2 にする。
    line_status_code = models.CharField(
        "明細状態コード", max_length=2, choices=edi_codes.LINE_STATUS_CHOICES, default=edi_codes.LINE_NEW
    )
    line_status_reason_code = models.CharField(
        "明細状態理由コード", max_length=4, choices=edi_codes.LINE_REASON_CHOICES, blank=True, default=""
    )

    class Meta:
        verbose_name = "発注の明細"
        verbose_name_plural = "発注の明細"
        ordering = ["purchase_order", "line_no"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gt=0),
                name="purchasing_purchaseorderline_qty_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gt=0),
                name="purchasing_purchaseorderline_unit_price_positive",
            ),
            models.UniqueConstraint(
                fields=["purchase_order", "line_no"],
                name="purchasing_purchaseorderline_line_no_unique",
            ),
        ]

    def __str__(self):
        # 受注の明細と同じ形 (候補の口が str(obj) を返す)。発注と部品を読むので、引く側は select_related を付ける。
        text = f"{self.purchase_order.po_no}-{self.line_no} {self.item.part_no} {self.item.name}"
        # 集計 (services.annotate_received) を通し、受入の残が 0 より大きい明細だけ、取引先の名前と受入の残を足す
        # (受入の入力の候補の口の字。初見テスト R5 の候補 C64)。取引先も読むので、候補の口は purchase_order__partner も select_related する。
        # 残りは必ず末尾 (受入の画面の JS purchasing/receipt-form.js が「 残り n」を読んで数量の欄の下に写す)。
        # 0 以下の明細は今の字のまま: 受入の一覧の受けすぎの知らせ (services.over_receipt_lines。集計を通すが取引先を読まない) が
        # 同じ字を使い、受けすぎの明細の受入の残はいつもマイナスなので、知らせの字が変わらず問い合わせも増えない。集計の無い明細も今の字のまま。
        remaining = getattr(self, "remaining_total", None)
        if remaining is None or remaining <= 0:
            return text
        return f"{text} / {self.purchase_order.partner.name} 残り {_qty_text(remaining)}"

    @property
    def price_unit_text(self):
        """単価の後ろに添える字 (読むだけ)。「¥250 / 個」の「個」、「¥50,000 / 式」の「式」。
        式なら「式」、ほかはこの明細の数量の単位、空なら「個」(決め PRICE-1b。関数は masters の price_basis_text)。"""
        return price_basis_text(self.price_basis, self.unit)

    @property
    def is_cancelled(self):
        """取り消した明細か (明細状態コード 2 = 明細行取消)。行は残し、受入の候補と数と金額の合計に入れない。"""
        return self.line_status_code == edi_codes.LINE_DELETED

    @property
    def detail_link_target(self):
        """詳細の基本の表でこの明細の値を押した時の行き先 (読むだけ)。親の発注。
        明細には詳細の画面が無いので、core の field_items が getattr で読み、発注の詳細への link にする (初見テスト R5 の V69。候補 C80)。"""
        return self.purchase_order

    @cached_property
    def shipping_notice(self):
        """出荷の知らせの要約 (読むだけ)。発注の詳細の明細の表が読む。"""
        from . import services

        return services.shipping_notice_summary(self)


class Receipt(models.Model):
    """受入。協力企業から受け入れた物の記録。発注の明細ごとに何回でも入れられる (分納)。

    金額は purchasing.services が 受け入れた数 × 明細の単価 を発注の取引先の方法で丸めて書く (DB で計算しない。一式の明細は全部を受け入れた回で一式の全額、ほかの回は 0。決め PRICE-1c)。
    列の名前を amount にするのは、金額の閲覧の無い人に土台が「—」を出すため (access.is_money_name)。
    """

    line = models.ForeignKey(
        PurchaseOrderLine,
        verbose_name="発注の明細",
        on_delete=models.PROTECT,
        related_name="receipts",
    )
    received_on = models.DateField("受入日")
    qty = models.DecimalField("数量", max_digits=12, decimal_places=2)
    amount = models.DecimalField("金額", max_digits=14, decimal_places=2)
    note = models.CharField("備考", max_length=500, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="入れた人",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="receipts",
    )
    # 検収 (受入を相手の seizou へ送った記録)。connect.services.send_acceptance が purchasing.services の
    # assign_acceptance_number・record_acceptance_response で書く。
    # 番号は 1 回目に付け、送り直すときに使い回す (相手は同じ番号を 1 件にまとめる = R11)。画面に出さない。
    acceptance_message_id = models.UUIDField("検収の番号", null=True, blank=True, unique=True)
    # 相手の受け口が 200 を返したときだけ入れる。空 = まだ相手に届いていない (か、送る物ではない)。
    acceptance_sent_at = models.DateTimeField("検収を送った日時", null=True, blank=True)
    acceptance_response = models.CharField("検収の相手の返事", max_length=500, blank=True)

    #: 詳細と小窓に出さない列 (core/views.py の hidden_detail_fields が物 1 件ずつ読む)。番号 (uuid) はいつも画面に出さない。
    #: 検収を送った日時と相手の返事の 2 列は、seizou どうしで送った発注の受入 (相手へ検収を送る物) だけ出す。
    #: 送った発注で検収がまだ無い受入は「—」のまま出す (決め 11-05 09:3x の「まだ無い」)。
    #: 送っていない発注の受入では入らない欄で、「—」の行は何の欄か迷わせる (初見テスト R5 の候補 C18)。
    #: 送った発注かの判定は、送る側と同じ services._is_sent_order を使う (同じ式を 2 か所に書かない)。
    #: purchasing.services が頭でこの model を読むので、services は中で import する (頭で読むと輪になる)。
    @property
    def detail_hidden_fields(self):
        hidden = ("acceptance_message_id",)
        if self.line_id is not None:
            from . import services

            if services._is_sent_order(self.line.purchase_order):
                return hidden
        return (*hidden, "acceptance_sent_at", "acceptance_response")

    class Meta:
        verbose_name = "受入"
        verbose_name_plural = "受入"
        ordering = ("-received_on", "-pk")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gt=0),
                name="purchasing_receipt_qty_positive",
            ),
        ]

    def __str__(self):
        line = self.line
        return f"{self.received_on:%Y-%m-%d} {line.purchase_order.po_no}-{line.line_no} {line.item.part_no} {_qty_text(self.qty)}"

    def acceptance_state(self):
        """発注の詳細の「検収」の節に出す字。送る物でない受入は空の字 (テンプレートが薄い「—」を出す)。

        送れた (相手の受け口が 200) なら「送った <日時>」、送れていなくて返事があれば
        「届いていない: <相手の返事>」。返事が「届きません」「断られた」で始まるときは前置きを付けない (前置きを重ねない。精査 A1 の想定外 S11)。
        """
        if self.acceptance_sent_at is not None:
            return f"送った {timezone.localtime(self.acceptance_sent_at):%Y-%m-%d %H:%M}"
        if self.acceptance_response:
            if self.acceptance_response.startswith(("届きません", "断られた")):
                return self.acceptance_response
            return f"届いていない: {self.acceptance_response}"
        return ""


def _qty_text(value) -> str:
    """数量の字。末尾の 0 を落とす (10.00 → 10、2.50 → 2.5)。"""
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(dec.normalize(), "f") if dec != dec.to_integral_value() else str(dec.quantize(Decimal(1)))
