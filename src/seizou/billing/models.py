"""請求書の表 (表の設計の決め)。

請求書は得意先ごとに、出荷済みの出荷 (出荷の記録がある出荷) をまとめた物。
明細は出荷ごとに 1 行 (と、出し切らずに終えた一式の受注の明細に人が金額を決めた行。作者の決め) で、数量・単価・金額の写しを持つ (出した請求書の金額は、後で受注の単価を直しても変わらない)。
支払期日も作ったときに計算して残す (後で取引先の支払の欄を直しても、出した請求書の期日は変わらない)。
文字数の上限は固定 (識別子 35・コード 10)。
書くときは billing.services を通す。
入金 (Payment) は別の表。請求書の外で受け取った入金は出荷を指す (請求書は空)。残と入金の状態は Invoice の method で出す (列に持たない)。合計が 0 以下の請求書は入金済み。
調整 (Adjustment) も別の表。請求書を指し、値引き・振込手数料・相殺・端数で請求書の残を増やすか減らす (残を減らす調整はマイナス)。
請求書の残 = 合計 − 取消でない入金 + 取消でない調整 (Invoice.balance。一覧・詳細・ダッシュボード・検査の層・入金の状態が同じ式を使う)。
入金の期日 (Payment.matures_on) は手形と電子記録債権の期日 (支払期日 Invoice.due_on と別の物)。
前回の残 (Invoice.previous_balance) は、会社の設定が「載せる」のときだけ請求書を作る時に数えて写す (空なら帳票に出さない)。
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

from seizou.masters.services import PRICE_BASIS_CHOICES, PRICE_BASIS_LUMP, PRICE_BASIS_UNIT, price_basis_text


class Invoice(models.Model):
    """請求書。請求書番号は core の連番が付ける。keeps_report_copy は帳票の写し (core.ReportCopy。R34) を持つ印で、詳細に「発行の時の写し」の枠を出す。

    発行した時の会社の登録番号・住所・電話・振込先 (issuer_ の 4 列) と、得意先の名前 (customer_name) と、税率 (tax_rate。百分率) を写して持つ。
    書くのは billing.services._issue_invoice の 1 か所。帳票 (billing.reports.invoice_context) は写しだけを読み、写しが空でも今の値で補わない。
    会社名は答えの YAML で決まり直せない (core.services.update_company が断る) ので写さない (帳票は今の会社名を出す)。ロゴも写さない。
    migration (0007) の前に作った請求書の写しは、migration の時の今の値で埋めた (今ある行には今の値しか入れられない。発行の時の値は残っていない)。
    写しの列は入力の欄と CSV の見出しに出さず (editable=False)、詳細と小窓の項目の一覧にも出さない (detail_hidden_fields)。
    振込先の写しは、会社の振込先と同じく変更の履歴に値を残さない (history_hidden_values)。
    訳: 請求書は発行した時の会社の登録番号・住所・電話・振込先と、得意先の名前と、明細の品番・品名・単位 (受注の明細の単位 = 注文で合意した字)・
    得意先の注文番号と、税率を写して持ち、刷り直しても渡した時と同じ字を出す。インボイス制度の記載事項を持つ書類で、マスターや会社の設定を後から直すと
    刷り直しの字が変わるため。振込先は消費税法の記載事項でないが、後で変えても渡した時と同じ字で刷り直すため写す。部品の単位は後から直されることが
    あるため、受注の明細から写す。納品書は会期中は写さない (shipping の migration が増えるため)。インボイス制度と電帳法への当てはめは、
    国税庁の資料と条文を読んでいないので未確認。出所 = 本人の決め 11-07 23:4x (今回は請求書だけ) と 11-08 00:0x (振込先・単位・注文番号)。
    変えてよい条件 = 本人が決め直したとき (納品書も写すと決めたとき)。
    """

    STATUS_ISSUED = "issued"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_ISSUED, "発行"),
        (STATUS_CANCELLED, "取消"),
    ]

    PAYMENT_UNPAID = "unpaid"
    PAYMENT_PARTIAL = "partial"
    PAYMENT_PAID = "paid"
    PAYMENT_LABELS = {PAYMENT_UNPAID: "未入金", PAYMENT_PARTIAL: "一部入金", PAYMENT_PAID: "入金済み"}

    invoice_no = models.CharField("請求書番号", max_length=35, unique=True)
    customer = models.ForeignKey(
        "masters.Partner",
        verbose_name="得意先",
        on_delete=models.PROTECT,
        related_name="invoices",
    )
    issued_on = models.DateField("発行日")
    period_from = models.DateField("締め期間の初め")
    period_to = models.DateField("締め期間の終わり")
    due_on = models.DateField("支払期日", null=True, blank=True)
    subtotal = models.DecimalField("小計", max_digits=14, decimal_places=2)
    tax = models.DecimalField("消費税", max_digits=14, decimal_places=2)
    total = models.DecimalField("合計", max_digits=14, decimal_places=2)
    status = models.CharField(
        "状態", max_length=10, choices=STATUS_CHOICES, default=STATUS_ISSUED
    )
    # 発行した時の会社の情報の写し (core.Company の同じ名前の列と同じ字数)。帳票の会社の欄と「お振込先」の枠が読む。
    issuer_registration_no = models.CharField("発行の時の登録番号", max_length=14, blank=True, default="", editable=False)
    issuer_address = models.CharField("発行の時の住所", max_length=100, blank=True, default="", editable=False)
    issuer_phone = models.CharField("発行の時の電話", max_length=35, blank=True, default="", editable=False)
    issuer_bank_account = models.CharField("発行の時の振込先", max_length=100, blank=True, default="", editable=False)
    # 発行した時の得意先の名前の写し (masters.Partner.name と同じ字数)。帳票の宛名が読む。
    customer_name = models.CharField("得意先の名前", max_length=100, blank=True, default="", editable=False)
    # 発行した時の消費税の率の写し (百分率。10.00 = 10%。purchasing.SelfInvoice.tax_rate と同じ形)。
    # services はいつも billing.services.consumption_tax_rate() を渡す。既定は services を通さずに作る試験のため。
    tax_rate = models.DecimalField("税率 (%)", max_digits=5, decimal_places=2, default=Decimal("10.00"), editable=False)
    # 前回の残の写し (会社の設定 invoice_previous_balance が「載せる」のときだけ、billing.services._issue_invoice が作る時に書く。
    # 同じ得意先の発行の請求書のうち、期間の終わりがこの請求書の期間の初めより前の物の、作る時の残の合計)。設定が「載せない」の間に作った請求書は空。
    previous_balance = models.DecimalField("前回の残", max_digits=14, decimal_places=2, null=True, blank=True, editable=False)
    # 前回の残を数えた日時 (検査の層の R36 が、数えた入金と調整を見分けるのに読む。仕組みのための列)。
    previous_balance_at = models.DateTimeField("前回の残を数えた日時", null=True, blank=True, editable=False)

    #: 変更の履歴 (core.history。作者の決めの伝票の「請求書」) を残し、詳細に「履歴」のタブを出す
    keeps_history = True
    keeps_report_copy = True
    #: 詳細と小窓の項目の一覧に出さない列 (発行の時の写し。帳票のための列で、入力の欄に無い。core.views.hidden_detail_fields)
    detail_hidden_fields = (
        "issuer_registration_no",
        "issuer_address",
        "issuer_phone",
        "issuer_bank_account",
        "customer_name",
        "tax_rate",
        "previous_balance",
        "previous_balance_at",
    )
    #: 変更の履歴に値を残さない列 (直したことと見出しだけ。core.models.Company の振込先と同じ)
    history_hidden_values = ("issuer_bank_account",)
    #: 変更の履歴に残さない列 (前回の残を数えた日時。仕組みのための列)
    history_skip = ("previous_balance_at",)

    class Meta:
        verbose_name = "請求書"
        verbose_name_plural = "請求書"
        ordering = ["-issued_on", "-id"]

    def __str__(self):
        return self.invoice_no

    def paid_total(self) -> Decimal:
        """取消でない入金の金額の合計 (Python で足す。self.payments.all() を絞るので、一覧の prefetch_related が効く)。"""
        return sum((row.amount for row in self.payments.all() if row.status == Payment.STATUS_RECORDED), Decimal("0"))

    def adjusted_total(self) -> Decimal:
        """取消でない調整の金額の合計 (Python で足す。self.adjustments.all() を絞るので、一覧の prefetch_related が効く。残を減らす調整はマイナス)。"""
        return sum(
            (row.amount for row in self.adjustments.all() if row.status == Adjustment.STATUS_RECORDED), Decimal("0")
        )

    def balance(self) -> Decimal:
        """残 = 合計 − 入金済み + 調整 (取消でない調整の合計。残を減らす調整はマイナス)。多い入金ならマイナス (止めない)。

        一覧の「残のある物だけ」(billing.views._unpaid_only)・ダッシュボードの「入金の残っている請求書」(core.dashboard._invoices_to_be_paid)・
        得意先ごとの残 (billing.services.with_receivables) は、同じ式を問い合わせの足し引きで数える。
        """
        return self.total - self.paid_total() + self.adjusted_total()

    def get_balance_display(self) -> Decimal | None:
        """一覧の「残」の欄の値 (画面と CSV の書き出し。core の display_value が get_<名前>_display を先に読む)。
        取消の請求書は None (CSV は空の欄。画面は billing/_row.html が「—」を出す)。ほかは balance()。
        """
        if self.status == self.STATUS_CANCELLED:
            return None
        return self.balance()

    def payment_status(self) -> str:
        """入金の状態。合計が 0 以下 (一式の途中の出荷だけを入れた請求書など) なら入金済み (残は 0 以下で、入金を待つ物ではない。作者の決め)。
        ほかは、残 (調整を数えた残) が 0 以下なら入金済み、入金済みが 0 なら未入金、ほかは一部入金。請求書の状態 (発行 / 取消) は見ない。
        調整だけで残が 0 になった請求書 (入金なし・調整 = −合計) は入金済み。調整の無い請求書の答えは、見る順を変える前と同じ。
        """
        if self.total <= 0:
            return self.PAYMENT_PAID
        if self.balance() <= 0:
            return self.PAYMENT_PAID
        if self.paid_total() == 0:
            return self.PAYMENT_UNPAID
        return self.PAYMENT_PARTIAL

    def payment_status_label(self) -> str:
        """入金の状態の字 (一覧の「入金」の列と、詳細の「入金」の枠の印)。

        取消の請求書は「—」(状態の payment_status は請求書の状態を見ないまま = 開発の記録 docs/nights/20261103/N56.md の 1-1)。
        """
        if self.status == self.STATUS_CANCELLED:
            return "—"
        return self.PAYMENT_LABELS[self.payment_status()]


class InvoiceLine(models.Model):
    """請求書の明細。出荷の行 (出荷 1 つに 1 行) か、一式の行 (出荷に結び付かない行。作者の決め)。どちらも受注の明細を指す。
    数量・単価・単価の単位・金額は作ったときの写し。中小企業共通 EDI の明細行類型コードは、どちらも既定の 210 (資産譲渡請求明細行) に
    当たるので列に持たない。
    発行した時の部品の品番 (part_no)・品名 (item_name)、受注の明細の単位 (unit)、受注の得意先の注文番号 (customer_order_no) も写して持つ
    (書くのは billing.services._issue_invoice の 1 か所。得意先の注文番号は受注の列だが、明細ごとに受注が違いうるので明細に写す)。
    帳票 (billing.reports.invoice_context) と単価の後ろの単位の字 (price_unit_text) は写しだけを読み、写しが空でも今の値で補わない。
    migration (0007) の前に作った明細の写しは、migration の時の今の値で埋めた (今ある行には今の値しか入れられない。発行の時の値は残っていない)。
    写しの列は入力の欄と CSV の見出しに出さない (editable=False)。
    訳: 明細の品番・品名・単位 (受注の明細の単位 = 注文で合意した字)・得意先の注文番号を写して持ち、刷り直しても渡した時と同じ字を出すため
    (請求書の会社の情報・得意先の名前・税率の写しは Invoice の訳のとおり)。マスターや受注を後から直すと刷り直しの字が変わるため。
    部品の単位は後から直されることがあるため、受注の明細から写す。納品書は会期中は写さない (shipping の migration が増えるため)。
    出所 = 本人の決め 11-07 23:4x (今回は請求書だけ) と 11-08 00:0x (単位・注文番号)。変えてよい条件 = 本人が決め直したとき (納品書も写すと決めたとき)。

    出荷の行は、発行した時の出荷の出荷番号 (shipment_no) と出荷日 (shipped_at) も写して持つ (書くのは billing.services._issue_invoice の 1 か所。
    一式の行は空のまま)。出荷を消すと、明細の出荷の外部キーは空になり (SET_NULL)、写しが残る (出荷を消した出荷の行)。
    一式の行かどうかは最後の出荷日 (last_shipped_at) があるかで見る (is_lump。出荷が空かでは見ない。出荷を消した出荷の行も出荷が空のため)。
    表と印刷の「出荷番号」「出荷日」の欄 (ref_no・shipped_on) は、出荷の行なら写しを読む (出荷を消した行も同じ字が出る)。
    migration (0008) の前に作った明細の出荷番号と出荷日の写しは、migration の時の今の出荷の値で埋めた (発行の時の値は残っていない)。
    訳: 請求書に入った出荷を消しても、請求書の明細の出荷番号と出荷日が残り、刷り直しても渡した時と同じ字を出すため。
    出荷を消せるようにしたのは、請求済みの出荷の直しと削除を止めない本人の決め 11-11 12:5x〜13:0x による (知らせるのは検査の層の R27)。
    出所 = 本人の決め 11-11 12:5x〜13:0x と、社員を消すときに名前と番号を実績に写す形 (N401)。変えてよい条件 = 本人が決め直したとき。
    """

    invoice = models.ForeignKey(
        Invoice,
        verbose_name="請求書",
        on_delete=models.CASCADE,
        related_name="lines",
    )
    # 空 = 一式の行 (出荷に結び付かない行。作者の決め) か、出荷を消した出荷の行 (出荷番号と出荷日の写しが残る)。
    # 出荷を消すと空になる (SET_NULL。請求書に入った出荷も消せる = 本人の決め 11-11 12:5x〜13:0x)。
    shipment = models.ForeignKey(
        "shipping.Shipment",
        verbose_name="出荷",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoice_lines",
    )
    # 明細が指す受注の明細。出荷の行は出荷の受注の明細と同じ。中小企業共通 EDI の請求の明細行参照 (受注書ID・受注書明細行ID・注文書ID) を、
    # 受注番号・行番号・得意先の注文番号で引く。
    order_line = models.ForeignKey(
        "orders.OrderLine",
        verbose_name="受注の明細",
        on_delete=models.PROTECT,
        related_name="invoice_lines",
    )
    qty = models.DecimalField("数量", max_digits=12, decimal_places=2)
    unit_price = models.DecimalField("単価", max_digits=14, decimal_places=2)
    # 請求書を作った時の受注の明細の単価の単位の写し (作者の決め)。一式の行は式。
    price_basis = models.CharField("単価の単位", max_length=10, choices=PRICE_BASIS_CHOICES, default=PRICE_BASIS_UNIT)
    amount = models.DecimalField("金額", max_digits=14, decimal_places=2)
    # 一式の行だけ: その受注の明細の、出荷の最後の出荷日 (中小企業共通 EDI の明細行取引終了日に当たる。
    # インボイス制度の適格請求書に載せる取引の日を行に残す)。出荷の行は空 (出荷の出荷日を読む)。
    last_shipped_at = models.DateField("最後の出荷日", null=True, blank=True)
    # 発行した時の部品の品番と品名の写し (masters.Item の part_no・name と同じ字数)。
    part_no = models.CharField("品番", max_length=35, blank=True, default="", editable=False)
    item_name = models.CharField("品名", max_length=100, blank=True, default="", editable=False)
    # 発行した時の受注の明細の単位の写し (orders.OrderLine.unit と同じ字数。注文で合意した字。空なら刷る時に price_basis_text が「個」にする)。
    unit = models.CharField("単位", max_length=10, blank=True, default="", editable=False)
    # 発行した時の受注の得意先の注文番号の写し (orders.Order.customer_order_no と同じ字数)。
    customer_order_no = models.CharField("得意先の注文番号", max_length=35, blank=True, default="", editable=False)
    # 出荷の行だけ: 発行した時の出荷の出荷番号と出荷日の写し (shipping.Shipment の shipment_no・shipped_at と同じ字数と型)。一式の行は空。
    shipment_no = models.CharField("出荷番号", max_length=35, blank=True, default="", editable=False)
    shipped_at = models.DateField("出荷日", null=True, blank=True, editable=False)

    #: 変更の履歴は請求書の詳細の「履歴」に出す (core.history。作者の決めの伝票の「請求書」)
    history_owner = "invoice"

    class Meta:
        verbose_name = "請求書の明細"
        verbose_name_plural = "請求書の明細"
        ordering = ["invoice", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gt=0),
                name="billing_invoiceline_qty_positive",
            ),
            # 1 つの出荷を発行の請求書 2 つに入れない約束は DB の制約にしない
            # (取り消した請求書の出荷は、また請求できるため)。services が行をロックして見る。
            models.UniqueConstraint(
                fields=["invoice", "shipment"],
                name="billing_invoiceline_invoice_shipment_unique",
            ),
            # 出荷の行 (出荷を指すか出荷番号の写しがあり、最後の出荷日は空) か、一式の行 (出荷を指さず、出荷番号の写しが空で、単価の単位が式で、
            # 最後の出荷日がある) のどちらか (作者の決め)。出荷を消した出荷の行は、出荷が空で出荷番号の写しがある (本人の決め 11-11 12:5x〜13:0x)。
            models.CheckConstraint(
                condition=(models.Q(shipment__isnull=False) | ~models.Q(shipment_no="")) & models.Q(last_shipped_at__isnull=True)
                | models.Q(shipment__isnull=True, shipment_no="", last_shipped_at__isnull=False, price_basis=PRICE_BASIS_LUMP),
                name="billing_invoiceline_shipment_or_lump",
            ),
            # 1 枚の請求書に、同じ受注の明細の一式の行 (最後の出荷日がある行) は 1 行だけ (出荷を消した出荷の行を一式の行と数えない)。
            models.UniqueConstraint(
                fields=["invoice", "order_line"],
                condition=models.Q(last_shipped_at__isnull=False),
                name="billing_invoiceline_invoice_lump_unique",
            ),
        ]

    @property
    def price_unit_text(self):
        """単価の後ろに添える字 (読むだけ)。式なら「式」、ほかは発行の時に受注の明細から写した単位、空なら「個」(masters の price_basis_text)。"""
        return price_basis_text(self.price_basis, self.unit)

    @property
    def is_lump(self):
        """一式の行か (読むだけ)。一式の行だけが最後の出荷日を持つ (表の制約 billing_invoiceline_shipment_or_lump)。

        出荷が空かでは見ない (出荷を消した出荷の行も出荷が空のため)。単価の単位でも見ない (一式の受注の明細の出荷の行も単価の単位が式のため)。
        問い合わせで絞るときは last_shipped_at__isnull=False (shipping と orders は billing を import せず、同じ列で見る)。
        """
        return self.last_shipped_at is not None

    @property
    def is_shipment_deleted(self):
        """出荷を消した出荷の行か (読むだけ)。一式の行でなく、出荷が空の行 (出荷番号と出荷日の写しが残る)。"""
        return not self.is_lump and self.shipment_id is None

    @property
    def shipped_on(self):
        """表と印刷の「出荷日」の欄の日付 (読むだけ)。出荷の行は出荷日の写し (出荷を消した行も同じ)、一式の行は最後の出荷日。"""
        if self.is_lump:
            return self.last_shipped_at
        return self.shipped_at

    @property
    def ref_no(self):
        """表と印刷の「出荷番号」の欄の字 (読むだけ)。出荷の行は出荷番号の写し (出荷を消した行も同じ)、一式の行は「受注 <受注番号>-<行番号> の一式」。"""
        if self.is_lump:
            return f"受注 {self.order_line.order.order_no}-{self.order_line.line_no} の一式"
        return self.shipment_no

    def __str__(self):
        return f"{self.invoice.invoice_no} {self.ref_no}"


class Payment(models.Model):
    """入金。請求書に対する入金か、請求書の外で受け取った出荷の入金 (出荷を指す。作者の決め) のどちらか。取消は行を消さず status を取消にして履歴に残す。

    残と状態は Invoice の method が取消でない入金を Python で足して出す (列に持たない)。書くときは billing.services を通す。
    期日 (matures_on) は手形と電子記録債権の期日で、その 2 つの方法では必ず入れる (billing.services._payment_values)。ほかの方法で入れても残す。
    期日と入金日の前後は見ない。依頼書 N409 の前に記録した手形の入金の期日は空のまま (migration で埋めない)。
    """

    METHOD_TRANSFER = "transfer"
    METHOD_CASH = "cash"
    METHOD_BILL = "bill"
    METHOD_E_BOND = "e_bond"
    METHOD_OTHER = "other"
    METHOD_CHOICES = [
        (METHOD_TRANSFER, "振込"),
        (METHOD_CASH, "現金"),
        (METHOD_BILL, "手形"),
        (METHOD_E_BOND, "電子記録債権"),
        (METHOD_OTHER, "その他"),
    ]
    #: 期日 (matures_on) を必ず入れる入金の方法 (手形と電子記録債権。billing.services._payment_values と入金の form が見る)
    METHODS_WITH_MATURITY = (METHOD_BILL, METHOD_E_BOND)

    STATUS_RECORDED = "recorded"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_RECORDED, "記録"),
        (STATUS_CANCELLED, "取消"),
    ]

    # 空 = 請求書の外で受け取った出荷の入金 (shipment を指す。作者の決め)。
    invoice = models.ForeignKey(
        Invoice,
        verbose_name="請求書",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    # 請求書の外で受け取った入金の出荷 (一式でない明細の出荷の「請求しない (別に受け取った)」。作者の決め)。
    # 取消でないこの行があると、その出荷は請求の候補に出ない (billing.services.billable_shipments)。請求書の入金は空。
    shipment = models.ForeignKey(
        "shipping.Shipment",
        verbose_name="出荷",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    received_on = models.DateField("入金日")
    amount = models.DecimalField("金額", max_digits=14, decimal_places=2)
    method = models.CharField(
        "入金の方法", max_length=10, choices=METHOD_CHOICES, default=METHOD_TRANSFER
    )
    # 手形と電子記録債権の期日 (支払期日 Invoice.due_on と別の物。空 = 期日の無い方法か、依頼書 N409 の前に記録した入金)。
    matures_on = models.DateField("期日", null=True, blank=True)
    note = models.CharField("備考", max_length=500, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="記録した人",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    recorded_at = models.DateTimeField("記録の日時", auto_now_add=True)
    status = models.CharField(
        "状態", max_length=10, choices=STATUS_CHOICES, default=STATUS_RECORDED
    )

    #: 変更の履歴 (core.history。作者の決めの伝票の「入金」) を出す詳細。請求書の入金は請求書の詳細の「履歴」に、
    #: 請求書の外で受け取った入金 (請求書が空。作者の決め) は出荷の詳細の「履歴」に出す (値の入った最初の外部キーの先)。
    #: 訳: 請求書の無い入金の持ち主が空のまま履歴を書いて止まらないため。出荷にするのは、その入金が出荷に記録され、
    #: 出荷の詳細の「請求書の外で受け取った入金」の枠に出る物だから
    history_owner = ("invoice", "shipment")

    class Meta:
        verbose_name = "入金"
        verbose_name_plural = "入金"
        ordering = ["-received_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="billing_payment_amount_positive",
            ),
            # 請求書の入金 (請求書を指し、出荷は空) か、請求書の外で受け取った出荷の入金 (出荷を指し、請求書は空) のどちらか (作者の決め)。
            models.CheckConstraint(
                condition=models.Q(invoice__isnull=False, shipment__isnull=True)
                | models.Q(invoice__isnull=True, shipment__isnull=False),
                name="billing_payment_invoice_or_shipment",
            ),
        ]

    def __str__(self):
        if self.invoice_id is None:
            return f"出荷 {self.shipment.shipment_no} {self.received_on:%Y-%m-%d}"
        return f"{self.invoice.invoice_no} {self.received_on:%Y-%m-%d}"



class Adjustment(models.Model):
    """請求書の調整 (値引き・振込手数料・相殺・端数・その他)。請求書を指し、請求書の残を増やすか減らす。残を減らす調整はマイナスで入れる
    (振込手数料 660 を引いて振り込まれたら −660)。金額は 0 でない数。取消は行を消さず status を取消にして履歴に残す (入金と同じ)。
    直すときは取り消して記録し直す (編集の口は作らない)。書くのは billing.services (record_adjustment・cancel_adjustment) だけ。
    残は Invoice の method (balance = 合計 − 入金済み + 調整) が取消でない調整を Python で足して出す (列に持たない)。
    訳: 請求書の残を、値引き・振込手数料・相殺・端数で消すため (残を減らす調整はマイナス)。出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M28)・
    本人の答え (調整を請求書に結ぶ)。変えてよい条件 = 本人が調整の持ち方を決め直したとき。
    """

    REASON_DISCOUNT = "discount"
    REASON_FEE = "fee"
    REASON_OFFSET = "offset"
    REASON_ROUNDING = "rounding"
    REASON_OTHER = "other"
    REASON_CHOICES = [
        (REASON_DISCOUNT, "値引き"),
        (REASON_FEE, "振込手数料"),
        (REASON_OFFSET, "相殺"),
        (REASON_ROUNDING, "端数"),
        (REASON_OTHER, "その他"),
    ]

    STATUS_RECORDED = "recorded"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_RECORDED, "記録"),
        (STATUS_CANCELLED, "取消"),
    ]

    invoice = models.ForeignKey(
        Invoice,
        verbose_name="請求書",
        on_delete=models.PROTECT,
        related_name="adjustments",
    )
    adjusted_on = models.DateField("調整日")
    # 0 でない数。足すも引くも (残を減らす調整はマイナス)。
    amount = models.DecimalField("金額", max_digits=14, decimal_places=2)
    reason = models.CharField("訳", max_length=10, choices=REASON_CHOICES, default=REASON_FEE)
    note = models.CharField("備考", max_length=500, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="記録した人",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    recorded_at = models.DateTimeField("記録の日時", auto_now_add=True)
    status = models.CharField(
        "状態", max_length=10, choices=STATUS_CHOICES, default=STATUS_RECORDED
    )

    #: 変更の履歴 (core.history) は請求書の詳細の「履歴」に出す
    history_owner = "invoice"

    class Meta:
        verbose_name = "調整"
        verbose_name_plural = "調整"
        ordering = ["-adjusted_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(amount=0),
                name="billing_adjustment_amount_not_zero",
            ),
        ]

    def __str__(self):
        return f"{self.invoice.invoice_no} {self.adjusted_on:%Y-%m-%d} {self.get_reason_display()}"
