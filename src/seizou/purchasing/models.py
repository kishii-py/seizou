"""発注の表 (表の設計の決め)。協力企業管理の最小の形。

文字数の上限は固定 (識別子 35・備考 500・コード 10)。
金額は purchasing.services が数量 × 単価 (単価の単位が式なら単価のまま) を取引先の小計の計算方法で丸めて書く (DB で計算しない)。
受入 (Receipt) の金額も、purchasing.services が受け入れた数 × 発注の単価を同じ方法で丸めて書く (一式の明細は全部を受け入れた回で一式の全額、ほかの回は 0。作者の決め)。検収は受入の後に connect.services.send_acceptance が相手へ送る (列は Receipt の acceptance_*)。支給材は次の版で。書くときは purchasing.services を通す。
受入の数と残は列に持たず、purchasing.services の集計 (received_qty・remaining_qty・annotate_received) で出す。
注文回答のタイプ (reply_type) は中小企業共通 EDI の注文回答タイプコードと注文回答明細回答タイプコードの項目を元にした値 (core.edi_codes)。
注文履歴番号・注文書目的コード・明細状態コード・明細状態理由コードは中小企業共通 EDI の項目を元にした (注文変更の方式 2。値は core.edi_codes)。取り消した明細 (明細状態コード 2) は行を残し、受入の候補と数と金額の合計に入れない。
完了の印 (completed。人が受入の足りないまま終えると決めた明細に付ける) は purchasing.services.mark_completed と clear_completed だけが書き、受入の残の数え方 (purchasing.services.remaining_qty・annotate_received。RULES.md R25) が読む。
明細の指示書の工程 (workorder_step。協力企業に出した指示書の工程。見比べのまとめの M01) は purchasing.services.create_purchase_order だけが書く (部品と工程は指示書の工程から写す)。
明細の渡した日 (handed_on。現物を協力企業に渡した日。空 = まだ渡していない) は purchasing.services.create_purchase_order と set_handed_on だけが書く。
受入の実績 (Receipt.progress。指示書の工程を指す明細の受入から作った進捗の実績。RULES.md R32) は purchasing.services.receive だけが書く (人が実績を消すと空になる)。
仕入明細 (SelfInvoice・SelfInvoiceLine) は、受入をまとめて相手へ送る書類 (中小企業共通 EDI の仕入明細の項目を元にした。作者の決め)。書くのは purchasing.services だけ。
"""

from decimal import Decimal
from functools import cached_property

from django.conf import settings
from django.db import models
from django.utils import timezone

from seizou.core import edi_codes
from seizou.masters.services import PRICE_BASIS_CHOICES, PRICE_BASIS_UNIT, price_basis_text


