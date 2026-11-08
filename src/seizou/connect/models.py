"""つながりと受信箱の表 (表の設計の決め)。

文字数の上限は masters と同じ (識別子 35・名称 100・備考 500・コード 10)。
鍵の値は表に置かない。つながりが持つのは、鍵の値を入れる環境変数の名前だけ。
ほかの部品と画面は、この表を書くときに connect.services を通す。
"""

import os
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import models
from django.utils import timezone

from seizou.core import edi_codes
from seizou.masters.services import PRICE_BASIS_CHOICES, PRICE_BASIS_UNIT, price_basis_text


class Connection(models.Model):
    """つながり。取引先ごとの、相手の受け口と鍵の置き場の名前と向き。

    同じ取引先に、送る向きと受ける向きの 2 行を持ってよい。
    contact_email は自社の連絡先のメールアドレス。この取引先から会話が届いたときの知らせの宛先に使う (受ける向きの行の値を先に見て、空なら送る向きの行の値)。
    項目は中小企業共通 EDI の連絡先／電子メール (連絡先クラスの電子メールアドレス = 国連 CEFACT 共通辞書 UN01005858。注文の発注者メールアドレス・受注者メールアドレス) を元にした。今は取引先へ送らない (送る JSON に入れない)。
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
    # 254 字はメールアドレスの長さの上限 (Django の EmailField の既定)。connect の字数の決まり (識別子 35・名称 100・備考 500) の外の形。EDI は字数を決めない
    contact_email = models.EmailField("自社の連絡先のメールアドレス", max_length=254, blank=True)

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
    入口は 2 つ。直接 (受け口 = services.receive。取引先は署名で決まる = R12) と、読み取り (見張るフォルダの注文書 = intake の extract_to_inbox。取引先は差出人の名前から当て、当たらなければ空 = 差出人の確認待ち)。
    読み取りの行は、相手の seizou とつながっていないので、注文回答と出荷の知らせを送らない。
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
        (STATUS_RECEIVED, "未処理"),
        (STATUS_ACCEPTED, "受け入れた"),
        (STATUS_REJECTED, "断った"),
    ]

    SOURCE_DIRECT = "direct"
    SOURCE_INTAKE = "intake"
    SOURCE_CHOICES = [
        # 表示名は取引先の一覧とダッシュボードの図と受注の流れの添え字 (core.dashboard.INLETS) と同じ字 (作者の決め)。
        (SOURCE_DIRECT, "seizou で直接"),
        (SOURCE_INTAKE, "読み取り"),
    ]

    message_id = models.UUIDField("番号", unique=True)
    # 受け口から届いた行はいつも入る (R12)。読み取りの行で差出人が当たらないときは空 (差出人の確認待ち。services.choose_sender で選ぶ)。
    partner = models.ForeignKey(
        "masters.Partner",
        verbose_name="取引先",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="inbox_messages",
    )
    kind = models.CharField("種類", max_length=10, choices=KIND_CHOICES)
    source = models.CharField("入口", max_length=10, choices=SOURCE_CHOICES, default=SOURCE_DIRECT)
    received_at = models.DateTimeField("受信日時", default=timezone.now)
    payload = models.JSONField("中身")
    # 読み取りの行の元の注文書の読み取り (ファイルと読み取った中身)。直接の行は空。
    extraction = models.ForeignKey(
        "intake.Extraction",
        verbose_name="元の読み取り",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="inbox_messages",
    )
    # 読み取りの行の、注文書から読んだ差出人の会社名 (確認待ちの表示と、選ぶときの手がかり)。
    sender_name = models.CharField("読み取った差出人", max_length=100, blank=True)
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
    # services.accept が書く。この列より前に承認した行は空。
    accepted_at = models.DateTimeField("承認した日時", null=True, blank=True)
    reason = models.CharField("断った理由", max_length=500, blank=True)
    # 注文回答 (承認か断りの結果を、注文を送ってきた相手の受け口へ返した記録)。connect.services.send_reply が書く。
    # 番号は 1 回目に付け、送り直すときに使い回す (相手は同じ番号を 1 件にまとめる = R11)。画面に出さない。
    reply_message_id = models.UUIDField("注文回答の番号", null=True, blank=True)
    # 相手の受け口が 200 を返したときだけ入れる。空 = まだ相手に届いていない。
    reply_sent_at = models.DateTimeField("注文回答を送った日時", null=True, blank=True)
    reply_response = models.CharField("注文回答の相手の返事", max_length=500, blank=True)
    # 注文回答のタイプ (EDI の注文回答タイプコードの値のまま。core.edi_codes)。最後に答えた物。保留 (AJ) の間は状態が「未処理」のまま。
    reply_type = models.CharField("注文回答のタイプ", max_length=2, choices=edi_codes.ORDER_REPLY_TYPES, blank=True)
    # 条件付受諾 (CA) で相手へ返した明細の答え (送ったとおりの形 = 注文回答の JSON の lines)。ほかの答えは空の並び。送り直しもこの値を送る。
    reply_lines = models.JSONField("注文回答の明細", default=list, blank=True)
    # 注文の版 (中小企業共通 EDI の注文変更の方式 2)。変更と取消は新しい知らせとして届き、上書きせず版を重ねる。
    # 変更の版の行は受注 (order) を持たず、revises で第 1 版の行を指す (受注は第 1 版の行の order = 1 対 1)。
    revision_no = models.PositiveIntegerField("注文履歴番号", default=1)
    purpose_code = models.CharField(
        "注文書目的コード", max_length=2, choices=edi_codes.PURPOSE_CHOICES, default=edi_codes.PURPOSE_NEW
    )
    revises = models.ForeignKey(
        "self",
        verbose_name="元の注文 (第 1 版)",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="revisions",
    )

    #: 変更の履歴 (core.history。作者の決め) を残す。受け入れ・断り・保留・差出人の選び直しの前と後が詳細の「履歴」の節に出る
    keeps_history = True
    #: 変更の履歴に残さない列。中身は送り合った記録 (Exchange) に受けたとおりに残り、受けた後に変わらないため
    history_skip = ("payload",)

    class Meta:
        verbose_name = "受信した注文"
        verbose_name_plural = "受信した注文"
        ordering = ["-received_at", "-id"]

    def __str__(self):
        received = timezone.localtime(self.received_at)
        name = self.partner.name if self.partner_id else "差出人の確認待ち"
        return f"{self.get_kind_display()} {name} {received:%Y-%m-%d %H:%M}"

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

    def buyer_order_no(self):
        """詳細と受け入れの画面に出す相手の発注番号 (payload の order_no)。payload の形が違うか、字でなければ空の字。"""
        value = self.payload.get("order_no") if isinstance(self.payload, dict) else None
        return value if isinstance(value, str) else ""

    def buyer_order_date(self):
        """詳細と受け入れの画面と受注の詳細に出す相手の発注日 (payload の order_date)。読めなければ None。

        読み方は受け入れ (services._payload_date) と同じ date.fromisoformat。_payload_date は読めない値で ValueError を投げるので呼ばず、
        表示で例外を出さないよう、None・空の字・字でない値・日付として読めない字は None にする。
        """
        value = self.payload.get("order_date") if isinstance(self.payload, dict) else None
        if not isinstance(value, str) or not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    def reply_state(self):
        """詳細に出す注文回答の字。「未処理」なら空の字 (テンプレートが薄い「—」を出す)。保留 (AJ) を答えた行は「未処理」でも出す。

        送れた (相手の受け口が 200) なら「送った <日時>」、承認か断りの後で届いていなければ
        「届いていない: <相手の返事>」。返事が「届きません」「断られた」で始まるときは前置きを付けない (前置きを重ねない。作者の決め)。
        読み取りの行 (入口 = 読み取り) は相手へ送らないので、返事の欄の字をそのまま出す。
        """
        if self.status == self.STATUS_RECEIVED and self.reply_type != edi_codes.REPLY_PENDING:
            return ""
        if self.source == self.SOURCE_INTAKE:
            return self.reply_response
        if self.reply_sent_at is not None:
            return f"送った {timezone.localtime(self.reply_sent_at):%Y-%m-%d %H:%M}"
        if self.reply_response.startswith(("届きません", "断られた")):
            return self.reply_response
        return f"届いていない: {self.reply_response}"

    def current_revision_no(self):
        """受注が今どの版の注文に合っているか。出荷の知らせ (send_shipping_notice) と受注の詳細の検収の印 (orders.services.acceptance_summary) が読む。

        第 1 版の行 (revises が空) で呼ぶと、自分と変更の版 (revisions) のうち状態が「受け入れた」の行の revision_no の一番大きい物 (無ければ自分の値)。
        変更の版の行で呼ぶと、元の行の値。
        """
        if self.revises_id is not None:
            return self.revises.current_revision_no()
        numbers = self.revisions.filter(status=self.STATUS_ACCEPTED).values_list("revision_no", flat=True)
        return max([self.revision_no, *numbers])


class Outbox(models.Model):
    """送った注文。connect.services.send_order が 1 回送るごとに 1 行を作る。

    番号は見出しの webhook-id と同じ uuid4。同じ発注を送り直すときは 1 回目の番号を使い回す
    (相手の受け口は同じ番号を 1 件にまとめる = R11。2 つのタブから送り直しても相手に 2 行できない)。
    相手の返事は日本語の短い文で持つ。
    鍵の値と本文は持たない。
    版 (revision_no) ごとに番号を分ける。版 1 の注文回答は今までどおり発注の列に書く。版 2 から (変更と取消) の注文回答は、
    その版の最初の送った注文の行 (pk の一番小さい行) の reply_* に書く。答えは EDI の注文回答タイプコードの値 (core.edi_codes)。
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
    # 送った版の番号と目的 (送った時の発注の revision_no と purpose_code)。
    revision_no = models.PositiveIntegerField("注文履歴番号", default=1)
    purpose_code = models.CharField(
        "注文書目的コード", max_length=2, choices=edi_codes.PURPOSE_CHOICES, default=edi_codes.PURPOSE_NEW
    )
    # 版 2 からの注文回答 (connect.services._record_revision_reply が書く)。版 1 の答えは発注の列 (purchasing.PurchaseOrder.reply_*)。
    reply_type = models.CharField("注文回答のタイプ", max_length=2, choices=edi_codes.ORDER_REPLY_TYPES, blank=True)
    reply_note = models.CharField("断りの理由", max_length=500, blank=True)
    replied_at = models.DateTimeField("注文回答の日時", null=True, blank=True)
    reply_message_id = models.UUIDField("注文回答の番号", null=True, blank=True, unique=True)

    # 詳細と小窓に出さない列 (core/views.py の hidden_detail_fields が読む)。uuid は画面に出さない。
    detail_hidden_fields = ("reply_message_id",)

    class Meta:
        verbose_name = "送った注文"
        verbose_name_plural = "送った注文"
        ordering = ["-sent_at", "-id"]

    def __str__(self):
        sent = timezone.localtime(self.sent_at)
        # 相手が受け取った行は、誰が受け取ったかを添える (発注の詳細の「送った記録」のタブの行の字。作者の決め)。
        if self.response == "受け取った":
            text = f"{sent:%Y-%m-%d %H:%M} {self.connection.partner.name} が受け取った"
        else:
            text = f"{sent:%Y-%m-%d %H:%M} {self.response}"
        # 版 2 から (変更と取消) は頭に版を、答えがあれば末尾に答えを付ける。第 1 版の行の字は今のまま。
        if self.revision_no >= 2:
            kind = "取消" if self.purpose_code == edi_codes.PURPOSE_CANCEL else "変更"
            text = f"第 {self.revision_no} 版の{kind} {text}"
        reply = _REPLY_TEXTS.get(self.reply_type)
        if reply:
            text = f"{text}・{reply}"
        return text


