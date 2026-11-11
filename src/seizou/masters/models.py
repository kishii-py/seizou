"""マスターの表 (表の設計の決め)。

文字数の上限は固定 (識別子 35・名称 100・備考 500・コード 10)。
ほかの部品はこの表を外部キーで指してよいが、書くときは masters.services を通す。
"""

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from seizou.core.models import Company
from seizou.core.services import company_or_none

#: 締日と支払日の 31 は月末の印
MONTH_END_DAY = 31

#: 単価の単位 (作者の決め)。部品の数量の単位 1 つあたり (unit) か、一式 (lump。金額に数量を掛けない) か。
#: 受注と発注の明細の単価の単位 (作者の決め) も同じ値と字を使う。中小企業共通 EDI の数量単位コード (付表 4) に一式に当たる値は無い
#: (SET「セット」は数える組の数で、数量を掛けない一式とは違う) ので、会社どうしの JSON では seizou の項目として送る (作者の決め)。
#: 部品の単位のときは、EDI の単価基準数量が空か 1 (定貫品目) のときと同じ意味。
PRICE_BASIS_UNIT = "unit"
PRICE_BASIS_LUMP = "lump"
PRICE_BASIS_CHOICES = [(PRICE_BASIS_UNIT, "部品の単位"), (PRICE_BASIS_LUMP, "式")]


def company_defaults():
    """取引先の端数の扱いが空のときに従う会社の行 (pk の小さい 1 行)。

    読むだけで、行が無ければ保存しない既定の行 (表の既定 = 四捨五入・トータル・切捨) を返す。会社の行は作らない。
    取引先の入力の案内の字 (masters.forms.PartnerForm) と、詳細と小窓の端数の行の字 (Partner.get_*_display) が読む。
    """
    return company_or_none() or Company()