class PurchaseOrder(models.Model):
    """発注。1 件に明細が複数。発注番号は core の連番が付ける。keeps_report_copy は帳票の写し (core.ReportCopy。R34) を版ごとに持つ印で、詳細に「発行の時の写し」の枠を出す。"""

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
    # つながりの無い取引先へ、発注書を seizou の外 (FAX・郵送・メールなど) で送ったと記録した日時 (空 = 記録していない)。
    # 書くのは purchasing.services.mark_sent_outside だけ。状態の値は足さず、「送った」の発注のうち seizou の外で送った物をこの列で見分ける
    # (送った記録の行が無い事では見分けない。作り物の種は、送った記録の無い「送った」の発注を作るため)。本人の決め 11-12 14:3x・18:2x と本体の決め 11-12 (依頼書 N439)。
    sent_outside_at = models.DateTimeField("発注書を送った日時", null=True, blank=True, editable=False)

    # 詳細と小窓に出さない列 (core/views/ の hidden_detail_fields が読む)。uuid は画面に出さない。
    detail_hidden_fields = ("reply_message_id",)
    #: 変更の履歴 (core.history。作者の決めの伝票の「発注」) を残し、詳細に「履歴」のタブを出す
    keeps_history = True
    keeps_report_copy = True

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
    def self_invoices(self):
        """この発注の受入が入った仕入明細 (読むだけ。取消も含む。1 枚に同じ発注の受入が 2 つあっても 1 行)。

        発注の詳細の「仕入明細」のタブのための入口 (土台の tabs は名前で引く)。行の字は SelfInvoice.__str__ (番号・版・締め期間・状態)。
        """
        return SelfInvoice.objects.filter(lines__receipt__line__purchase_order=self).distinct()

    @property
    def lines_with_received(self):
        """明細に受入の数 (received_total) と受入の残 (remaining_total) を足した物 (読むだけ。詳細の明細の表が回す)。

        指示書の工程と、その指示書と工程の名前も一緒に読む (明細の部品の枡の下の「指示書 … の工程 …」を、明細ごとに問い合わせずに出すため。
        purchasing_tags の line_step が読む。発注の詳細は 2 秒ごとに取り直すので、明細の数だけ問い合わせを増やさない)。
        相手からの出荷の知らせ (connect の逆向きの関係 shipping_notices) も、明細ごとに問い合わせずに 1 回で読む
        (明細の shipping_notice と purchasing.services の notice_rests が読む。connect が入っていない会社では関係が無いので読まない)。
        """
        from . import services

        lines = self.lines.select_related("item", "process", "workorder_step__workorder", "workorder_step__process").order_by(
            "line_no", "pk"
        )
        if hasattr(self.lines.model, "shipping_notices"):
            lines = lines.prefetch_related("shipping_notices")
        return services.annotate_received(lines)


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
    # 単位の数量単位コード (作者の決め。書くのは purchasing.services だけ。明細の単位の字が部品の単位と同じなら部品のコードを写し、違う字は名前から引く)
    unit_code = models.CharField("数量単位コード", max_length=10, blank=True, editable=False)
    unit_price = models.DecimalField("単価", max_digits=14, decimal_places=2)
    # 単価の単位 (作者の決め)。部品の単位 (1 つあたり) か式 (一式。金額に数量を掛けない)。書くのは purchasing.services だけ。
    # 数量の単位の列 (unit) とは別。式は数量の単位でないので unit に入れない (数量は実際の個数のまま)。
    price_basis = models.CharField("単価の単位", max_length=10, choices=PRICE_BASIS_CHOICES, default=PRICE_BASIS_UNIT)
    amount = models.DecimalField("金額", max_digits=14, decimal_places=2)
    due_date = models.DateField("納期", null=True, blank=True)
    note = models.CharField("備考", max_length=500, blank=True)
    # 協力企業に出す工程 (作者の決め。空でもよい)。入力の欄は工程マスターの使用中で協力企業に出す工程から選ぶ。中小企業共通 EDI の工程名を元に、送る注文の明細に工程の名前を載せる。書くのは purchasing.services だけ。
    # PROTECT の訳: 工程は消さずに使用停止にする作りで、発注に使った工程を消せないようにするため (指示書の工程と同じ)。
    process = models.ForeignKey(
        "masters.Process", verbose_name="工程", on_delete=models.PROTECT, null=True, blank=True, related_name="purchase_order_lines"
    )
    # 単価表から写した見積の番号 (作者の決め)。中小企業共通 EDI の (明細行参照) 見積回答書ID を元にした列。手でも打てる。
    quote_no = models.CharField("見積の番号", max_length=35, blank=True, default="")
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
    # 人が付け外しする完了の印 (本人の決め 11-11 08:4x・09:3x)。受入の数と結ばない。明細が終わっているのは、この印が真か、受入の合計が数量以上のとき。
    # 書くのは purchasing.services.mark_completed と clear_completed だけ (受入・発注の変更の数量の直しでは書き換えない)。
    # 明細状態コードの「明細行取消」(2) は使わない (相手へ送る取消のコードで、明細の数量と金額の合計からも外れるため)。
    completed = models.BooleanField("完了", default=False, editable=False)
    # 協力企業に出した指示書の工程 (見比べのまとめの M01。空 = 指示書の工程に結ばない明細)。書くのは purchasing.services.create_purchase_order だけ
    # (部品と工程は指示書の工程から写す)。この明細の受入は、同じ処理の中でこの工程の実績になる (RULES.md R32)。
    # PROTECT の訳: 指示書は取り消すだけで消さないので、工程が消えるのは表の外の消し方だけ。そのときに明細の結びが黙って消えないようにするため。
    workorder_step = models.ForeignKey(
        "workorders.WorkOrderStep",
        verbose_name="指示書の工程",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="purchase_order_lines",
        editable=False,
    )
    # 現物を協力企業に渡した日 (空 = まだ渡していない)。書くのは purchasing.services.create_purchase_order と set_handed_on だけ。
    handed_on = models.DateField("渡した日", null=True, blank=True, editable=False)

    #: 変更の履歴は発注の詳細の「履歴」に出す (core.history。作者の決めの伝票の「発注」)
    history_owner = "purchase_order"

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
        # (受入の入力の候補の口の字。作者の決め)。取引先も読むので、候補の口は purchase_order__partner も select_related する。
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
        式なら「式」、ほかはこの明細の数量の単位、空なら「個」(作者の決め。関数は masters の price_basis_text)。"""
        return price_basis_text(self.price_basis, self.unit)

    @property
    def is_cancelled(self):
        """取り消した明細か (明細状態コード 2 = 明細行取消)。行は残し、受入の候補と数と金額の合計に入れない。"""
        return self.line_status_code == edi_codes.LINE_DELETED

    @property
    def detail_link_target(self):
        """詳細の基本の表でこの明細の値を押した時の行き先 (読むだけ)。親の発注。
        明細には詳細の画面が無いので、core の field_items が getattr で読み、発注の詳細への link にする (作者の決め)。"""
        return self.purchase_order

    @cached_property
    def shipping_notice(self):
        """出荷の知らせの要約 (読むだけ)。発注の詳細の明細の表が読む。"""
        from . import services

        return services.shipping_notice_summary(self)


class Receipt(models.Model):
    """受入。協力企業から受け入れた物の記録。発注の明細ごとに何回でも入れられる (分納)。

    金額は purchasing.services が 受け入れた数 × 明細の単価 を発注の取引先の方法で丸めて書く (DB で計算しない。一式の明細は全部を受け入れた回で一式の全額、ほかの回は 0。作者の決め)。
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
    # 指示書の工程を指す明細の受入から作った進捗の実績 (RULES.md R32)。書くのは purchasing.services.receive だけ。画面に出さない照合の列。
    # SET_NULL の訳: 人が実績を消しても、受け取った事実と金額の記録は残すため。
    progress = models.OneToOneField(
        "progress.Progress",
        verbose_name="受入から作った実績",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="receipt",
        editable=False,
    )

    #: 詳細と小窓に出さない列 (core/views/ の hidden_detail_fields が物 1 件ずつ読む)。番号 (uuid) と受入から作った実績はいつも画面に出さない。
    #: 検収を送った日時と相手の返事の 2 列は、seizou どうしで送った発注の受入 (相手へ検収を送る物) だけ出す。
    #: 送った発注で検収がまだ無い受入は「—」のまま出す (決め 11-05 09:3x の「まだ無い」)。
    #: 送っていない発注の受入では入らない欄で、「—」の行は何の欄か迷わせる (作者の決め)。
    #: 送った発注かの判定は、送る側と同じ services._is_sent_order を使う (同じ式を 2 か所に書かない)。
    #: purchasing.services が頭でこの model を読むので、services は中で import する (頭で読むと輪になる)。
    @property
    def detail_hidden_fields(self):
        hidden = ("acceptance_message_id", "progress")
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

    @property
    def self_invoice_no(self):
        """この受入が入った取消でない仕入明細の番号 (読むだけ)。入っていなければ空の字。

        受入の一覧の「仕入明細」の列が読む。self_invoice_lines.all() を Python で見るので、一覧の prefetch_related ("self_invoice_lines__self_invoice") が効く。
        """
        for line in self.self_invoice_lines.all():
            if line.self_invoice.status != SelfInvoice.STATUS_CANCELLED:
                return line.self_invoice.self_invoice_no
        return ""

    def acceptance_state(self):
        """発注の詳細の「検収」の節に出す字。送る物でない受入は空の字 (テンプレートが薄い「—」を出す)。

        送れた (相手の受け口が 200) なら「送った <日時>」、送れていなくて返事があれば
        「届いていない: <相手の返事>」。返事が「届きません」「断られた」で始まるときは前置きを付けない (前置きを重ねない。作者の決め)。
        """
        if self.acceptance_sent_at is not None:
            return f"送った {timezone.localtime(self.acceptance_sent_at):%Y-%m-%d %H:%M}"
        if self.acceptance_response:
            if self.acceptance_response.startswith(("届きません", "断られた")):
                return self.acceptance_response
            return f"届いていない: {self.acceptance_response}"
        return ""