#: 版 2 からの注文回答の字 (送った記録の行の末尾)
_REPLY_TEXTS = {edi_codes.REPLY_ACCEPTED: "相手が受け入れた", edi_codes.REPLY_REJECTED: "相手が断った"}


class Acceptance(models.Model):
    """検収 (EDI の 4 段目)。相手 (注文を送ってきた得意先の seizou) が受け入れた数の知らせを、受注の明細ごとに 1 件 1 行で持つ。

    番号の一意の制約で同じ番号は 1 行 (R11 の考え)。受信した注文の承認 (accept) とは別の言葉。
    書くのは connect.services.record_acceptance だけ。
    """

    message_id = models.UUIDField("検収の番号", unique=True)
    inbox = models.ForeignKey(
        Inbox,
        verbose_name="元の受信した注文",
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
    # 検収が指す注文の版 (相手の発注の注文履歴番号。無い知らせは 1)
    order_revision_no = models.PositiveIntegerField("注文履歴番号", default=1)

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
    # 知らせが指す注文の版 (相手の受注が合っていた注文履歴番号。無い知らせは 1)
    order_revision_no = models.PositiveIntegerField("注文履歴番号", default=1)

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


class ChatMessage(models.Model):
    """会話の言葉。受注と発注の 1 件ごとに、相手の会社と送り合った言葉を 1 言 1 行で持つ。

    意図: 受注と発注のそれぞれで相手と話し、両方の会社の seizou に同じ会話を残すため (本人の決め 11-06 23:1x〜23:2x)。
    注文と同じ作りで、同じ注文の番号で結ぶ。発注した側は発注 (purchase_order)、受けた側は受信した注文 (inbox) に結ぶ
    (受け入れて受注になった後も受信した注文の結びのまま。受注の詳細は受信した注文を通して引く)。
    会話は中小企業共通 EDI に無い。seizou どうしの送り合い (取引先との接続) だけで送り、送る形は seizou の JSON (kind = chat_message)。
    EDI の文書の種類ではないので、EDI の電文 (XML) に直す物に入れない。EDI のコードの値を持つ欄も作らない。
    書くのは connect.services の関数 (post_chat_message・resend_chat_message・受け口の _receive_chat_message) だけ。
    言葉を直す関数と消す関数は作らない。訳: 電子帳簿保存法 (電子取引のデータの保存)。会話で見積・図面の版・単価の合意など
    取引の中身を送り合うことがあり、送ったとおり・受けたとおりに残すため。確かめた資料 = 中小企業共通 EDI ガイドブック (ver.4.3) 3.2.2
    「電帳法の改正により EDI 取引データはすべて保存することが規定された。保存データの修正・削除についてもその履歴が残るシステムが必須」。
    法令の本文と国税庁の資料は読んでいない。会話が電子取引のデータに当たるかの当てはめは未確認 (適法の側に倒して、直さず消さない形にした)。
    未確認の所: 取引年月日・取引金額・取引先で探す決まりは、この表では作っていない (送り合った中身を残す仕組みの側で扱う)。
    番号 (message_id) は送り手が作り、両方の会社で同じ値。1 つの DB に 2 社の役を置く試験では、同じ表に「自社が書いた」行と
    「相手から届いた」行が同じ番号で並ぶので、一意の制約は番号と向きの組にする。受け口の 2 回目は (番号, 「相手から届いた」) で 1 回目の行を返す (R11 の考え)。
    """

    DIRECTION_SENT = "sent"
    DIRECTION_RECEIVED = "received"
    DIRECTION_CHOICES = [
        (DIRECTION_SENT, "自社が書いた"),
        (DIRECTION_RECEIVED, "相手から届いた"),
    ]

    # 送り手が uuid4 で作り、送る見出しの webhook-id と同じ値。両方の会社で同じ値。
    message_id = models.UUIDField("番号")
    # 発注した側の会話。受けた側の会話は inbox。どちらか片方だけが入る (下の制約)。
    purchase_order = models.ForeignKey(
        "purchasing.PurchaseOrder",
        verbose_name="発注",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="chat_messages",
    )
    inbox = models.ForeignKey(
        Inbox,
        verbose_name="受信した注文",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="chat_messages",
    )
    direction = models.CharField("向き", max_length=10, choices=DIRECTION_CHOICES)
    # 自社が書いた物だけ。
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="書いた人",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # 自社が書いた物は画面に出す名前 (氏名か、空ならログインの名前)。相手から届いた物は相手が送った氏名で、空でよい。
    author_name = models.CharField("書いた人の名前", max_length=100, blank=True)
    # 2000 字までの決まりは services が見る。
    body = models.TextField("言葉")
    # 相手から届いた物は、送り手が書いた日時。並びはこの列。
    written_at = models.DateTimeField("書いた日時", default=timezone.now)
    # 相手から届いた物だけ。
    received_at = models.DateTimeField("届いた日時", null=True, blank=True)
    # 自社が書いた物で、相手の受け口が 200 を返した時だけ入れる。空 = まだ相手に届いていない (Inbox.reply_sent_at と同じ考え)。
    sent_at = models.DateTimeField("相手に届いた日時", null=True, blank=True)
    # 自社が書いた物の、最後に送った時の返事の字 (Outbox.response と同じ字)。
    response = models.CharField("相手の返事", max_length=500, blank=True)

    class Meta:
        verbose_name = "会話の言葉"
        verbose_name_plural = "会話の言葉"
        # 古い順 (会話は上から下へ読む)。
        ordering = ["written_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["message_id", "direction"],
                name="connect_chatmessage_message_direction_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(purchase_order__isnull=False, inbox__isnull=True)
                | models.Q(purchase_order__isnull=True, inbox__isnull=False),
                name="connect_chatmessage_one_target",
            ),
        ]

    def __str__(self):
        written = timezone.localtime(self.written_at)
        return f"{written:%Y-%m-%d %H:%M} {self.author_name}: {self.body[:20]}"

    def delivery_state(self):
        """吹き出しの下に出す字。相手から届いた行は空の字。

        相手に届いた (sent_at がある) なら「届いた <日時>」、届いていなければ「届いていない: <相手の返事>」。
        返事が「届きません」「断られた」で始まるときは前置きを付けない (前置きを重ねない。Inbox.reply_state と同じ決まり)。
        """
        if self.direction == self.DIRECTION_RECEIVED:
            return ""
        if self.sent_at is not None:
            return f"届いた {timezone.localtime(self.sent_at):%Y-%m-%d %H:%M}"
        if self.response.startswith(("届きません", "断られた")):
            return self.response
        return f"届いていない: {self.response}"


