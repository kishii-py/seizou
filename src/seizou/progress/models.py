"""進捗の実績の表 (表の設計の決め)。

実績は指示書の工程を指し、1 つの工程に何回でも入れられる。予定を超えた実数もそのまま残す (現物優先)。
実績の数 (qty) は良品の数。不良の数は defect_qty に分けて持つ (作者の決め)。工程の完了は、印 (completes_step) の付いた実績があるかで決める (作者の決め)。
工程ごとの実績の合計・不良の数の合計・完了の印の有る無しは列に持たず、progress.services の recorded_qty・recorded_defect_qty・step_completed で毎回出す。
文字数の上限は固定 (識別子 35・備考 500・コード 10)。
書くときは progress.services を通す。

作業した人 (本人の決め 11-11 09:0x) は、社員 (masters.Staff) への外部キー staff と、名前の写し staff_name・番号の写し staff_code の 3 つで持つ。
社員が居る間は外部キーだけを書き、見せる字は社員の今の番号と名前にする (社員の一覧で名前を直すと前の実績の表示も直る)。
写しを書くのは 2 つのときだけ。社員の一覧に居ない人 (応援) を名前で入れたとき (名前の写しだけ) と、社員を消すとき
(masters.services.delete_staff が progress.services.copy_staff_before_delete を呼び、消す時の名前と番号を写してから外部キーを空にする)。
外部キーは PROTECT なので、services を通らずに社員を消す道は表が止める。見せる字は get_staff_name_display の 1 か所で作る (表に書かない)。

不良の行 (ProgressDefect。見比べのまとめの M09) は、実績の不良を区分ごとに持つ。1 行 = 区分 (masters.DefectKind への外部キー。空でよい)・数・原因・対策。
人が打つのは行だけで、実績の不良の数 (defect_qty) は progress.services がいつも行の数の合計を書く (盤・生産管理ボード・R02 の表の制約は今のまま実績の不良の数を読む)。
不良のある実績には、いつも 1 行以上の不良の行がある。不良の数だけを渡されたとき (区分の一覧を使わない会社) は、区分の無い 1 行を services が作る。
区分を消すときは、masters.services.delete_defect_kind が progress.services.copy_defect_kind_before_delete を呼び、その区分を指す行の全部に
消す時の区分の名前を写し (kind_name) てから外部キーを空にする。外部キーは PROTECT なので、services を通らずに区分を消す道は表が止める。
見せる字は get_kind_name_display の 1 か所で作る (表に書かない)。
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


def _qty_text(value) -> str:
    """数量の字。末尾の 0 を落とす (500.00 → 500、2.50 → 2.5)。stock・purchasing の models と同じ関数 (ほかの部品の model を import しない)。"""
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(dec.normalize(), "f") if dec != dec.to_integral_value() else str(dec.quantize(Decimal(1)))


class Progress(models.Model):
    """進捗の実績。人は services が user を書く (無ければ空)。"""

    workorder_step = models.ForeignKey(
        "workorders.WorkOrderStep",
        verbose_name="指示書の工程",
        on_delete=models.PROTECT,
        related_name="progress_records",
    )
    qty = models.DecimalField("実績の数", max_digits=12, decimal_places=2)
    # 不良の数 (作者の決め)。実績の数 (qty) は良品の数 (作者の決め) なので、不良は分けて持つ。
    defect_qty = models.DecimalField("不良の数", max_digits=12, decimal_places=2, default=Decimal("0"))
    # 工程の完了の印 (作者の決め)。工程の完了は数でなく、この印の付いた実績があるかで決める。初めは付けない (作者の決め)。
    completes_step = models.BooleanField("工程の完了", default=False)
    recorded_at = models.DateTimeField("日時")
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="人",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="progress_records",
    )
    note = models.CharField("備考", max_length=500, blank=True)
    # 作業した人 (本人の決め 11-11 09:0x)。外部キーの先は字で書く (progress の models は masters の models を import しない)。
    # PROTECT: services (masters.services.delete_staff) を通らずに社員を消す道を表が止め、写しの無いまま外部キーだけが空になる道を作らない。
    staff = models.ForeignKey(
        "masters.Staff",
        verbose_name="社員",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="progress_records",
    )
    # 名前の写し。書くのは、社員の一覧に居ない人 (応援) を名前で入れたときと、社員を消すときだけ。
    staff_name = models.CharField("作業した人", max_length=100, blank=True, default="")
    # 社員番号の写し。書くのは、社員を消すときだけ。
    staff_code = models.CharField("社員番号の写し", max_length=10, blank=True, default="")

    #: 変更の履歴は指示書の詳細の「履歴」に出す (core.history が指示書の工程 → 指示書と辿る。作者の決めの伝票の「実績」)。
    #: 実績は指示書の詳細の「進捗」のタブに出る物で、誰がいつ直したか・消したかも同じ詳細で追えるようにするため
    history_owner = "workorder_step.workorder"

    class Meta:
        verbose_name = "進捗の実績"
        verbose_name_plural = "進捗の実績"
        ordering = ["-recorded_at", "-id"]
        # R02 の実績の形「良品の数と不良の数はどちらも 0 以上で、合わせて 0 より大きい」(作者の決め)。form と services を通らずに表へ書いても止める。
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty__gte=0),
                name="progress_progress_qty_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(defect_qty__gte=0),
                name="progress_progress_defect_qty_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(qty__gt=0) | models.Q(defect_qty__gt=0),
                name="progress_progress_qty_or_defect_positive",
            ),
        ]

    def __str__(self):
        recorded_at = timezone.localtime(self.recorded_at).strftime("%Y-%m-%d %H:%M")
        text = f"{self.workorder_step} 実績 {_qty_text(self.qty)} ({recorded_at})"
        worker = self.get_staff_name_display()
        # 指示書の詳細の「進捗」のタブの行はこの字を出す。作業した人の無い実績は今の字のまま。
        return f"{text} 作業 {worker}" if worker else text

    def get_staff_name_display(self) -> str:
        """実績の「作業した人」の見せる字 (一覧の列・CSV・指示書の詳細・知らせの字が、この 1 つの関数で読む。表に書かない)。

        外部キーがあれば「<社員の今の番号> <今の名前>」。空なら写しの「<番号の写し> <名前の写し>」
        (番号の写しが空なら名前の写しだけ。両方が空なら空の字)。土台の display_value は get_<欄の名前>_display を先に見るので、
        一覧の列と CSV の staff_name もこの字になる。
        """
        if self.staff_id is not None:
            return str(self.staff)
        return " ".join(part for part in (self.staff_code, self.staff_name) if part)


#: 区分の外部キーも写しの名前も空の不良の行の見せる字
NO_KIND_TEXT = "区分なし"


class ProgressDefect(models.Model):
    """不良の行 (実績の不良を区分ごとに持つ行。見比べのまとめの M09)。書くのは progress.services だけ。

    実績の不良の数 (Progress.defect_qty) は、この行の数の合計を services が書く (人が合計を別に打たない)。
    区分の名前の写し (kind_name) を書くのは、区分を消すとき (progress.services.copy_defect_kind_before_delete) だけ。
    """

    progress = models.ForeignKey(Progress, verbose_name="実績", on_delete=models.CASCADE, related_name="defects")
    # 外部キーの先は字で書く (progress の models は masters の models を import しない)。
    # PROTECT: services (masters.services.delete_defect_kind) を通らずに区分を消す道を表が止め、写しの無いまま外部キーだけが空になる道を作らない。
    kind = models.ForeignKey(
        "masters.DefectKind",
        verbose_name="不良の区分",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="progress_defects",
    )
    # 区分の名前の写し。書くのは、区分を消すときだけ。
    kind_name = models.CharField("区分の名前の写し", max_length=100, blank=True, default="")
    qty = models.DecimalField("数", max_digits=12, decimal_places=2)
    cause_note = models.CharField("原因", max_length=500, blank=True)
    countermeasure = models.CharField("対策", max_length=500, blank=True)

    #: 変更の履歴は指示書の詳細の「履歴」に出す (実績と同じ所。core.history が実績 → 指示書の工程 → 指示書と辿る)
    history_owner = "progress.workorder_step.workorder"
    #: 変更の履歴に残さない列 (N407b。本体の 11-11 16:3x の答え (2))。区分の名前の写しの字は見せる字の関数 (get_kind_name_display) を読むので、
    #: 履歴では「不良の区分」と同じ字が並ぶだけになる (区分の無い行では写しが無いのに「区分なし」と出る)。区分を消した事は「不良の区分」の行の前後に残る。
    #: 「実績」は残す (指示書の詳細の履歴には実績がいくつも並ぶので、どの実績の不良の行かを示す)。
    history_skip = ("kind_name",)

    class Meta:
        verbose_name = "不良の行"
        verbose_name_plural = "不良の行"
        ordering = ("progress", "pk")
        constraints = [
            # 不良の行の数は 0 より大きい (0 の行は作らず、行を消す)。services を通らずに表へ書いても止める。
            models.CheckConstraint(condition=models.Q(qty__gt=0), name="progress_progressdefect_qty_positive"),
        ]

    def __str__(self):
        return f"{self.get_kind_name_display()} {_qty_text(self.qty)}"

    def get_kind_name_display(self) -> str:
        """不良の行の区分の見せる字 (一覧の列・CSV・知らせの字・変更の履歴が、この 1 つの関数で読む。表に書かない)。

        外部キーがあれば区分の今の名前。空なら写しの名前。両方が空なら「区分なし」。土台の display_value は
        get_<欄の名前>_display を先に見るので、一覧の列と CSV の kind_name もこの字になる。
        """
        if self.kind_id is not None:
            return self.kind.name
        return self.kind_name or NO_KIND_TEXT