def price_basis_text(price_basis, unit):
    """単価の後ろに添える単位の字 (読むだけ)。式なら「式」、部品の単位なら unit の字、unit が空なら「個」。

    画面の「¥250 / 個」「¥50,000 / 式」の後ろの字。単位が空の部品は個で数える (中小企業共通 EDI は数量単位コードを指定しない品物を
    定貫品目 = 数えられる品目と見なす = 本編 7.3.2 (4) 数量単位コード。付表 4 の H87 の名前は「個<デフォルト>」)。
    受注と発注の明細の単価の字 (作者の決め) と、同じ条件の単価の行の断りの字 (masters.services) も使う。
    """
    if price_basis == PRICE_BASIS_LUMP:
        return "式"
    return unit or "個"


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
    # 消費税を 1 円に丸める向き (作者の決め)。空なら会社の既定 (Company.tax_rounding。既定は切捨) に従う。
    tax_rounding = models.CharField(
        "消費税の端数の向き", max_length=10, choices=Company.ROUNDING_CHOICES, blank=True
    )
    # 請求の仕方 (作者の決め)。締日でまとめる = 締め期間ごとに 1 枚、都度 = 納品書ごとに作れる。今ある取引先は既定の締日でまとめるのまま。
    # 列は端数の 3 列の後に置く (詳細と小窓は列の順に出し、小窓は先頭の 8 項目だけなので、前に置くと小窓から「消費税の端数の扱い」が落ちる)。
    # 入力の欄は締日の前に読む並び (masters.forms.PartnerForm の Meta.fields)。
    billing_method = models.CharField(
        "請求の仕方", max_length=35, choices=BILLING_METHOD_CHOICES, default=BILLING_CLOSING
    )
    # 相手の適格請求書発行事業者の登録番号 (中小企業共通 EDI の仕入明細の受注者の登録番号 SIID27 を元にした。インボイス制度)。
    # 仕入明細 (purchasing.SelfInvoice) に相手の番号として写す。名前と字数は会社の登録番号 (core.Company.registration_no) と同じ。
    # 形 (T と 13 桁の数字) は masters.services が core.services.check_registration_no で見る (作者の決め)。空は登録していない相手。
    registration_no = models.CharField("登録番号", max_length=14, blank=True)
    note = models.CharField("備考", max_length=500, blank=True)
    # 使用停止 (決め 11-04 22:0x。開発の記録 docs/nights/20261104/N135.md): 外すと候補に出ず、新しい受注と発注に使えない。消さないので、今までの記録からは見える。
    # 編集の画面の欄は form が足す。CSV の見出しと見本には出さない (editable=False)。書くのは masters.services だけ。
    active = models.BooleanField("使用中", default=True, editable=False)

    #: 詳細と小窓の項目の一覧に出さない列 (core の土台が読む)。支払日の行の字 (get_payment_day_display) が
    #: 支払の月を含めて「翌月末」「翌月 25 日」と出すので、「支払の月 翌月」の行と二重になる (作者の決め)。
    #: 支払日が空で支払の月だけあるときも、支払日の行が「翌月」と出すので、情報は減らない。一覧の列 (支払日だけ) と同じ見せ方。
    detail_hidden_fields = ("payment_month",)
    #: 変更の履歴 (core.history。作者の決め) を残し、詳細に「履歴」のタブを出す
    keeps_history = True

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

        取引先の入力の画面の JS (masters/static/masters/partner-form.js) が同じ分かれで字を作る。分かれを変えたら JS も直す。
        """
        if self.payment_month == self.PAYMENT_SAME_DAY:
            return self.get_payment_month_display()
        month = self.get_payment_month_display() if self.payment_month else ""
        if self.payment_day is None:
            return month or None
        if self.payment_day == MONTH_END_DAY:
            return f"{month}末" if month else day_label(self.payment_day)
        return f"{month} {day_label(self.payment_day)}" if month else day_label(self.payment_day)

    # 端数の 3 列の画面の字 (詳細と小窓の土台の display_value が読む)。空の行を「—」でなく「会社の既定 (四捨五入)」と出す
    # (作者の決め)。自分で書いた get_<列>_display があると Django は choices の自動の関数を付けないので、
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

    def get_tax_rounding_display(self):
        """消費税の端数の向きの画面の字。空なら「会社の既定 (今の会社の値)」。"""
        if self.tax_rounding:
            return dict(Company.ROUNDING_CHOICES).get(self.tax_rounding, self.tax_rounding)
        return f"会社の既定 ({company_defaults().get_tax_rounding_display()})"

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
    # 使用停止 (決め 11-04 22:0x。開発の記録 docs/nights/20261104/N135.md): 外すと候補に出ず、新しい受注と発注に使えない。消さないので、今までの記録からは見える。
    # 編集の画面の欄は form が足す。CSV の見出しと見本には出さない (editable=False)。書くのは masters.services だけ。
    active = models.BooleanField("使用中", default=True, editable=False)
    # 図面の表示用の絵を作れなかった理由の 1 文 (空 = 作れた、か作る必要が無い)。書くのは masters.services だけ。
    # 最後に置く (土台の小窓は先頭の 8 項目を出すので、その外に置き、小窓では黄色の 1 行で出す)。
    drawing_preview_error = models.CharField(
        "図面の表示用の誤り", max_length=500, blank=True, editable=False
    )
    # 単位の数量単位コード (作者の決め。値は QuantityUnit のコード)。書くのは masters.services だけで、単位の名前 (unit) から引く。
    # 一覧に無い単位の字 (移す前の字) は空 (unit_unlisted)。入力の画面と CSV の見出しに出さない (editable=False。CSV は「単位」の名前の列で読む)。
    # 置き場: 小窓の先頭の 8 項目をずらさず (隠す列の drawing_preview_error の後)、最後の列は drawing_page_count のまま。
    unit_code = models.CharField("数量単位コード", max_length=10, blank=True, editable=False)
    # 表示用の PNG のページ数 (複数ページの TIFF の 2 ページ目から後も PNG にする。表示用が無ければ 1)。書くのは masters.services だけ。
    # 入力の画面と CSV の見出しに出さない (editable=False)。小窓の先頭の 8 項目をずらさないように最後に置く。
    drawing_page_count = models.PositiveSmallIntegerField("図面のページ数", default=1, editable=False)

    #: 詳細と小窓の項目の一覧に出さない列 (core の土台が物 1 件ずつ読む)。仕組みのための列で、入力の画面に無い物。
    #: 表示用 (drawing_preview) は、絵がある部品だけ出す (土台の詳細の「ファイル」の節が、この列の行から TIFF の図面の絵を出す)。
    #: 絵の無い部品では「—」の行になり、何の欄か分からないので出さない (作者の決め)。
    @property
    def detail_hidden_fields(self):
        hidden = ("drawing_preview_error", "drawing_page_count")
        return hidden if self.drawing_preview else (*hidden, "drawing_preview")

    #: 変更の履歴 (core.history。作者の決め) を残し、詳細に「履歴」のタブを出す
    keeps_history = True
    #: 変更の履歴に残さない列 (表示用の図面の 3 列。仕組みのための列で、利用者の欄でない。detail_hidden_fields と同じ列)
    history_skip = ("drawing_preview", "drawing_preview_error", "drawing_page_count")

    class Meta:
        verbose_name = "部品"
        verbose_name_plural = "部品"
        ordering = ["part_no"]

    def __str__(self):
        return f"{self.part_no} {self.name}"

    @property
    def unit_unlisted(self):
        """単位の字があるのに数量単位コードが無い (一覧に無い字のまま。作者の決めの「当たらない字は画面で選んでもらう」)。

        詳細と小窓の黄色の 1 行と、入力の画面の欄の下の案内が読む。一覧の絞りは同じ条件を表の上で見る (masters.views)。
        """
        return bool(self.unit) and not self.unit_code

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
    """部品の取引先。部品と取引先の組を 1 行で持つ。取引先の品番は空でもよい。

    品番の空の行は、その取引先と取引する登録としてだけ使い、受け入れと読み取りの品番の自動のつなぎ
    (masters.services.resolve_partner_item) には使わない (作者の決め)。品番の空の行は取引先と部品ごとに 1 つ (作者の決め)。
    取引先の品番は、得意先の行では中小企業共通 EDI の発注者品目ID、協力企業の行では受注者品目ID に当たる
    (項目を元にした。付表 1 の注文の明細の UN01005813 と UN01005812。取引先の種類が両方の行は、その注文の向きで決まる)。
    単価の行 (ItemPrice) はこの行に付く。見積書はこの行に添付ファイルで付ける (作者の決め)。書くのは masters.services だけ。
    """

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
    # 見出しの字「取引先の品番」と字数 35 は変えない (core.demo の種の読み込みが見出しの字から欄を引く。35 は識別子の上限)。
    partner_part_no = models.CharField("取引先の品番", max_length=35, blank=True, default="")
    partner_item_name = models.CharField("取引先の品名", max_length=100, blank=True)

    #: 変更の履歴は部品の詳細の「履歴」に出す (core.history。作者の決めの「部品の取引先と単価」)
    history_owner = "item"

    class Meta:
        verbose_name = "部品の取引先"
        verbose_name_plural = "部品の取引先"
        constraints = [
            # 同じ取引先の同じ品番は 1 つ。品番が空の行は、同じ取引先でも部品ごとにできるので、空の字どうしを同じ品番と見ない。
            models.UniqueConstraint(
                fields=["partner", "partner_part_no"],
                condition=~models.Q(partner_part_no=""),
                name="masters_partneritem_partner_part_no_unique",
            ),
            # 品番が空の行は取引先と部品ごとに 1 つ (作者の決め)。断りの字は services が出す。
            # 品番が空の 2 行は画面で見分けがつかず、それぞれに同じ条件の単価の行を足せて、
            # 同じ条件の単価の行を 1 つにする決め (作者の決め) が行をまたいで崩れるため。services を通らない書き込みと、2 つの要求が重なった時も止める。
            # (取引先, 部品) の組を全部 1 行にする決まりは足さない (1 つの取引先が 1 つの部品を 2 つの品番で呼ぶ行は今も持てる)。
            models.UniqueConstraint(
                fields=["partner", "item"],
                condition=models.Q(partner_part_no=""),
                name="masters_partneritem_partner_item_blank_unique",
            ),
        ]

    def __str__(self):
        """品番のある行は「略称 取引先の品番」、品番の空の行は「略称 (自社品番 P-1)」。字に金額を入れない。

        訳: 添付ファイルの一覧の「付けた先」と削除の確かめに、品番の空の行も読める字で出すため。
        """
        partner = self.partner.short_name or self.partner.name
        if not self.partner_part_no:
            return f"{partner} (自社品番 {self.item.part_no})"
        return f"{partner} {self.partner_part_no}"


class Process(models.Model):
    """工程。"""

    code = models.CharField("工程コード", max_length=10, unique=True)
    name = models.CharField("名前", max_length=100)
    is_outsourced = models.BooleanField("協力企業に出すか", default=False)
    sort_order = models.PositiveIntegerField("並び順", default=0)
    use_in_plan = models.BooleanField("計画に使う", default=False)
    # 使用停止 (開発の記録 docs/nights/20261104/N140.md。部品と取引先の開発の記録 docs/nights/20261104/N135.md と同じ形): 外すと指示書の発行の候補に出ず、部品の標準の工程の並びからも写さない。
    # 消さないので、今までの指示書の工程はそのまま残る。編集の画面の欄は form が足す (editable=False)。書くのは masters.services だけ。
    active = models.BooleanField("使用中", default=True, editable=False)

    #: 変更の履歴 (core.history。作者の決め) を残し、詳細に「履歴」のタブを出す
    keeps_history = True

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


class ItemPrice(models.Model):
    """単価の行 (単価表)。部品ごとに、取引先の行 (部品の取引先) か共通の、売りか協力企業への発注の単価を持つ (作者の決め)。

    1 行 = 部品・取引先の行 (空 = 共通。どの取引先にも同じ単価)・売りか発注か・協力企業の工程 (発注の行だけ。空 = 工程によらない)・
    数量の下限 (空 = 数量によらない。帯の終わりは次の行の下限の 1 つ手前 = 作者の決め)・単価・単価の単位 (部品の単位か式 = 作者の決め)・
    適用開始日 (空 = いつでも効く。改定は行を直さず、新しい適用開始日の行を足す = 作者の決め)・見積の番号と発行日と有効期限・備考。

    中小企業共通 EDI の項目を元にした欄: 見積の番号 = 見積回答書ID (UN01008345)・見積の発行日 = 見積回答書発行日 (UN01008352)・
    見積の有効期限 = 見積有効期限 (UN01005613。空 = 期限なし)・単価 = 見積回答単価 (UN01005792。1 単位あたりの単価で、式の行は当たらない)。
    EDI に無い seizou の項目: 売りか発注か・共通・数量の下限・適用開始日・単価の単位の式 (作者の決め)。

    表の制約は 3 つ: 単価は 0 より大きい・数量の下限は空か 0 より大きい (作者の決め)・同じ条件の行は 1 つ (作者の決め)。
    断りの字は masters.services が出す (表の制約は services を通らない書き込みと、2 つの要求が重なった時の守り)。
    書くのは masters.services だけ。
    """

    KIND_SALE = "sale"
    KIND_PURCHASE = "purchase"
    KIND_CHOICES = [
        (KIND_SALE, "売り"),
        (KIND_PURCHASE, "協力企業への発注"),
    ]

    # 全部の行が部品を持つ (共通の行も部品を指す。読むのはいつも部品から)
    item = models.ForeignKey(Item, verbose_name="部品", on_delete=models.PROTECT, related_name="prices")
    # 空 = 共通 (作者の決め)。取引先の行を消すときは services.delete_partner_item が先にこの行を消す (作者の決め)。
    # PROTECT の訳: 表で一緒に消す (CASCADE) と、services を通らない消し方でも単価の行が黙って消え、変更の履歴が行ごとに消した事を残せないため。
    partner_item = models.ForeignKey(
        PartnerItem, verbose_name="取引先", on_delete=models.PROTECT, null=True, blank=True, related_name="prices"
    )
    kind = models.CharField("売りか発注か", max_length=10, choices=KIND_CHOICES)
    # 協力企業への発注の行だけが持つ (空 = 工程によらない。作者の決め)。売りの行は services が空にそろえる。
    process = models.ForeignKey(
        Process, verbose_name="協力企業の工程", on_delete=models.PROTECT, null=True, blank=True, related_name="item_prices"
    )
    # この数量から効く (空 = 数量によらない)。桁は受注の明細の数量と同じ。
    min_qty = models.DecimalField("数量の下限", max_digits=12, decimal_places=2, null=True, blank=True)
    # 名前を unit_price にする訳: 金額の項目の名前の集まり (core.access.MONEY_FIELDS) に入り、金額の閲覧の無い人には「—」になるため。
    unit_price = models.DecimalField("単価", max_digits=14, decimal_places=2)
    price_basis = models.CharField("単価の単位", max_length=10, choices=PRICE_BASIS_CHOICES, default=PRICE_BASIS_UNIT)
    start_date = models.DateField("適用開始日", null=True, blank=True)
    quote_no = models.CharField("見積の番号", max_length=35, blank=True)
    quote_date = models.DateField("見積の発行日", null=True, blank=True)
    quote_expires = models.DateField("見積の有効期限", null=True, blank=True)
    note = models.CharField("備考", max_length=500, blank=True)

    #: 変更の履歴は部品の詳細の「履歴」に出す (core.history。作者の決めの「部品の取引先と単価」)
    history_owner = "item"

    class Meta:
        verbose_name = "単価の行"
        verbose_name_plural = "単価表"
        constraints = [
            # 単価と数量の下限は 0 より大きい (作者の決め)。数量の下限の空は「数量によらない」で通す。
            models.CheckConstraint(condition=models.Q(unit_price__gt=0), name="masters_itemprice_unit_price_positive"),
            models.CheckConstraint(
                condition=models.Q(min_qty__isnull=True) | models.Q(min_qty__gt=0),
                name="masters_itemprice_min_qty_positive",
            ),
            # 同じ条件 (部品・取引先の行か共通・売りか発注か・工程・数量の下限・適用開始日) の単価の行は 1 つ (作者の決め)。断りの字は services が出す。
            # 空 (NULL) の欄どうしを同じ値と見るため、空を表に無い値 (外部キーは 0・数量の下限は 0・日付は 1 年 1 月 1 日) に置き換えた式で一意にする。
            # fields だけの UniqueConstraint は空の欄を別々の値と見るので、共通の行・工程の空・下限の空・開始日の空の重なりを止めない。
            # nulls_distinct=False は SQLite が対応せず、制約が作られない (Django の警告 models.W047)。
            # 置き換えの値 0 は、数量の下限が 0 より大きい (上の制約) ので本当の値と重ならない。
            models.UniqueConstraint(
                "item",
                Coalesce("partner_item", Value(0), output_field=models.IntegerField()),
                "kind",
                Coalesce("process", Value(0), output_field=models.IntegerField()),
                Coalesce("min_qty", Value(Decimal("0")), output_field=models.DecimalField(max_digits=12, decimal_places=2)),
                Coalesce("start_date", Value(date(1, 1, 1)), output_field=models.DateField()),
                name="masters_itemprice_same_band_unique",
            ),
        ]

    def __str__(self):
        """「自社品番 取引先の略称か「共通」 売りか発注か」。単価を字に入れない (添付ファイルや削除の確かめの字から金額が漏れないように)。"""
        if self.partner_item_id is None:
            whose = "共通"
        else:
            partner = self.partner_item.partner
            whose = partner.short_name or partner.name
        return f"{self.item.part_no} {whose} {self.get_kind_display()}"

    @property
    def quote_expired(self):
        """見積の有効期限が今日より前なら真 (作者の決めの「期限切れ」の印の元)。期限が空なら偽。読むだけ。"""
        return self.quote_expires is not None and self.quote_expires < timezone.localdate()

    @property
    def unit_text(self):
        """単価の後ろに添える単位の字 (price_basis_text)。読むだけ。"""
        return price_basis_text(self.price_basis, self.item.unit)


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

    #: 変更の履歴 (core.history。作者の決め) を残し、詳細に「履歴」のタブを出す
    keeps_history = True

    class Meta:
        verbose_name = "休日"
        verbose_name_plural = "休日"
        ordering = ("date",)

    def __str__(self):
        return f"{self.date:%Y-%m-%d} {self.name}"


class Staff(models.Model):
    """社員 (現場で実績を入れる人の一覧。ログインのアカウントとは別に持つ。本人の決め 11-11 09:0x)。書くのは masters.services だけ。

    社員番号 (code) は会社の字のまま持ち、後から直せる。同じ人かは照合の字 (code_key) で見る。照合の字は services が code から作る
    (全角を半角にそろえ、前後の空白を落とし、数字だけの番号は先頭の 0 を落とす。英字を含む番号は 0 を落とさず、大文字と小文字も分ける)。
    個人の情報は番号と名前だけにし、入社日・部署・連絡先・ログインのアカウントとのつながりは持たない (本人の決め 11-11 09:0x)。
    辞めた人は消さずに使用停止 (active が偽) にできる。消すときは masters.services.delete_staff が、実績に名前と番号を写してから消す
    (実績の外部キーは progress の側にだけあり、PROTECT)。
    """

    code = models.CharField("社員番号", max_length=10)
    # 照合の字 (「0012」「12」「１２」は同じ「12」)。一意の制約は照合の字に置く (2 人が同時に保存しても同じ番号の社員が 2 人にならない)。
    code_key = models.CharField("照合の字", max_length=10, unique=True, editable=False)
    name = models.CharField("名前", max_length=100)
    note = models.CharField("備考", max_length=500, blank=True)
    # 使用停止 (Process の active と同じ形): 外すと、新しい実績にこの社員の番号を入れたときに、検査の層のポカよけ R28 が知らせる (止めるかは会社の設定 progress_inactive_staff)。消さないので、今までの実績は社員を指したまま残る。
    # 編集の画面の欄は form が足す (editable=False)。書くのは masters.services だけ。
    active = models.BooleanField("使用中", default=True, editable=False)

    #: 詳細の項目の一覧に出さない列 (照合の字。services が社員番号から作る、仕組みのための列で、入力の画面に無い物)
    detail_hidden_fields = ("code_key",)

    #: 変更の履歴 (core.history。作者の決め) を残し、詳細に「履歴」のタブを出す
    keeps_history = True
    #: 変更の履歴に残さない列 (照合の字。社員番号を直したことは「社員番号」の行に残る。detail_hidden_fields と同じ列)
    history_skip = ("code_key",)

    class Meta:
        verbose_name = "社員"
        verbose_name_plural = "社員"
        ordering = ("code", "pk")

    def __str__(self):
        return f"{self.code} {self.name}"


class QuantityUnit(models.Model):
    """数量の単位の一覧 (読むだけの表。作者の決め)。

    中小企業共通 EDI の付表 4 (識別コード定義表) の「数量単位コード」のシートの、国際サブセット定義表 15 行と
    ＜参考＞の表 25 行の、コードと日本語の名前の項目を元にした。コードは国連 CEFACT の数量単位コード (UNECE Rec.20) の
    値のまま、部品と明細の表と会社どうしの JSON に持つ。中小企業共通 EDI では、数量単位コードは課税分類コードと並ぶ
    実装必須の識別コード (本編 3.1.3 識別コード)。
    行は migration だけが入れる。画面から足さない・直さない (部品の単位の欄の候補の口 masters:quantityunit_select が読む)。
    名前の上限は 10 字 (部品と明細の単位の列 (10 字) にそのまま入れるため)。
    品目数量単位区分 (定貫・不定貫) の列は持たない (今は使う所が無い)。
    """

    code = models.CharField("数量単位コード", max_length=10, primary_key=True)
    name = models.CharField("名前", max_length=10, unique=True)
    sort_order = models.PositiveSmallIntegerField("並び順")

    class Meta:
        verbose_name = "数量の単位"
        verbose_name_plural = "数量の単位"
        ordering = ["sort_order"]

    def __str__(self):
        return self.name