def chat_file_upload_to(instance, filename):
    """会話の添付の保存の名前。chat/<年>/<月>/<乱数 32 字><元の拡張子の小文字> (添付ファイルの attachment_upload_to と同じ形)。

    元の名前は保存の名前に使わない (media の道から名前を当てて開けないように)。元の名前は name に残し、開く口が返すときのファイル名にする。
    """
    extension = os.path.splitext(filename)[1].lower()
    today = timezone.localdate()
    return f"chat/{today:%Y}/{today:%m}/{uuid.uuid4().hex}{extension}"


class ChatFile(models.Model):
    """会話の添付。会話の言葉 1 つに付けたファイルを 1 つ 1 行で持つ (1 回に付けられるのは 1 つ)。

    意図: 会話に添付も付けて話せるようにし (本人の決め)、添付は URL の方式で持つ (作者の決め「推奨で」11-06 23:5x):
    会話の言葉には名前と大きさと中身の印だけを載せて送り、受け手の seizou が署名つきの要求で取りに行って自分の側にも保存する。
    項目は中小企業共通 EDI の添付の 2 つの方式のうち URL 記載方式 (本編 6.4.1 (2)) の項目を元にした:
    file_id = 添付バイナリファイル識別ID (UN01006015)・name = 添付バイナリファイル名 (UN01006019)・
    mime = 添付バイナリファイルの MIME コード (UN01006021)・size = 添付バイナリファイルサイズ (UN01006027)。sha256 は EDI に無い
    (受け手が取りに行った中身を確かめるため)。会話は EDI に無い seizou どうしだけの種類なので、EDI の電文に直す物に入れない。
    取りに行く先: URL の字は送る JSON にも表にも持たない。EDI の URL の情報項目 (参照文書の URI = UN01005581) に当たるのは
    「送り手の受け口 (受け手が自分で登録した向き「送る」の接続の URL) + 添付の識別 ID」の組 (送り手の seizou は外から見える自分の URL を持たず、
    受け手は自分が登録した相手にだけ取りに行くので、届いた字に書かれた宛先へ要求を向けることが無い)。
    アクセス制御: 送り手の受け口は、取引先との接続の鍵の署名が通り、その添付が「その取引先との会話」で自社が書いた言葉の物のときだけ返す。
    有効期限: 添付は消さないので期限を設けない。1 回の要求が通る幅は署名の時刻の ±5 分 (connect.signing.verify の tolerance)。
    取り直し: 受け手が取れなかったときは、受け手の画面の「取り直す」で同じ要求を出し直す (送り手の側で何もしない)。
    直す口と消す口を作らない (会話の言葉と同じく電子帳簿保存法の訳 = ChatMessage の docstring)。書くのは connect.services の関数だけ。
    一意の制約は言葉と添付の識別 ID の組 (ChatMessage と同じ訳で、番号だけの一意にしない = 1 つの DB に 2 社の役を置く試験で同じ値が 2 行並ぶ)。
    """

    #: 取りに行っている途中の印 (fetch_response)
    FETCHING = "受け取っています"
    #: 受け取った印 (fetch_response)
    FETCHED = "受け取った"

    message = models.ForeignKey(
        ChatMessage,
        verbose_name="会話の言葉",
        on_delete=models.PROTECT,
        related_name="files",
    )
    # 送り手が uuid4 で作る。両方の会社で同じ値。
    file_id = models.UUIDField("添付の識別 ID", db_index=True)
    name = models.CharField("ファイル名", max_length=100)
    # 値は添付ファイルの形式の表 (attachments.services.ALLOWED) の値。
    mime = models.CharField("形式", max_length=100)
    size = models.PositiveBigIntegerField("大きさ (バイト)")
    sha256 = models.CharField("中身の印 (SHA-256)", max_length=64)
    # 受け手は取りに行くまで空。
    file = models.FileField("ファイル", upload_to=chat_file_upload_to, blank=True)
    # 受け手の行だけ。
    fetched_at = models.DateTimeField("受け取った日時", null=True, blank=True)
    # 受け手の行だけ。空 = まだ取りに行っていない・「受け取っています」= 取りに行っている途中・「受け取った」・ほかは理由の字。
    fetch_response = models.CharField("取りに行った結果", max_length=500, blank=True)

    class Meta:
        verbose_name = "会話の添付"
        verbose_name_plural = "会話の添付"
        ordering = ["message", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["message", "file_id"],
                name="connect_chatfile_message_file_unique",
            ),
        ]

    def __str__(self):
        return self.name


