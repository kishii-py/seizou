"""出荷の表 (表の設計の決め)。

出荷は受注の明細を指し、1 つの明細に何回でも出荷できる (分納)。
納品書は得意先ごとに出荷をまとめた物で、数量と金額の写しを持たない。
納品書の明細は、その納品書に結ばれた出荷そのもの (R08 の「納品書の数量は出荷の数量と一致する」)。
文字数の上限は固定 (識別子 35・備考 500・コード 10)。
出荷の金額の列は持たない。一式の明細の全額は、明細を出し終えた出荷 (finishes_line) に付く (shipping.services.amount_of_shipment)。
出荷の知らせ (EDI の 3 段目) の記録は Shipment の notice_* の 3 列。受信した注文で作った受注の出荷だけが、出荷の後に connect.services.send_shipping_notice で相手へ送られる。
書くときは shipping.services を通す。
"""

from functools import cached_property

from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.utils import timezone


class DeliveryNote(models.Model):
    """納品書。納品書番号は core の連番が付ける。keeps_report_copy は帳票の写し (core.ReportCopy。R34) を持つ印で、詳細に「発行の時の写し」の枠を出す。"""

    STATUS_ISSUED = "issued"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_ISSUED, "発行"),
        (STATUS_CANCELLED, "取消"),
    ]

    note_no = models.CharField("納品書番号", max_length=35, unique=True)
    customer = models.ForeignKey(
        "masters.Partner",
        verbose_name="得意先",
        on_delete=models.PROTECT,
        related_name="delivery_notes",
    )
    issued_on = models.DateField("発行日")
    status = models.CharField(
        "状態", max_length=10, choices=STATUS_CHOICES, default=STATUS_ISSUED
    )

    #: 変更の履歴 (core.history。作者の決めの伝票の「納品書」) を残し、詳細に「履歴」のタブを出す
    keeps_history = True
    keeps_report_copy = True

    class Meta:
        verbose_name = "納品書"
        verbose_name_plural = "納品書"
        ordering = ["-issued_on", "-id"]

    def __str__(self):
        return self.note_no

    @cached_property
    def has_unbilled_shipments(self):
        """この納品書の出荷のうち、発行の請求書に入っていない物が 1 つでもあるか (読むだけ。問い合わせ 1 回)。

        納品書の詳細の頭の緑の「請求書作成」が読む (全部請求済みの納品書には出さない。作者の決め)。
        請求書の明細の表は出荷の逆の関係 invoice_lines の先として引き、「発行」の値は明細の外部キー invoice の先の表の
        STATUS_ISSUED から読む (billing を import しない。shipping.services.invoiced_shipments と同じ読み方)。
        取消の請求書だけに入った出荷は「入っていない」に数える (billing.services.billable_shipments と同じ数え方)。
        受注の明細に「請求しない」の印がある出荷は数えない (billing.services.billable_shipments と同じ。作者の決め)。
        請求書の外で受け取った入金 (取消でない) のある出荷は数えない (billing.services.billable_shipments と同じ。作者の決め)。
        入金の表は出荷の逆の関係 payments の先として引き、「記録」の値はその表の STATUS_RECORDED から読む。
        請求書の部品が無ければ偽。
        """
        try:
            lines = Shipment._meta.get_field("invoice_lines").related_model
        except FieldDoesNotExist:
            return False
        issued = lines._meta.get_field("invoice").related_model.STATUS_ISSUED
        shipments = self.shipments.exclude(invoice_lines__invoice__status=issued).exclude(order_line__no_bill=True)
        try:
            payments = Shipment._meta.get_field("payments").related_model
        except FieldDoesNotExist:
            payments = None
        if payments is not None:
            shipments = shipments.exclude(payments__status=payments.STATUS_RECORDED)
        return shipments.exists()


