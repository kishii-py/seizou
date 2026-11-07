from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """利用者。会社のプロジェクトの AUTH_USER_MODEL はいつもこれを指す。"""

    class Meta:
        verbose_name = "利用者"
        verbose_name_plural = "利用者"


class Company(models.Model):
    """会社の設定。1 行だけ。組み立ての命令が書く。

    連番の形と選べる設定は settings の SEIZOU (seizou.core.conf) が持つ。
    端数と税の既定は会社で持ち、取引先ごとの値 (取引先マスター) が優先する。
    消費税は請求書の税率ごとの合計に 1 回だけ計算し (インボイス制度。billing.services._tax)、丸める向きは tax_rounding (既定は切捨)。
    """

    ROUNDING_CHOICES = [
        ("round", "四捨五入"),
        ("ceil", "切上"),
        ("floor", "切捨"),
    ]
    # 消費税の扱いは、請求書の税率ごとの合計に 1 回だけ計算するトータルだけ (決め LAW-2。インボイス制度)。
    # 明細ごとに丸めて足す 2 つの扱いは外し、今の値は core と masters の migration がトータルと消費税の端数の向きに移した。
    TAX_CHOICES = [
        ("total", "トータル"),
    ]

    name = models.CharField("会社名", max_length=100)
    short_name = models.CharField("略称", max_length=35, blank=True)
    theme = models.CharField("画面の色", max_length=35, default="light")
    rounding = models.CharField("端数の扱い", max_length=10, choices=ROUNDING_CHOICES, default="round")
    tax_mode = models.CharField("税の扱い", max_length=10, choices=TAX_CHOICES, default="total")
    # 消費税 (請求書の金額の合計 × 率) を 1 円に丸める向き (決め C95-1b)。既定は切捨。
    # 取引先の同じ名前の欄が空なら、この値に従う。会社の値を変える画面は作らない (会社の端数と同じ。決め C32-1)。
    tax_rounding = models.CharField("消費税の端数の向き", max_length=10, choices=ROUNDING_CHOICES, default="floor")
    address = models.CharField("住所", max_length=100, blank=True)
    phone = models.CharField("電話", max_length=35, blank=True)
    bank_account = models.CharField("振込先", max_length=100, blank=True)
    # 適格請求書発行事業者の登録番号 (決め LAW-3)。インボイス制度 (消費税法の適格請求書等保存方式) では、
    # 適格請求書に、発行する事業者の名前と登録番号を書く。形は T と 13 桁の数字を続けた 14 字 (中小企業共通 EDI の
    # 付表 3 の SIID27「受注者適格請求書発行事業者登録番号」と同じ形)。空 = 登録していない事業者で、請求書に行を出さない。
    # 形の確かめは core.services.check_registration_no (決め C95-2)。出すのは請求書の印刷だけ (core/report_base.html。納品書などの context は鍵を選んで渡すので出ない)。
    registration_no = models.CharField("登録番号", max_length=14, blank=True)
    logo = models.FileField("ロゴ", upload_to="company/", blank=True)

    #: 変更の履歴 (core.history。決め HIST-1 の「会社の設定」) を残す。会社に詳細の画面は無いので、設定の画面の「設定の履歴」に出す
    keeps_history = True
    #: 直したことと見出しだけを残し、前と後の値は残さない列 (振込先の番号を記録に残さない。core.services.update_company の今の決め)
    history_hidden_values = ("bank_account",)

    class Meta:
        verbose_name = "会社"
        verbose_name_plural = "会社"

    def __str__(self):
        return self.name


class NumberSequence(models.Model):
    """連番の今の値 (seizou.core.services.next_number が使う)。

    kind は番号の種類 (order・workorder・shipment・delivery_note・invoice)。
    period は年月 (設定 order_number が ym_seq のとき "2611" の形。seq のときは空)。
    """

    kind = models.CharField("種類", max_length=35)
    period = models.CharField("期間", max_length=10, blank=True, default="")
    last = models.PositiveIntegerField("最後の番号", default=0)

    class Meta:
        verbose_name = "連番"
        verbose_name_plural = "連番"
        constraints = [
            models.UniqueConstraint(fields=["kind", "period"], name="core_numbersequence_kind_period"),
        ]

    def __str__(self):
        return f"{self.kind} {self.period} {self.last}"