def _qty_text(value) -> str:
    """数量の字。末尾の 0 を落とす (10.00 → 10、2.50 → 2.5)。purchasing の models と同じ (connect は purchasing を頭で import しない)。"""
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(dec.normalize(), "f") if dec != dec.to_integral_value() else str(dec.quantize(Decimal(1)))


class Exchange(models.Model):
    """送り合った記録。会社どうしで送った物と受けた物の中身を、送ったとおり・受けたとおりの字で 1 通 1 行に残す。

    電子帳簿保存法: 電子取引のデータ (送った注文・注文の変更・注文回答・出荷の知らせ・検収・会話) を、直さず消さずに残し、
    取引年月日 (created_at)・取引金額 (amount)・取引先 (partner) で探せるようにする表 (作者の決め)。
    書くのは connect.services の _deliver (送った物) と receive (受けた物) だけ。直す道と消す道は作らない (services が書くのは、
    作る時の全部の列と、送った後・受けた後の result だけ)。見出し (署名) は残さない。鍵の値は表にも中身にも無い。
    新しい表にする訳: 送った注文の表 (Outbox) は送った注文の行だけで、注文回答・検収・出荷の知らせ・受けた物には中身を持つ行が無い。
    送る所 (_deliver) と受ける所 (receive) がどれも 1 か所なので、そこで全部の種類を 1 つの表に残す形を、送り合った物を残す仕組みを
    1 つにする形として作者が選んだ。Outbox はそのまま使い、同じ番号 (message_id) で結び付く。
    訳: 送ったとおり・受けたとおりの中身を、直さず消さずに残し、日付・金額・取引先で探せるようにするため。確かめた資料 =
    中小企業共通 EDI の EDI インボイス仕様編 6.3.2 (電帳法の保存の要件の節。③ 削除変更できない条件で保存する・② 保存期間は 7 年間・
    ④ 検索の条件) とガイドブック 3.2.2 (EDI 取引データの保存と、修正・削除の履歴)。法令の本文と国税庁の資料は未読。
    出所 = 法令と持ち主の決め。
    変えてよい条件 = 法律の保存の決まりが変わったとき。画面の字には法律の当てはめを書かない。
    """

    DIRECTION_SENT = "sent"
    DIRECTION_RECEIVED = "received"
    DIRECTION_CHOICES = [
        (DIRECTION_SENT, "送った"),
        (DIRECTION_RECEIVED, "受けた"),
    ]
    #: 種類の字 (本文の kind → 画面の字)。知らない kind は字のまま出す。後の依頼が自分の kind の行を足す
    KIND_LABELS = {
        "order": "注文",
        "order_reply": "注文回答",
        "acceptance": "検収",
        "shipping_notice": "出荷の知らせ",
        "chat_message": "会話",
        "self_invoice": "仕入明細",
        "self_invoice_response": "仕入明細回答",
    }

    direction = models.CharField("向き", max_length=10, choices=DIRECTION_CHOICES)
    # 本文の kind の字のまま (kind の無い注文は "order")。choices は持たない (後の依頼の kind も同じ列に入る)
    kind = models.CharField("種類", max_length=35, blank=True)
    # 見出しの webhook-id。送り直しは同じ番号で行が増える。uuid の形でない番号は空
    message_id = models.UUIDField("番号", null=True, blank=True, db_index=True)
    partner = models.ForeignKey(
        "masters.Partner", verbose_name="取引先", on_delete=models.PROTECT, related_name="exchanges"
    )
    created_at = models.DateTimeField("日時", default=timezone.now)
    # 送った bytes・受けた bytes を UTF-8 で読んだ字そのもの (整形し直さない)
    body = models.TextField("中身")
    # 取引金額 (作者の決め)。注文と注文の変更は明細の「数量 × 単価」の合計を丸めずに持つ (小数 4 桁まで)。金額の無い物は空。
    # 名前を amount にして access.MONEY_FIELDS に当て、一覧の列・CSV・詳細の項目を金額の閲覧の無い人に出さない (土台のまま)。
    # decimal_places=4 は、明細の数量 (小数 2 桁) × 単価 (小数 2 桁) を丸めずに持てる桁。max_digits=20 は整数 16 桁 (明細の金額の列を足しても足りる見込み)
    amount = models.DecimalField("金額", max_digits=20, decimal_places=4, null=True, blank=True)
    # 送った後・受けた後の結果の字 (受け取った・断られた: …・届きません…・受けた・断った: …)。Outbox.response と同じ字数
    result = models.CharField("結果", max_length=500, blank=True)

    #: 詳細の基本情報に出さない列 (番号の uuid は画面に出さない決まり。中身は詳細の枠で整えて出す)
    detail_hidden_fields = ("message_id", "body")

    class Meta:
        verbose_name = "送り合った記録"
        verbose_name_plural = "送り合った記録"
        ordering = ("-created_at", "-pk")

    def __str__(self):
        return f"{self.get_direction_display()} {self.kind_label()} {timezone.localtime(self.created_at):%Y-%m-%d %H:%M}"

    def _payload(self):
        """中身を JSON として読んだ辞書 (読めなければ None)。読むだけ。"""
        import json

        if not hasattr(self, "_payload_cache"):
            try:
                data = json.loads(self.body)
            except ValueError:
                data = None
            self._payload_cache = data if isinstance(data, dict) else None
        return self._payload_cache

    def kind_label(self):
        """種類の画面の字。注文は中身の注文書目的コードが "4" なら「注文の変更」、"1" なら「注文の取消」。知らない kind は字のまま。"""
        if self.kind == "order":
            purpose = (self._payload() or {}).get("purpose_code")
            if purpose == edi_codes.PURPOSE_CHANGE:
                return "注文の変更"
            if purpose == edi_codes.PURPOSE_CANCEL:
                return "注文の取消"
        return self.KIND_LABELS.get(self.kind, self.kind)

    kind_label.short_description = "種類"

    def order_no(self):
        """中身の注文番号 (order_no。どの種類も発注元の発注番号)。無ければ空の字。"""
        value = (self._payload() or {}).get("order_no")
        return value if isinstance(value, str) else ""

    order_no.short_description = "注文番号"


