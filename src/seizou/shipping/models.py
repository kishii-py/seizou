"""出荷の表 (01 の 4 節)。

出荷は受注の明細を指し、1 つの明細に何回でも出荷できる (分納)。
納品書は得意先ごとに出荷をまとめた物で、数量と金額の写しを持たない。
納品書の明細は、その納品書に結ばれた出荷そのもの (R08 の「納品書の数量は出荷の数量と一致する」)。
文字数の上限は固定 (識別子 35・備考 500・コード 10)。
出荷の知らせ (EDI の 3 段目) の記録は Shipment の notice_* の 3 列。受信した注文で作った受注の出荷だけが、出荷の後に connect.services.send_shipping_notice で相手へ送られる。
書くときは shipping.services を通す。
"""

from functools import cached_property

from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.utils import timezone


class DeliveryNote(models.Model):
    """納品書。納品書番号は core の連番が付ける。"""

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

    class Meta:
        verbose_name = "納品書"
        verbose_name_plural = "納品書"
        ordering = ["-issued_on", "-id"]

    def __str__(self):
        return self.note_no

    @cached_property
    def has_unbilled_shipments(self):
        """この納品書の出荷のうち、発行の請求書に入っていない物が 1 つでもあるか (読むだけ。問い合わせ 1 回)。

        納品書の詳細の頭の緑の「請求書作成」が読む (全部請求済みの納品書には出さない。初見テスト R5 の候補 C93 と C03-1 a)。
        請求書の明細の表は出荷の逆の関係 invoice_lines の先として引き、「発行」の値は明細の外部キー invoice の先の表の
        STATUS_ISSUED から読む (billing を import しない。shipping.services.invoiced_shipments と同じ読み方)。
        取消の請求書だけに入った出荷は「入っていない」に数える (billing.services.billable_shipments と同じ数え方)。
        請求書の部品が無ければ偽。
        """
        try:
            lines = Shipment._meta.get_field("invoice_lines").related_model
        except FieldDoesNotExist:
            return False
        issued = lines._meta.get_field("invoice").related_model.STATUS_ISSUED
        return self.shipments.exclude(invoice_lines__invoice__status=issued).exists()


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
    # 出荷の知らせ (出荷を相手の seizou へ送った記録)。connect.services.send_shipping_notice が shipping.services の
    # assign_notice_number・record_notice_response で書く。
    # 番号は 1 回目に付け、送り直すときに使い回す (相手は同じ番号を 1 件にまとめる = R11)。画面に出さない。
    notice_message_id = models.UUIDField("出荷の知らせの番号", null=True, blank=True, unique=True)
    # 相手の受け口が 200 を返したときだけ入れる。空 = まだ相手に届いていない (か、送る物ではない)。
    notice_sent_at = models.DateTimeField("出荷の知らせを送った日時", null=True, blank=True)
    notice_response = models.CharField("出荷の知らせの相手の返事", max_length=500, blank=True)

    #: 詳細と小窓に出さない列 (core/views.py の hidden_detail_fields が物 1 件ずつ読む)。番号 (uuid) はいつも画面に出さない。
    #: 知らせを送った日時と相手の返事の 2 列は、受信した注文で作った受注の出荷 (相手へ出荷の知らせを送る物) だけ出す。
    #: ほかの出荷では入らない欄で、「—」の行は何の欄か迷わせる (初見テスト R5 の候補 C18)。
    #: 送る物かの判定は、送る側と同じ services._is_edi_order を使う (同じ式を 2 か所に書かない)。
    #: shipping.services が頭でこの model を読むので、services は中で import する (頭で読むと輪になる)。
    @property
    def detail_hidden_fields(self):
        hidden = ("notice_message_id",)
        if self.order_line_id is not None:
            from . import services

            if services._is_edi_order(self.order_line.order):
                return hidden
        return (*hidden, "notice_sent_at", "notice_response")

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
        「届いていない: <相手の返事>」。返事が「届きません」「断られた」で始まるときは前置きを付けない (前置きを重ねない。精査 A1 の想定外 S11)。
        """
        if self.notice_sent_at is not None:
            return f"送った {timezone.localtime(self.notice_sent_at):%Y-%m-%d %H:%M}"
        if self.notice_response:
            if self.notice_response.startswith(("届きません", "断られた")):
                return self.notice_response
            return f"届いていない: {self.notice_response}"
        return ""