class SelfInvoice(models.Model):
    """仕入明細 (中小企業共通 EDI の仕入明細の項目を元にした)。発注元が受入をまとめて作り、相手 (協力企業の seizou) へ送り、相手が明細ごとに答える。

    インボイス制度では、仕入明細書は売り手 (相手) の確認を得ると適格請求書と同じ扱いになり、記載事項 (相手の名前と登録番号・取引の日・品目・
    税率ごとの合計と税額と税率) は適格請求書と同じ。確かめた資料 = 中小企業共通 EDI インボイス仕様編 3.1.8「請求レスユースケース」と 6.1.1。
    国税庁の資料と条文は未読。
    取適法 (2026-01-01 施行。旧下請法) では、代金の支払期日は物を受け取った日から 60 日以内で定める。支払期日 (due_on) は取引先の締日・
    支払の月・支払日から作り (請求書と同じ計算)、一番早い受領日から 60 日を超えると詳細に知らせる (止めない。作者の決め。
    確かめた資料 = 中小企業共通 EDI インボイス仕様編 Ⅰ.Ⅲ「インボイス以外の改定」。公正取引委員会の一次資料は未読)。
    金額・消費税・税率・期間・支払期日は作った時の写しで、受入や取引先を後で直しても変わらない (請求書と同じ)。
    取り消した仕入明細は消さずに状態「取消」で残す (作者の決め)。書くのは purchasing.services だけ。
    状態と答えのコードは core.edi_codes の値 (目的は PURPOSE_*、答えは SI_REPLY_TYPES)。画面にはコードを出さず日本語の字を出す。
    """

    STATUS_DRAFT = "draft"
    STATUS_SENT = "sent"
    STATUS_PARTIAL = "partial"
    STATUS_CONFIRMED = "confirmed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "未送信"),
        (STATUS_SENT, "答え待ち"),
        (STATUS_PARTIAL, "確かめ中"),
        (STATUS_CONFIRMED, "確認済"),
        (STATUS_CANCELLED, "取消"),
    ]

    self_invoice_no = models.CharField("仕入明細番号", max_length=35, unique=True)
    partner = models.ForeignKey(
        "masters.Partner",
        verbose_name="取引先",
        on_delete=models.PROTECT,
        related_name="self_invoices",
    )
    issued_on = models.DateField("発行日")
    period_from = models.DateField("締め期間の初め")
    period_to = models.DateField("締め期間の終わり")
    due_on = models.DateField("支払期日", null=True, blank=True)
    # 目的 (インボイス文書目的コード)。版 1 は新規 "9"、提案を受け入れた版は "4"、取消の版は "1"。画面には出さない (状態の「取消」で分かる)。
    purpose_code = models.CharField(
        "目的", max_length=2, choices=edi_codes.PURPOSE_CHOICES, default=edi_codes.PURPOSE_NEW
    )
    subtotal = models.DecimalField("小計", max_digits=14, decimal_places=2)
    tax = models.DecimalField("消費税", max_digits=14, decimal_places=2)
    total = models.DecimalField("合計", max_digits=14, decimal_places=2)
    # 作った時の消費税の率の写し (百分率。10.00 = 10%)。率は billing.services.consumption_tax_rate の 1 か所から読む。
    tax_rate = models.DecimalField("税率 (%)", max_digits=5, decimal_places=2)
    # 作った時の取引先の登録番号の写し (インボイス制度の受注者の登録番号 = 付表 1 の SIID27)。
    supplier_registration_no = models.CharField("相手の登録番号", max_length=14, blank=True)
    status = models.CharField("状態", max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    revision_no = models.PositiveIntegerField("版", default=1)
    # 今の版を送る番号 (webhook-id)。送り直すときは同じ番号 (相手は同じ番号を 1 件にまとめる = R11)。版を上げると新しい番号。画面に出さない。
    message_id = models.UUIDField("仕入明細の番号", null=True, blank=True, unique=True)
    # 相手の受け口が 200 を返したときだけ入れる。空 = まだ相手に届いていない。
    sent_at = models.DateTimeField("送った日時", null=True, blank=True)
    response = models.CharField("相手の返事", max_length=500, blank=True)
    # 相手の答え (鑑ヘッダ仕入明細回答タイプコード)・届いた日時・答えの番号 (同じ番号の 2 回目は何もしない = R11)。
    reply_type = models.CharField("相手の答え", max_length=2, choices=edi_codes.SI_REPLY_TYPES, blank=True)
    replied_at = models.DateTimeField("答えが届いた日時", null=True, blank=True)
    reply_message_id = models.UUIDField("仕入明細回答の番号", null=True, blank=True, unique=True)

    #: 詳細と小窓に出さない列 (core/views/ の hidden_detail_fields が読む)。uuid は画面に出さない。
    detail_hidden_fields = ("message_id", "reply_message_id")
    #: 変更の履歴 (core.history) を残し、詳細に「履歴」のタブを出す。送った日時と返事と答えの日時は送り直しのたびに動くので残さない。
    keeps_history = True
    history_skip = ("sent_at", "response", "replied_at")

    class Meta:
        verbose_name = "仕入明細"
        verbose_name_plural = "仕入明細"
        ordering = ["-issued_on", "-id"]

    def __str__(self):
        # 発注の詳細の「仕入明細」のタブの行の字 (状態を含める)。
        return (
            f"{self.self_invoice_no} 第 {self.revision_no} 版 "
            f"({self.period_from:%Y-%m-%d}〜{self.period_to:%Y-%m-%d}) {self.get_status_display()}"
        )

    def receipt_total(self):
        """明細の受入の金額の合計 (Python で足す。明細の受入は select_related か prefetch_related で引いておく)。"""
        return sum((line.receipt.amount for line in self.lines.all()), Decimal("0"))

    def receipt_difference(self):
        """仕入明細の小計 − 明細の受入の金額の合計 (詳細の合計の枠の「差」。0 なら「差はありません」)。"""
        return self.subtotal - self.receipt_total()

    def confirmed_subtotal(self):
        """相手が承諾した明細 (承諾と、直した内容の承諾) の金額の合計 (Python で足す)。"""
        accepted = (edi_codes.SI_REPLY_ACCEPTED, edi_codes.SI_REPLY_ACKNOWLEDGED)
        return sum((line.amount for line in self.lines.all() if line.reply_type in accepted), Decimal("0"))

    def unconfirmed_subtotal(self):
        """答え待ち・確かめ中の金額 (小計 − 確認済の金額)。"""
        return self.subtotal - self.confirmed_subtotal()

    def has_proposal(self):
        """相手が提案 (UV) と答えた明細があるか (詳細の「提案を受け入れる」のボタンが読む)。"""
        return any(line.reply_type == edi_codes.SI_REPLY_PROPOSED for line in self.lines.all())

    def has_reply(self):
        """相手の答えが届いているか (答えの番号があるか、明細のどれかに答えがある)。取消の見方と詳細の「取消」のボタンが読む。"""
        if self.reply_message_id is not None:
            return True
        return any(line.reply_type for line in self.lines.all())

    def delivery_state(self):
        """今の版を送った結果の字 (詳細の黄の 1 行)。Receipt.acceptance_state と同じ形。送っていなければ空の字。

        送れた (相手の受け口が 200) なら「送った <日時>」、送れていなくて返事があれば「届いていない: <相手の返事>」。
        返事が「届きません」「断られた」で始まるときは前置きを付けない (前置きを重ねない)。
        """
        if self.sent_at is not None:
            return f"送った {timezone.localtime(self.sent_at):%Y-%m-%d %H:%M}"
        if self.response:
            if self.response.startswith(("届きません", "断られた")):
                return self.response
            return f"届いていない: {self.response}"
        return ""


class SelfInvoiceLine(models.Model):
    """仕入明細の明細。受入 1 件に 1 行 (中小企業共通 EDI の明細行受領日・受領数量が 1 行に 1 つのため)。

    受領日は発注元が物を受け取った日 (受入日の写し)。取適法 (2026-01-01 施行。旧下請法) では、代金の支払期日は物を受け取った日から 60 日以内で定め、
    受け取った日がその起算の日になる。中小企業共通 EDI ver.4.3 は取適法に合わせて仕入明細に受領日と受領数量を足した。
    確かめた資料 = 中小企業共通 EDI 標準仕様書 本編の改定履歴とインボイス仕様編 Ⅰ.Ⅲ「インボイス以外の改定」(公正取引委員会の一次資料は未読)。
    数量・単位・数量単位コード・単価の単位・単価・金額は作った時の写し (金額は受入の金額)。答えの列は相手の仕入明細回答の明細の値。
    受入は外部キー (1 つの受入を取消でない仕入明細 2 つに入れない約束は services が見る。取り消した仕入明細の受入は、また入れられる)。
    """

    self_invoice = models.ForeignKey(
        SelfInvoice,
        verbose_name="仕入明細",
        on_delete=models.CASCADE,
        related_name="lines",
    )
    line_no = models.PositiveIntegerField("行番号")
    receipt = models.ForeignKey(
        Receipt,
        verbose_name="受入",
        on_delete=models.PROTECT,
        related_name="self_invoice_lines",
    )
    # 受領日 (受入日の写し)。取適法の支払期日の起算の日。
    received_on = models.DateField("受領日")
    qty = models.DecimalField("受領数量", max_digits=12, decimal_places=2)
    unit = models.CharField("単位", max_length=10, blank=True)
    unit_code = models.CharField("数量単位コード", max_length=10, blank=True, editable=False)
    price_basis = models.CharField("単価の単位", max_length=10, choices=PRICE_BASIS_CHOICES, default=PRICE_BASIS_UNIT)
    unit_price = models.DecimalField("単価", max_digits=14, decimal_places=2)
    amount = models.DecimalField("金額", max_digits=14, decimal_places=2)
    # 相手の答え (明細行仕入明細回答タイプコード)・確認できない訳 (明細行仕入明細回答理由コード)・提案の数と単価・相手の書いた字。
    reply_type = models.CharField("相手の答え", max_length=2, choices=edi_codes.SI_REPLY_LINE_TYPES, blank=True)
    reply_reason_code = models.CharField("確認できない訳", max_length=3, choices=edi_codes.SI_REASON_CHOICES, blank=True)
    reply_qty = models.DecimalField("提案の数", max_digits=12, decimal_places=2, null=True, blank=True)
    reply_unit_price = models.DecimalField("提案の単価", max_digits=14, decimal_places=2, null=True, blank=True)
    reply_note = models.CharField("相手の書いた字", max_length=500, blank=True)
    # この明細を最後に直した版 (修正仕入明細で直した明細は、その版の番号)。
    revised_in = models.PositiveIntegerField("直した版", default=1)

    #: 変更の履歴は仕入明細の詳細の「履歴」に出す (core.history)
    history_owner = "self_invoice"

    class Meta:
        verbose_name = "仕入明細の明細"
        verbose_name_plural = "仕入明細の明細"
        ordering = ["self_invoice", "line_no"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gt=0),
                name="purchasing_selfinvoiceline_qty_positive",
            ),
            models.UniqueConstraint(
                fields=["self_invoice", "line_no"],
                name="purchasing_selfinvoiceline_line_no_unique",
            ),
            # 1 つの受入を取消でない仕入明細 2 つに入れない約束は DB の制約にしない
            # (取り消した仕入明細の受入は、また仕入明細に入れられるため)。services が行をロックして見る。
            models.UniqueConstraint(
                fields=["self_invoice", "receipt"],
                name="purchasing_selfinvoiceline_receipt_unique",
            ),
        ]

    def __str__(self):
        return f"{self.self_invoice.self_invoice_no}-{self.line_no}"

    @property
    def price_unit_text(self):
        """単価の後ろに添える字 (「¥250 / 個」の「個」。式なら「式」)。"""
        return price_basis_text(self.price_basis, self.unit)


def _qty_text(value) -> str:
    """数量の字。末尾の 0 を落とす (10.00 → 10、2.50 → 2.5)。"""
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(dec.normalize(), "f") if dec != dec.to_integral_value() else str(dec.quantize(Decimal(1)))