class ReceivedSelfInvoice(models.Model):
    """受けた仕入明細 (中小企業共通 EDI の仕入明細の項目を元にした)。得意先 (発注元の seizou) が受入をまとめて送った物。受注の明細ごとに答える (承諾・提案・確認できない)。

    インボイス制度では、ここで承諾した仕入明細が、得意先の仕入税額控除の書類になる (売り手の確認。確かめた資料 = 中小企業共通 EDI インボイス仕様編 3.1.8。
    国税庁の資料と条文は未読)。書くのは connect.services だけ。直す道と消す道は作らない (修正仕入明細は同じ行の版を上げる)。
    番号と版: 得意先と仕入明細番号の組で 1 行。message_id は今の版を受けた番号 (同じ番号の 2 回目は何もしない = R11)。
    """

    STATUS_RECEIVED = "received"
    STATUS_PARTIAL = "partial"
    STATUS_CONFIRMED = "confirmed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_RECEIVED, "未回答"),
        (STATUS_PARTIAL, "確かめ中"),
        (STATUS_CONFIRMED, "確認済"),
        (STATUS_CANCELLED, "取消"),
    ]

    partner = models.ForeignKey(
        "masters.Partner",
        verbose_name="得意先",
        on_delete=models.PROTECT,
        related_name="received_self_invoices",
    )
    self_invoice_no = models.CharField("仕入明細番号", max_length=35)
    revision_no = models.PositiveIntegerField("版", default=1)
    # 文書の種類 (インボイス文書類型コード)。版 1 は仕入明細 "1"、版 2 からは修正仕入明細 "1C"。
    document_code = models.CharField("文書の種類", max_length=3, choices=edi_codes.SI_DOC_CHOICES[:2])
    purpose_code = models.CharField(
        "目的", max_length=2, choices=edi_codes.PURPOSE_CHOICES, default=edi_codes.PURPOSE_NEW
    )
    issued_on = models.DateField("発行日")
    period_from = models.DateField("締め期間の初め")
    period_to = models.DateField("締め期間の終わり")
    due_on = models.DateField("支払期日", null=True, blank=True)
    subtotal = models.DecimalField("小計", max_digits=14, decimal_places=2)
    tax = models.DecimalField("消費税", max_digits=14, decimal_places=2)
    total = models.DecimalField("合計", max_digits=14, decimal_places=2)
    tax_rate = models.DecimalField("税率 (%)", max_digits=5, decimal_places=2)
    tax_category_code = models.CharField("課税分類コード", max_length=3)
    # 得意先が仕入明細に書いた自社 (受ける側) の名前と登録番号 (インボイス制度の受注者の名前と登録番号)。
    supplier_name = models.CharField("仕入明細の自社の名前", max_length=100)
    supplier_registration_no = models.CharField("仕入明細の登録番号", max_length=14, blank=True)
    status = models.CharField("状態", max_length=10, choices=STATUS_CHOICES, default=STATUS_RECEIVED)
    message_id = models.UUIDField("仕入明細の番号", unique=True)
    received_at = models.DateTimeField("届いた日時", default=timezone.now)
    # 答え (仕入明細回答) を送る番号。答えるたびに新しい番号。相手の受け口が 200 を返したときだけ送った日時を入れる。
    reply_message_id = models.UUIDField("仕入明細回答の番号", null=True, blank=True, unique=True)
    # 今の答えの文書の種類 (インボイス文書類型コード)。その仕入明細番号への 1 回目の答えは仕入明細回答 "1R"、2 回目からは修正仕入明細回答 "1CR"。
    # 答えを作る時に決めて残す (同じ答えの送り直しは同じ本文で送るため)。
    reply_document_code = models.CharField(
        "答えの文書の種類", max_length=3, choices=edi_codes.SI_DOC_CHOICES[2:], blank=True
    )
    reply_sent_at = models.DateTimeField("答えを送った日時", null=True, blank=True)
    reply_response = models.CharField("答えの相手の返事", max_length=500, blank=True)

    #: 詳細と小窓に出さない列 (uuid は画面に出さない)
    detail_hidden_fields = ("message_id", "reply_message_id")
    #: 変更の履歴を残す (名前が _sent_at・_response で終わる列は残らない)
    keeps_history = True

    class Meta:
        verbose_name = "受けた仕入明細"
        verbose_name_plural = "受けた仕入明細"
        ordering = ["-received_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "self_invoice_no"],
                name="connect_receivedselfinvoice_partner_no_unique",
            ),
        ]

    def __str__(self):
        # 受注の詳細の「仕入明細」のタブの行の字 (状態を含める)。
        return (
            f"{self.self_invoice_no} 第 {self.revision_no} 版 "
            f"({self.period_from:%Y-%m-%d}〜{self.period_to:%Y-%m-%d}) {self.get_status_display()}"
        )

    def reply_state(self):
        """答えを送った結果の字 (詳細の黄の 1 行)。答えを作っていなければ空の字。Inbox.reply_state と同じ形。"""
        if self.reply_message_id is None:
            return ""
        if self.reply_sent_at is not None:
            return f"送った {timezone.localtime(self.reply_sent_at):%Y-%m-%d %H:%M}"
        if self.reply_response.startswith(("届きません", "断られた")):
            return self.reply_response
        return f"届いていない: {self.reply_response}"


