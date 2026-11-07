"""マスターの表 (01 の 4 節)。

文字数の上限は固定 (識別子 35・名称 100・備考 500・コード 10)。
ほかの部品はこの表を外部キーで指してよいが、書くときは masters.services を通す。
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from seizou.core.models import Company
from seizou.core.services import company_or_none

#: 締日と支払日の 31 は月末の印
MONTH_END_DAY = 31


def company_defaults():
    """取引先の端数の扱いが空のときに従う会社の行 (pk の小さい 1 行)。

    読むだけで、行が無ければ保存しない既定の行 (表の既定 = 四捨五入・トータル) を返す。会社の行は作らない。
    取引先の入力の案内の字 (masters.forms.PartnerForm) と、詳細と小窓の端数の行の字 (Partner.get_*_display) が読む。
    """
    return company_or_none() or Company()


def day_label(day):
    """月の中の日の画面の字。31 は「月末」、ほかは「n 日」、空は None。"""
    if day is None:
        return None
    return "月末" if day == MONTH_END_DAY else f"{day} 日"


class Partner(models.Model):
    """取引先。取引先の種類は得意先・協力企業・両方のどれか。

    金額と消費税の端数の扱いが空のときは、会社 (core の Company) の既定に従う。
    支払の条件は 支払の月 (当日・当月・翌月・翌々月) と 支払日 (1〜31。31 は月末) の 2 列。画面には 1 つの字で出す。
    支払の月が当日なら支払日は使わない (締日でまとめる得意先は締日、都度の得意先は納品の日が支払期日)。
    請求の仕方は 締日でまとめる (closing。既定) と 都度 (per_delivery) の 2 つ。中小企業共通 EDI のインボイス仕様編が書く
    合算請求 (締日ごとに 1 枚) と都度請求 (納品書ごとに 1 枚。現金払いなど) を元にした項目。値に EDI のコードの値は使っていない
    (EDI に請求の仕方のコードがあるかは未確認)。
    """

    ROLE_CUSTOMER = "customer"
    ROLE_SUBCONTRACTOR = "subcontractor"
    ROLE_BOTH = "both"
    ROLE_CHOICES = [
        (ROLE_CUSTOMER, "得意先"),
        (ROLE_SUBCONTRACTOR, "協力企業"),
        (ROLE_BOTH, "両方"),
    ]

    BILLING_CLOSING = "closing"
    BILLING_PER_DELIVERY = "per_delivery"
    BILLING_METHOD_CHOICES = [
        (BILLING_CLOSING, "締日でまとめる"),
        (BILLING_PER_DELIVERY, "都度"),
    ]

    PAYMENT_SAME_DAY = "same_day"
    PAYMENT_CURRENT = "current"
    PAYMENT_NEXT = "next"
    PAYMENT_AFTER_NEXT = "after_next"
    PAYMENT_MONTH_CHOICES = [
        (PAYMENT_SAME_DAY, "当日"),
        (PAYMENT_CURRENT, "当月"),
        (PAYMENT_NEXT, "翌月"),
        (PAYMENT_AFTER_NEXT, "翌々月"),
    ]

    code = models.CharField("取引先コード", max_length=10, unique=True)
    name = models.CharField("名前", max_length=100)
    short_name = models.CharField("略称", max_length=100, blank=True)
    role = models.CharField("取引先の種類", max_length=15, choices=ROLE_CHOICES)
    # 締日は空欄か 1〜31 (31 は月末)。
    closing_day = models.PositiveSmallIntegerField(
        "締日",
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(31)],
    )
    # 支払の月は空 (未設定) か 当日・当月・翌月・翌々月。支払日は空か 1〜31 (31 は月末。締日と同じ約束)。当日のとき支払日は使わない。
    payment_month = models.CharField(
        "支払の月", max_length=10, choices=PAYMENT_MONTH_CHOICES, blank=True, default=""
    )
    payment_day = models.PositiveSmallIntegerField(
        "支払日",
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(31)],
    )
    rounding = models.CharField(
        "金額の端数の扱い", max_length=10, choices=Company.ROUNDING_CHOICES, blank=True
    )
    tax_mode = models.CharField(
        "消費税の端数の扱い", max_length=10, choices=Company.TAX_CHOICES, blank=True
    )
    # 請求の仕方 (C02-3c)。締日でまとめる = 締め期間ごとに 1 枚、都度 = 納品書ごとに作れる。今ある取引先は既定の締日でまとめるのまま。
    # 列は端数の 2 列の後に置く (詳細と小窓は列の順に出し、小窓は先頭の 8 項目だけなので、前に置くと小窓から「消費税の端数の扱い」が落ちる)。
    # 入力の欄は締日の前に読む並び (masters.forms.PartnerForm の Meta.fields)。
    billing_method = models.CharField(
        "請求の仕方", max_length=35, choices=BILLING_METHOD_CHOICES, default=BILLING_CLOSING
    )
    note = models.CharField("備考", max_length=500, blank=True)
    # 使用停止 (決め 11-04 22:0x。N135): 外すと候補に出ず、新しい受注と発注に使えない。消さないので、今までの記録からは見える。
    # 編集の画面の欄は form が足す。CSV の見出しと見本には出さない (editable=False)。書くのは masters.services だけ。
    active = models.BooleanField("使用中", default=True, editable=False)

    #: 詳細と小窓の項目の一覧に出さない列 (core の土台が読む)。支払日の行の字 (get_payment_day_display) が
    #: 支払の月を含めて「翌月末」「翌月 25 日」と出すので、「支払の月 翌月」の行と二重になる (初見テスト 5 回目の V98)。
    #: 支払日が空で支払の月だけあるときも、支払日の行が「翌月」と出すので、情報は減らない。一覧の列 (支払日だけ) と同じ見せ方。
    detail_hidden_fields = ("payment_month",)

    class Meta:
        verbose_name = "取引先"
        verbose_name_plural = "取引先"
        ordering = ["code"]

    def __str__(self):
        return self.name

    def get_closing_day_display(self):
        """締日の画面の字 (31 は「月末」、ほかは「n 日」)。表の値は数のまま。一覧と詳細の土台が読む。"""
        return day_label(self.closing_day)

    def get_payment_day_display(self):
        """支払の条件の画面の字。支払の月と合わせて 1 つにする (例「翌月 25 日」「翌月末」)。表の値は 2 列のまま。

        支払の月が当日なら、支払日に関わらず「当日」だけ (支払日は使わない)。
        """
        if self.payment_month == self.PAYMENT_SAME_DAY:
            return self.get_payment_month_display()
        month = self.get_payment_month_display() if self.payment_month else ""
        if self.payment_day is None:
            return month or None
        if self.payment_day == MONTH_END_DAY:
            return f"{month}末" if month else day_label(self.payment_day)
        return f"{month} {day_label(self.payment_day)}" if month else day_label(self.payment_day)

    # 端数の 2 列の画面の字 (詳細と小窓の土台の display_value が読む)。空の行を「—」でなく「会社の既定 (四捨五入)」と出す
    # (初見テスト 4 回目の U118)。自分で書いた get_<列>_display があると Django は choices の自動の関数を付けないので、
    # 値があるときの選択肢の字 (例「切上」) もここで引く。表の値はそのまま。

    def get_rounding_display(self):
        """金額の端数の扱いの画面の字。空なら「会社の既定 (今の会社の値)」。"""
        if self.rounding:
            return dict(Company.ROUNDING_CHOICES).get(self.rounding, self.rounding)
        return f"会社の既定 ({company_defaults().get_rounding_display()})"

    def get_tax_mode_display(self):
        """消費税の端数の扱いの画面の字。空なら「会社の既定 (今の会社の値)」。"""
        if self.tax_mode:
            return dict(Company.TAX_CHOICES).get(self.tax_mode, self.tax_mode)
        return f"会社の既定 ({company_defaults().get_tax_mode_display()})"

    @property
    def is_customer(self):
        return self.role in (self.ROLE_CUSTOMER, self.ROLE_BOTH)

    @property
    def is_subcontractor(self):
        return self.role in (self.ROLE_SUBCONTRACTOR, self.ROLE_BOTH)


class Item(models.Model):
    """部品マスター (部品)。品番は会社の中で一意。"""

    part_no = models.CharField("自社品番", max_length=35, unique=True)
    name = models.CharField("品名", max_length=100)
    drawing_no = models.CharField("図面番号", max_length=35, blank=True)
    drawing = models.FileField("図面ファイル", upload_to="drawings/", blank=True)
    # 図面の表示用の PNG (元が TIFF などのとき、保存のときに 1 回だけ作る)。書くのは masters.services だけ。
    drawing_preview = models.FileField(
        "図面 (画面で見る用の絵)", upload_to="drawings/previews/", blank=True, editable=False
    )
    unit = models.CharField("単位", max_length=10, blank=True)
    standard_price = models.DecimalField(
        "標準単価 (円)", max_digits=14, decimal_places=2, null=True, blank=True
    )
    note = models.CharField("備考", max_length=500, blank=True)
    # 使用停止 (決め 11-04 22:0x。N135): 外すと候補に出ず、新しい受注と発注に使えない。消さないので、今までの記録からは見える。
    # 編集の画面の欄は form が足す。CSV の見出しと見本には出さない (editable=False)。書くのは masters.services だけ。
    active = models.BooleanField("使用中", default=True, editable=False)
    # 図面の表示用の絵を作れなかった理由の 1 文 (空 = 作れた、か作る必要が無い)。書くのは masters.services だけ。
    # 最後に置く (土台の小窓は先頭の 8 項目を出すので、その外に置き、小窓では黄色の 1 行で出す)。
    drawing_preview_error = models.CharField(
        "図面の表示用の誤り", max_length=500, blank=True, editable=False
    )
    # 表示用の PNG のページ数 (複数ページの TIFF の 2 ページ目から後も PNG にする。表示用が無ければ 1)。書くのは masters.services だけ。
    # 入力の画面と CSV の見出しに出さない (editable=False)。小窓の先頭の 8 項目をずらさないように最後に置く。
    drawing_page_count = models.PositiveSmallIntegerField("図面のページ数", default=1, editable=False)

    #: 詳細と小窓の項目の一覧に出さない列 (core の土台が物 1 件ずつ読む)。仕組みのための列で、入力の画面に無い物。
    #: 表示用 (drawing_preview) は、絵がある部品だけ出す (土台の詳細の「ファイル」の節が、この列の行から TIFF の図面の絵を出す)。
    #: 絵の無い部品では「—」の行になり、何の欄か分からないので出さない (初見テスト 3 回目)。
    @property
    def detail_hidden_fields(self):
        hidden = ("drawing_preview_error", "drawing_page_count")
        return hidden if self.drawing_preview else (*hidden, "drawing_preview")

    class Meta:
        verbose_name = "部品"
        verbose_name_plural = "部品"
        ordering = ["part_no"]

    def __str__(self):
        return f"{self.part_no} {self.name}"

    @property
    def display_drawing(self):
        """絵として出す図面のファイル (読むだけ)。出す物が無ければ None。

        表示用の絵を作れなかった (誤りの字がある) ときは None (壊れた絵を出さない)。
        表示用の PNG があればそれ。無ければ元の図面が PNG・JPEG ならそれ。PDF などは None (link だけ)。
        """
        if self.drawing_preview_error:
            return None
        if self.drawing_preview:
            return self.drawing_preview
        if self.drawing and self.drawing.name.lower().endswith((".png", ".jpg", ".jpeg")):
            return self.drawing
        return None

    @property
    def drawing_version_no(self):
        """今の図面の版の番号 (読むだけ)。この部品の版の番号の最大 + 1。版が無ければ 1。"""
        if self.pk is None:
            return 1
        last = self.drawing_versions.aggregate(last=models.Max("version_no"))["last"]
        return (last or 0) + 1

    def file_url(self, field_name):
        """詳細の画面のファイルの URL (core の土台が呼ぶ)。図面と表示用は、ログインした人にだけ返す口の URL。

        ほかの項目と、ファイルの無い項目は None (土台は link を出さない)。
        """
        from .drawings import drawing_url

        if field_name == "drawing" and self.drawing:
            return drawing_url(self, "original")
        if field_name == "drawing_preview" and self.drawing_preview:
            return drawing_url(self, "preview")
        return None

    @property
    def display_drawing_url(self):
        """小窓とビューアの絵の URL (読むだけ)。display_drawing と同じ選び方で、口の URL を返す。出す物が無ければ None。"""
        from .drawings import drawing_url

        if self.drawing_preview_error:
            return None
        if self.drawing_preview:
            return drawing_url(self, "preview")
        if self.drawing and self.drawing.name.lower().endswith((".png", ".jpg", ".jpeg")):
            return drawing_url(self, "original")
        return None


class PartnerItem(models.Model):
    """取引先ごとの品番。取引先の品番を自社の部品に対応させる。"""

    partner = models.ForeignKey(
        Partner,
        verbose_name="取引先",
        on_delete=models.PROTECT,
        related_name="partner_items",
    )
    item = models.ForeignKey(
        Item,
        verbose_name="自社品番",
        on_delete=models.PROTECT,
        related_name="partner_items",
    )
    partner_part_no = models.CharField("取引先の品番", max_length=35)
    partner_item_name = models.CharField("取引先の品名", max_length=100, blank=True)

    class Meta:
        verbose_name = "取引先の品番"
        verbose_name_plural = "取引先の品番"
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "partner_part_no"],
                name="masters_partneritem_partner_part_no_unique",
            ),
        ]

    def __str__(self):
        partner = self.partner.short_name or self.partner.name
        return f"{partner} {self.partner_part_no}"


class Process(models.Model):
    """工程。"""

    code = models.CharField("工程コード", max_length=10, unique=True)
    name = models.CharField("名前", max_length=100)
    is_outsourced = models.BooleanField("協力企業に出すか", default=False)
    sort_order = models.PositiveIntegerField("並び順", default=0)
    use_in_plan = models.BooleanField("計画に使う", default=False)
    # 使用停止 (N140。部品と取引先の N135 と同じ形): 外すと指示書の発行の候補に出ず、部品の標準の工程の並びからも写さない。
    # 消さないので、今までの指示書の工程はそのまま残る。編集の画面の欄は form が足す (editable=False)。書くのは masters.services だけ。
    active = models.BooleanField("使用中", default=True, editable=False)

    class Meta:
        verbose_name = "工程"
        verbose_name_plural = "工程"
        ordering = ["sort_order", "code"]

    def __str__(self):
        return f"{self.code} {self.name}"


class ItemProcess(models.Model):
    """標準の工程の並び。"""

    item = models.ForeignKey(
        Item,
        verbose_name="部品",
        on_delete=models.CASCADE,
        related_name="item_processes",
    )
    process = models.ForeignKey(
        Process,
        verbose_name="工程",
        on_delete=models.PROTECT,
        related_name="item_processes",
    )
    seq = models.PositiveIntegerField("順")

    class Meta:
        verbose_name = "標準の工程の並び"
        verbose_name_plural = "標準の工程の並び"
        ordering = ["item", "seq"]

    def __str__(self):
        return f"{self.item.part_no} {self.process}"


class ItemDrawingVersion(models.Model):
    """図面の版。部品の図面を差し替えたときに、前の図面を版として残す (書くのは masters.services だけ)。

    ファイルは写さず、置き場に残した前の図面の名前の字を持つ (upload_to は使われない)。
    今の図面は部品の行が持ち、その版の番号は「この部品の版の番号の最大 + 1」(Item.drawing_version_no)。
    """

    item = models.ForeignKey(
        Item,
        verbose_name="部品",
        on_delete=models.CASCADE,
        related_name="drawing_versions",
    )
    version_no = models.PositiveIntegerField("版")
    drawing = models.FileField("図面ファイル", upload_to="drawings/versions/")
    drawing_preview = models.FileField("図面の表示用", upload_to="drawings/previews/", blank=True)
    drawing_preview_error = models.CharField("図面の表示用の誤り", max_length=500, blank=True)
    page_count = models.PositiveSmallIntegerField("ページ数", default=1)
    note = models.CharField("備考", max_length=500, blank=True)
    created_at = models.DateTimeField("保存した日時", auto_now_add=True)

    class Meta:
        verbose_name = "図面の版"
        verbose_name_plural = "図面の版"
        ordering = ["item", "-version_no"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "version_no"],
                name="masters_itemdrawingversion_item_version_no_unique",
            ),
        ]

    def __str__(self):
        return f"{self.item.part_no} 版 {self.version_no}"


class Holiday(models.Model):
    """会社の休日 (祝日と会社の休み)。日付は一意。書くのは masters.services だけ。

    定休日の曜日 (土日など) は表に持たず、設定 weekly_holidays が持つ。稼働日の数え方は core.calendar。
    """

    KIND_PUBLIC = "public"
    KIND_COMPANY = "company"
    KIND_CHOICES = [
        (KIND_PUBLIC, "祝日"),
        (KIND_COMPANY, "会社の休み"),
    ]

    date = models.DateField("日付", unique=True)
    name = models.CharField("名前", max_length=100)
    kind = models.CharField("種類", max_length=10, choices=KIND_CHOICES)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="登録した人",
        editable=False,
    )
    created_at = models.DateTimeField("登録した日時", auto_now_add=True)

    class Meta:
        verbose_name = "休日"
        verbose_name_plural = "休日"
        ordering = ("date",)

    def __str__(self):
        return f"{self.date:%Y-%m-%d} {self.name}"