class SettingOverride(models.Model):
    """選べる設定の画面からの上書き。seizou.core.conf.setting が答えの YAML より先に読む。

    1 鍵 1 行。変更は core の変更の履歴 (ChangeLog) に残す。logging の 1 行も今のまま書く。値は答えの YAML と同じ形 (真偽・字・字の並び・辞書)。
    """

    key = models.CharField("設定の鍵", max_length=50, unique=True)
    value = models.JSONField("値")
    updated_at = models.DateTimeField("変えた日時", auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="変えた人",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name = "設定の上書き"
        verbose_name_plural = "設定の上書き"
        ordering = ("key",)

    def __str__(self):
        return f"{self.key} = {self.value!r}"


class UserPreference(models.Model):
    """人ごとの表示の設定 (決め 11-05 21:2x。N167)。1 人 1 鍵 1 行。書くのは core.services.set_user_preference だけ (R13)。

    鍵と見出しは core.display にある。今の鍵は上の帯の色 (header_color) だけで、ほかの表示の設定も表を変えずに同じ表へ鍵を足す。
    標準の値は行を持たない (行が無ければ標準)。履歴は持たない。
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+", verbose_name="利用者")
    key = models.CharField("設定の鍵", max_length=64)
    value = models.CharField("値", max_length=200)
    updated_at = models.DateTimeField("変えた日時", auto_now=True)

    class Meta:
        verbose_name = "表示の設定"
        verbose_name_plural = "表示の設定"
        constraints = [
            models.UniqueConstraint(fields=["user", "key"], name="core_userpreference_user_key"),
        ]

    def __str__(self):
        return f"{self.key} = {self.value}"


class ChangeLog(models.Model):
    """変更の履歴 (決め HIST-0・HIST-1)。マスターと伝票を、誰が・いつ・どの項目を・前の値 → 後の値に直したかの 1 行。

    書くのは core.services.record_history と record_history_deleted だけ (R13。UserPreference と同じ形)。読むのは core.history。
    表と行は ContentType でなく字 (model = _meta.label_lower・object_pk・object_text) で持つ。行や表を消しても読めるため。
    owner_model と owner_pk は、この行を出す詳細 (親の無い表は自分。明細などは親。model の history_owner)。
    changes は [{"field", "label", "type", "before", "after"}, …]。値はその時の字で、type は money・number・date・bool・text の 5 つ
    (表示の時に型で描き分ける)。直す道と消す道は作らない。
    """

    ACTION_CREATED = "created"
    ACTION_UPDATED = "updated"
    ACTION_STOPPED = "stopped"
    ACTION_RESUMED = "resumed"
    ACTION_CANCELLED = "cancelled"
    ACTION_DELETED = "deleted"
    ACTION_CHOICES = [
        (ACTION_CREATED, "作った"),
        (ACTION_UPDATED, "直した"),
        (ACTION_STOPPED, "使用停止にした"),
        (ACTION_RESUMED, "使用を再開した"),
        (ACTION_CANCELLED, "取り消した"),
        (ACTION_DELETED, "消した"),
    ]

    created_at = models.DateTimeField("日時", default=timezone.now)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="変えた人",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # その時の人の字。人の無い処理 (受け口) は呼び手が渡した字 (例「<取引先の名前> の seizou」)。どちらも無ければ空 = 画面は「システム」
    actor_label = models.CharField("変えた人の名前", max_length=150, blank=True)
    action = models.CharField("操作", max_length=20, choices=ACTION_CHOICES)
    model = models.CharField("表", max_length=100)
    object_pk = models.PositiveBigIntegerField("行の番号")
    object_text = models.CharField("行の字", max_length=200, blank=True)
    owner_model = models.CharField("出す詳細の表", max_length=100)
    owner_pk = models.PositiveBigIntegerField("出す詳細の行の番号")
    changes = models.JSONField("中身", default=list, blank=True)

    class Meta:
        verbose_name = "変更の履歴"
        verbose_name_plural = "変更の履歴"
        ordering = ("-created_at", "-pk")
        indexes = [models.Index(fields=["owner_model", "owner_pk", "created_at"], name="core_changelog_owner")]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.object_text} {self.get_action_display()}"


class BellOpened(models.Model):
    """上の帯の知らせの鈴を最後に開いた日時 (1 人 1 行。受け渡しの seizou-部品の6案-20261107/指示書.md の 4。本人の決め 11-07 18:2x〜18:4x)。

    鈴の赤い数は、この日時より後に起きた知らせ (受信した注文・自分のフィードバックの直した・見送った・返事) の数 (core.bell)。
    行の無い人 (まだ 1 度も開いていない) は、登録した日時 (date_joined) を代わりに使う。書くのは core.services.open_bell だけ (R13)。
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+", verbose_name="利用者")
    opened_at = models.DateTimeField("鈴を開いた日時", default=timezone.now)

    class Meta:
        verbose_name = "鈴を開いた日時"
        verbose_name_plural = "鈴を開いた日時"

    def __str__(self):
        return f"{self.user} {self.opened_at:%Y-%m-%d %H:%M}"