class ReceivedSelfInvoiceLine(models.Model):
    """受けた仕入明細の明細。受注の明細に当てた 1 行 (得意先の受入 1 件)。数・単価・金額は得意先が送った値。答えの列は自社が返した答え。

    受領日は得意先が物を受け取った日 (取適法の支払期日の起算の日)。列の名前と型は送る側 (purchasing.SelfInvoiceLine) と同じ。
    """

    self_invoice = models.ForeignKey(
        ReceivedSelfInvoice,
        verbose_name="受けた仕入明細",
        on_delete=models.CASCADE,
        related_name="lines",
    )
    line_no = models.PositiveIntegerField("行番号")
    order_line = models.ForeignKey(
        "orders.OrderLine",
        verbose_name="受注の明細",
        on_delete=models.PROTECT,
        related_name="received_self_invoice_lines",
    )
    buyer_order_no = models.CharField("相手の発注番号", max_length=35, blank=True)
    buyer_line_no = models.PositiveIntegerField("相手の行番号")
    order_revision_no = models.PositiveIntegerField("注文履歴番号", default=1)
    received_on = models.DateField("受領日")
    qty = models.DecimalField("受領数量", max_digits=12, decimal_places=2)
    unit = models.CharField("単位", max_length=10, blank=True)
    unit_code = models.CharField("数量単位コード", max_length=10, blank=True, editable=False)
    price_basis = models.CharField("単価の単位", max_length=10, choices=PRICE_BASIS_CHOICES, default=PRICE_BASIS_UNIT)
    unit_price = models.DecimalField("単価", max_digits=14, decimal_places=2)
    amount = models.DecimalField("金額", max_digits=14, decimal_places=2)
    reply_type = models.CharField("自社の答え", max_length=2, choices=edi_codes.SI_REPLY_LINE_TYPES, blank=True)
    reply_reason_code = models.CharField("確認できない訳", max_length=3, choices=edi_codes.SI_REASON_CHOICES, blank=True)
    reply_qty = models.DecimalField("提案の数", max_digits=12, decimal_places=2, null=True, blank=True)
    reply_unit_price = models.DecimalField("提案の単価", max_digits=14, decimal_places=2, null=True, blank=True)
    reply_note = models.CharField("書いておく字", max_length=500, blank=True)
    revised_in = models.PositiveIntegerField("直した版", default=1)

    #: 変更の履歴は受けた仕入明細の詳細の「履歴」に出す
    history_owner = "self_invoice"

    class Meta:
        verbose_name = "受けた仕入明細の明細"
        verbose_name_plural = "受けた仕入明細の明細"
        ordering = ["self_invoice", "line_no"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gt=0),
                name="connect_receivedselfinvoiceline_qty_positive",
            ),
            models.UniqueConstraint(
                fields=["self_invoice", "line_no"],
                name="connect_receivedselfinvoiceline_line_no_unique",
            ),
        ]

    def __str__(self):
        return f"{self.self_invoice.self_invoice_no}-{self.line_no}"

    @property
    def price_unit_text(self):
        """単価の後ろに添える字 (「¥250 / 個」の「個」。式なら「式」)。"""
        return price_basis_text(self.price_basis, self.unit)