class Shipment(models.Model):
    """出荷。出荷番号は core の連番が付ける。出荷済みの数は orders.services の shipped_qty が集計する。"""

    shipment_no = models.CharField("出荷番号", max_length=35, unique=True)
    order_line = models.ForeignKey(
        "orders.OrderLine",
        verbose_name="受注の明細",
        on_delete=models.PROTECT,
        related_name="shipments",
    )
    qty = models.DecimalField("数量", max_digits=12, decimal_places=2)
    shipped_at = models.DateField("出荷日")
    delivery_note = models.ForeignKey(
        DeliveryNote,
        verbose_name="納品書",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="shipments",
    )
    note = models.CharField("備考", max_length=500, blank=True)
    # この出荷で、受注の明細の数量を初めて出し終えたか (作者の決め)。書くのは shipping.services だけ (出荷の時と、出荷を直した時)。
    # 一式 (単価の単位が式) の明細は、この印の付いた出荷だけが一式の全額を持つ (shipping.services.amount_of_shipment)。部品の単位の明細にも同じ決まりで付く (金額には使わない)。
    finishes_line = models.BooleanField("明細を出し終えた出荷", default=False, editable=False)
    # 出荷の知らせ (出荷を相手の seizou へ送った記録)。connect.services.send_shipping_notice が shipping.services の
    # assign_notice_number・record_notice_response で書く。
    # 番号は 1 回目に付け、送り直すときに使い回す (相手は同じ番号を 1 件にまとめる = R11)。画面に出さない。
    notice_message_id = models.UUIDField("出荷の知らせの番号", null=True, blank=True, unique=True)
    # 相手の受け口が 200 を返したときだけ入れる。空 = まだ相手に届いていない (か、送る物ではない)。
    notice_sent_at = models.DateTimeField("出荷の知らせを送った日時", null=True, blank=True)
    notice_response = models.CharField("出荷の知らせの相手の返事", max_length=500, blank=True)

    #: 詳細と小窓に出さない列 (core/views/ の hidden_detail_fields が物 1 件ずつ読む)。番号 (uuid) はいつも画面に出さない。
    #: 明細を出し終えた出荷の印 (finishes_line) も、金額を数えるための列なのでいつも出さない。
    #: 知らせを送った日時と相手の返事の 2 列は、受信した注文で作った受注の出荷 (相手へ出荷の知らせを送る物) だけ出す。
    #: ほかの出荷では入らない欄で、「—」の行は何の欄か迷わせる (作者の決め)。
    #: 送る物かの判定は、送る側と同じ services._is_edi_order を使う (同じ式を 2 か所に書かない)。
    #: shipping.services が頭でこの model を読むので、services は中で import する (頭で読むと輪になる)。
    @property
    def detail_hidden_fields(self):
        hidden = ("notice_message_id", "finishes_line")
        if self.order_line_id is not None:
            from . import services

            if services._is_edi_order(self.order_line.order):
                return hidden
        return (*hidden, "notice_sent_at", "notice_response")

    #: 変更の履歴 (core.history。作者の決めの伝票の「出荷」) を残し、詳細に「履歴」のタブを出す。
    #: 出荷の知らせの番号 (uuid)・送った日時・相手の返事は core が残さない (番号の uuid と、名前が _sent_at・_response で終わる列)
    keeps_history = True

    class Meta:
        verbose_name = "出荷"
        verbose_name_plural = "出荷"
        ordering = ["-shipped_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gt=0),
                name="shipping_shipment_qty_positive",
            ),
        ]

    def __str__(self):
        return self.shipment_no

    def notice_state(self):
        """出荷の詳細の「出荷の知らせ」の節に出す字。送る物でない出荷は空の字 (テンプレートが節を出さない)。

        送れた (相手の受け口が 200) なら「送った <日時>」、送れていなくて返事があれば
        「届いていない: <相手の返事>」。返事が「届きません」「断られた」で始まるときは前置きを付けない (前置きを重ねない。作者の決め)。
        """
        if self.notice_sent_at is not None:
            return f"送った {timezone.localtime(self.notice_sent_at):%Y-%m-%d %H:%M}"
        if self.notice_response:
            if self.notice_response.startswith(("届きません", "断られた")):
                return self.notice_response
            return f"届いていない: {self.notice_response}"
        return ""
