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

    class Meta:
        verbose_name = "受信箱"
        verbose_name_plural = "受信箱"
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


class Outbox(models.Model):
    """送った注文。connect.services.send_order が 1 回送るごとに 1 行を作る。

    番号は見出しの webhook-id と同じ uuid4。相手の返事は日本語の短い文で持つ。
    鍵の値と本文は持たない。
    """

    message_id = models.UUIDField("番号", unique=True)
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
        return f"{sent:%Y-%m-%d %H:%M} {self.response}"
