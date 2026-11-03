"""つながりと受信箱の表 (01 の 4 節)。

文字数の上限は masters と同じ (識別子 35・名称 100・備考 500・コード 10)。
鍵の値は表に置かない。つながりが持つのは、鍵の値を入れる環境変数の名前だけ。
ほかの部品と画面は、この表を書くときに connect.services を通す。
"""

import os
from decimal import Decimal, InvalidOperation

from django.db import models
from django.utils import timezone


class Connection(models.Model):
    """つながり。取引先ごとの、相手の受け口と鍵の置き場の名前と向き。

    同じ取引先に、送る向きと受ける向きの 2 行を持ってよい。
    """

    DIRECTION_SEND = "send"
    DIRECTION_RECEIVE = "receive"
    DIRECTION_CHOICES = [
        (DIRECTION_SEND, "送る"),
        (DIRECTION_RECEIVE, "受ける"),
    ]

    partner = models.ForeignKey(
        "masters.Partner",
        verbose_name="取引先",
        on_delete=models.PROTECT,
        related_name="connections",
    )
    # 相手の受け口。送るときに使う。受ける向きのときは空でよい。
    url = models.URLField("受け口の URL", max_length=200, blank=True)
    # 環境変数の名前だけを持つ。値は会社のプロジェクトの .env に置く。
    secret_env = models.CharField("鍵の置き場の名前", max_length=100)
    direction = models.CharField("向き", max_length=10, choices=DIRECTION_CHOICES)

    class Meta:
        verbose_name = "つながり"
        verbose_name_plural = "つながり"
        ordering = ["partner", "direction"]

    def __str__(self):
        return f"{self.partner.name} ({self.get_direction_display()})"

    def has_secret(self):
        """鍵の値が環境変数に置いてあるか。値そのものは返さない。"""
        return bool(os.environ.get(self.secret_env))

    def secret_state(self):
        """つながりの一覧の「鍵の設定」に出す字 (済み / まだ)。"""
        return "済み" if self.has_secret() else "まだ"


class Inbox(models.Model):
    """受信箱。受け口が受けた注文のデータを 1 件 1 行で持つ。

    番号の一意の制約で、同じ番号のデータは 1 行だけにする (R11)。
    人が受け入れるまで受注にはならない (R10)。
    """

    KIND_ORDER = "order"
    KIND_DELIVERY = "delivery"
    KIND_CHOICES = [
        (KIND_ORDER, "注文"),
        (KIND_DELIVERY, "納品"),
    ]

    STATUS_RECEIVED = "received"
    STATUS_ACCEPTED = "accepted"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_RECEIVED, "届いた"),
        (STATUS_ACCEPTED, "受け入れた"),
        (STATUS_REJECTED, "断った"),
    ]

    message_id = models.UUIDField("番号", unique=True)
    partner = models.ForeignKey(
        "masters.Partner",
        verbose_name="取引先",
        on_delete=models.PROTECT,
        related_name="inbox_messages",
    )
    kind = models.CharField("種類", max_length=10, choices=KIND_CHOICES)
    received_at = models.DateTimeField("届いた日時", default=timezone.now)
    payload = models.JSONField("中身")
    status = models.CharField(
        "状態", max_length=10, choices=STATUS_CHOICES, default=STATUS_RECEIVED
    )
    # 受け入れて作った受注。1 対 1 なので、2 回受け入れても受注との結びは 1 つ (R11)。
    order = models.OneToOneField(
        "orders.Order",
        verbose_name="受け入れて作った受注",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="inbox_message",
    )
    reason = models.CharField("断った理由", max_length=500, blank=True)
    # 注文回答 (承認か断りの結果を、注文を送ってきた相手の受け口へ返した記録)。connect.services.send_reply が書く。
    # 番号は 1 回目に付け、送り直すときに使い回す (相手は同じ番号を 1 件にまとめる = R11)。画面に出さない。
    reply_message_id = models.UUIDField("注文回答の番号", null=True, blank=True)
    # 相手の受け口が 200 を返したときだけ入れる。空 = まだ相手に届いていない。
    reply_sent_at = models.DateTimeField("注文回答を送った日時", null=True, blank=True)
    reply_response = models.CharField("注文回答の相手の返事", max_length=500, blank=True)

    class Meta:
        verbose_name = "届いた注文"
        verbose_name_plural = "届いた注文"
        ordering = ["-received_at", "-id"]

    def __str__(self):
        received = timezone.localtime(self.received_at)
        return f"{self.get_kind_display()} {self.partner.name} {received:%Y-%m-%d %H:%M}"

    def summary(self):
        """一覧に出す中身の要約 (例「T-100 ×10 ほか 2 行」)。payload の lines の形が違えば空の字。"""
        lines = self.payload.get("lines") if isinstance(self.payload, dict) else None
        if not isinstance(lines, list) or not lines or not isinstance(lines[0], dict):
            return ""
        part_no = lines[0].get("part_no")
        qty = lines[0].get("qty")
        if not isinstance(part_no, str) or not part_no:
            return ""
        if isinstance(qty, bool) or not isinstance(qty, (str, int)):
            return ""
        try:
            qty_text = f"{Decimal(str(qty)).normalize():f}"
        except InvalidOperation:
            return ""
        text = f"{part_no} ×{qty_text}"
        if len(lines) > 1:
            text += f" ほか {len(lines) - 1} 行"
        return text

    def reply_state(self):
        """詳細に出す注文回答の字。「届いた」なら空の字 (テンプレートが薄い「—」を出す)。

        送れた (相手の受け口が 200) なら「送った <日時>」、承認か断りの後で届いていなければ
        「届いていない: <相手の返事>」。返事が「届きません」「断られた」で始まるときは前置きを付けない (前置きを重ねない。精査 A1 の想定外 S11)。
        """
        if self.status == self.STATUS_RECEIVED:
            return ""
        if self.reply_sent_at is not None:
            return f"送った {timezone.localtime(self.reply_sent_at):%Y-%m-%d %H:%M}"
        if self.reply_response.startswith(("届きません", "断られた")):
            return self.reply_response
        return f"届いていない: {self.reply_response}"


class Outbox(models.Model):
    """送った注文。connect.services.send_order が 1 回送るごとに 1 行を作る。

    番号は見出しの webhook-id と同じ uuid4。同じ発注を送り直すときは 1 回目の番号を使い回す
    (相手の受け口は同じ番号を 1 件にまとめる = R11。2 つのタブから送り直しても相手に 2 行できない)。
    相手の返事は日本語の短い文で持つ。
    鍵の値と本文は持たない。
    """

    message_id = models.UUIDField("番号", db_index=True)
    connection = models.ForeignKey(
        Connection,
        verbose_name="つながり",
        on_delete=models.PROTECT,
        related_name="outbox_messages",
    )
    purchase_order = models.ForeignKey(
        "purchasing.PurchaseOrder",
        verbose_name="元の発注",
        on_delete=models.PROTECT,
        related_name="outbox_messages",
    )
    sent_at = models.DateTimeField("送った日時", default=timezone.now)
    response = models.CharField("相手の返事", max_length=500, blank=True)

    class Meta:
        verbose_name = "送った注文"
        verbose_name_plural = "送った注文"
        ordering = ["-sent_at", "-id"]

    def __str__(self):
        sent = timezone.localtime(self.sent_at)
        # 相手が受け取った行は、誰が受け取ったかを添える (発注の詳細の「送った記録」のタブの行の字。初見テスト 3 回目)。
        if self.response == "受け取った":
            return f"{sent:%Y-%m-%d %H:%M} {self.connection.partner.name} が受け取った"
        return f"{sent:%Y-%m-%d %H:%M} {self.response}"


class Acceptance(models.Model):
    """検収 (EDI の 4 段目)。相手 (注文を送ってきた得意先の seizou) が受け入れた数の知らせを、受注の明細ごとに 1 件 1 行で持つ。

    番号の一意の制約で同じ番号は 1 行 (R11 の考え)。届いた注文の承認 (accept) とは別の言葉。
    書くのは connect.services.record_acceptance だけ。
    """

    message_id = models.UUIDField("検収の番号", unique=True)
    inbox = models.ForeignKey(
        Inbox,
        verbose_name="元の届いた注文",
        on_delete=models.PROTECT,
        related_name="acceptances",
    )
    order_line = models.ForeignKey(
        "orders.OrderLine",
        verbose_name="受注の明細",
        on_delete=models.PROTECT,
        related_name="acceptances",
    )
    buyer_order_no = models.CharField("相手の発注番号", max_length=35, blank=True)
    buyer_line_no = models.PositiveIntegerField("相手の行番号")
    accepted_on = models.DateField("検収日")
    qty = models.DecimalField("検収の数", max_digits=12, decimal_places=2)
    received_at = models.DateTimeField("届いた日時", default=timezone.now)

    class Meta:
        verbose_name = "検収"
        verbose_name_plural = "検収"
        ordering = ["-accepted_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gt=0),
                name="connect_acceptance_qty_positive",
            ),
        ]

    def __str__(self):
        return f"{self.accepted_on:%Y-%m-%d} {self.order_line} {_qty_text(self.qty)}"


class ShippingNotice(models.Model):
    """出荷の知らせ (EDI の 3 段目)。相手 (注文を受けた協力企業の seizou) が出荷した日と数の知らせを、発注の明細ごとに出荷 1 件 1 行で持つ。

    番号の一意の制約と、発注の明細 × 相手の出荷番号 の一意の制約で、同じ知らせは 1 行 (R11 の考え)。
    受入 (purchasing.Receipt) は人が記録する別の物で、この行は受入の初期値になるだけ。
    書くのは connect.services.record_shipping_notice だけ。
    """

    message_id = models.UUIDField("出荷の知らせの番号", unique=True)
    line = models.ForeignKey(
        "purchasing.PurchaseOrderLine",
        verbose_name="発注の明細",
        on_delete=models.PROTECT,
        related_name="shipping_notices",
    )
    shipment_no = models.CharField("相手の出荷番号", max_length=35)
    supplier_order_no = models.CharField("相手の受注番号", max_length=35, blank=True)
    shipped_at = models.DateField("出荷日")
    qty = models.DecimalField("出荷の数", max_digits=12, decimal_places=2)
    received_at = models.DateTimeField("届いた日時", default=timezone.now)

    class Meta:
        verbose_name = "出荷の知らせ"
        verbose_name_plural = "出荷の知らせ"
        # 発注の明細の枡に古い順で並べる。
        ordering = ["shipped_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gt=0),
                name="connect_shippingnotice_qty_positive",
            ),
            models.UniqueConstraint(
                fields=["line", "shipment_no"],
                name="connect_shippingnotice_line_shipment_no_unique",
            ),
        ]

    def __str__(self):
        return f"{self.shipped_at:%Y-%m-%d} {self.line} ×{self.qty_text()}"

    def qty_text(self):
        """出荷の数の字 (末尾の 0 を落とす)。発注の詳細の枡と、受入の初期値の GET の鍵の値に使う。"""
        return _qty_text(self.qty)


def _qty_text(value) -> str:
    """数量の字。末尾の 0 を落とす (10.00 → 10、2.50 → 2.5)。purchasing の models と同じ (connect は purchasing を頭で import しない)。"""
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(dec.normalize(), "f") if dec != dec.to_integral_value() else str(dec.quantize(Decimal(1)))
