"""progress の入力の form。保存は form でなく services が行う (土台が form の save を呼ぶことは無い)。

土台は service_kwargs() の辞書を検査の層の data に使い、同じ辞書で
services.record_progress(workorder_step=..., qty=..., recorded_at=..., user=..., note=..., defect_qty=..., completes_step=..., staff=..., staff_name=...) を呼ぶ (部品の公開する関数の設計の決め)。
ほかの部品の model は import せず、外部キーの先として引く (読むだけ)。社員は masters の services (staff_by_code・has_active_staff) で引く。

作業した人の 2 つの欄 (本人の決め 11-11 09:0x。2 段目 N401b): 「社員番号」(staff_code) と「社員の一覧に居ない人の名前」(staff_name)。
使用中の社員が 1 人も居ない会社では 2 つの欄を form から外し、画面は今のままにする (編集で、その実績に社員か名前の写しが入っているときは外さない)。
社員を消した後の写しを持つ実績 (社員を指す欄が空で、番号の写しがある実績) の編集では 2 つの欄を出さず、services に作業した人を渡さない
(services の既定 KEEP で写しを変えない。消した人の写しを画面から書き換える道を作らない)。
作業した人が空のときの知らせは R26 (検査の層) が出す。form では止めない (会社が設定 progress_staff で選ぶ)。
使用停止の社員の番号も欄の誤りにせず、その社員を services に渡す。知らせるか止めるかは R28 (検査の層) が決める
(会社が設定 progress_inactive_staff で選ぶ。本人の決め 11-11 13:1x)。

不良の行の欄 (見比べのまとめの M09。2 段目 N407b): 使用中の不良の区分が 1 つ以上ある会社では、入力の「不良の数」の欄を外し、
1 行 = 区分 (defect_<n>_kind)・数 (defect_<n>_qty)・原因 (defect_<n>_cause) の欄を「実績の数」の後に 3 行ぶん足す (宣言 = base_fields は変えない)。
行を足す JS は使わない (小窓と、現品票の QR から開くスマホの画面で同じに動かすため)。4 行以上は、保存した後に実績の編集で足す。
編集は、使用中の区分があるか、その実績に区分のある行 (区分を指すか、区分の名前の写しがある行) か 2 行以上があるときに、今の行ごとの組
(pk の隠しの欄 defect_<n>_pk つき) と空の 3 組を出す。数を空にした今の行は消す。services には不良の数 (defect_qty) でなく
行の並び (defects) を渡し、実績の不良の数は行の合計を services が書く。区分を選んでいない行は form では止めない
(ポカよけ R30 が会社の設定 progress_defect_kind で知らせるか止める)。区分の候補は masters の services (active_defect_kinds) で引く。
"""

from decimal import Decimal

from django import forms
from django.utils import timezone

from seizou.core.widgets import RemoteSelect
from seizou.masters import services as masters_services

from . import services
from .models import NO_KIND_TEXT, Progress

_WorkOrderStep = Progress._meta.get_field("workorder_step").related_model

#: 印の付いた工程を選んだときに「指示書の工程」の欄の下に出す字 (作者の決め。断らない・色を変えない)。
#: 実績の新規登録の画面で工程を選び直したときは progress/progress-form.js が同じ字を出し入れする。
STEP_COMPLETED_HELP = "この工程には完了の印が付いています。実績は足せます。"

#: 作業した人の 2 つの欄の名前 (入力と編集で同じ)
STAFF_FIELDS = ("staff_code", "staff_name")
#: 作業した人の 2 つの欄の見出しと説明 (入力と編集で同じ字)
STAFF_CODE_LABEL = "社員番号"
STAFF_CODE_HELP = "社員の札のバーコードを読むか、番号を打ちます。先頭の 0 は省けます。"
STAFF_NAME_LABEL = "社員の一覧に居ない人の名前"
STAFF_NAME_HELP = "応援の人など、社員の一覧に居ない人が作業したときだけ入れます。"
#: 作業した人の欄の誤り ({code} は社員番号)。欄の下の赤い字に出す
STAFF_UNKNOWN_MESSAGE = "社員番号 {code} は社員の一覧にありません。番号を確かめるか、社員の一覧に足してください。"
STAFF_BOTH_MESSAGE = "社員番号と名前のどちらか一方を入れます。社員番号を入れたときは、名前を空にしてください。"

#: 不良の行の空の組の数 (入力は 3 行。編集は今の行の後に 3 行。N407b)
DEFECT_EMPTY_SETS = 3
#: 不良の行の欄の誤り (欄の下の赤い字。知らせの段の 1)
DEFECT_QTY_REQUIRED = "数を入れてください。"
DEFECT_QTY_POSITIVE = "数は 0 より大きい数にしてください。"
#: 不良の行の欄の下の説明 (入力の 1 行目の数・編集の今の行の数・消した区分の写しだけの行の区分。{name} は写しの名前)
DEFECT_FIRST_QTY_HELP = "区分ごとに 1 行。4 行以上あるときは、保存した後に実績の編集で足します。"
DEFECT_DROP_HELP = "数を空にすると、この行を消します。"
DEFECT_COPIED_HELP = "消した区分「{name}」です。区分を選び直さなければ、そのまま残ります。"


#: 不良の行を包む開け閉めの枠の見出し (入力のカードの fold_label)
DEFECT_FOLD_LABEL = "不良を入れる"


class DefectFoldMixin:
    """不良の行の欄を、入力のカードの開け閉めの枠 (core/_form_card.html の fold_fields) に入れる form の共通の所。

    fold_fields は枠に入れる欄の名前 (不良の行の区分・数・原因の全部の組。_add_defect_fields が置く。不良の行の欄が無い form は空で、枠を描かない)。
    fold_open は枠を開いて描くか: 今の不良の行がある (編集) か、送った値に不良の行の字があるか、不良の行の欄に誤りがあるとき。ほかは閉じて描く。
    閉じていても欄は form の中にあり、値は送られる (clean は今のまま)。
    訳: 現場でスマホから入れる実績の多くは不良が無い。不良の区分を登録した会社では欄が 17 個並び、「記録する」まで 2 画面あまり送っていたので、
    不良の行は「不良を入れる」を押した時だけ開く。値のある行を見えないまま保存させないために、値か誤りがある時は開いたまま出す。
    出所 = 本人の決め 11-11 22:1x。変えてよい条件 = 本人が見せ方を決め直したとき。
    """

    fold_fields = ()
    fold_label = DEFECT_FOLD_LABEL

    @property
    def fold_open(self) -> bool:
        if not self.fold_fields:
            return False
        if getattr(self, "defect_rows", None):
            return True
        if not self.is_bound:
            return False
        if any(str(self.data.get(self.add_prefix(name)) or "").strip() for name in self.fold_fields):
            return True
        return any(name in self.errors for name in self.fold_fields)


class ProgressRecordForm(DefectFoldMixin, forms.Form):
    """実績の入力。日時の既定は今。予定を超えた実数も入れられる (現物優先)。

    日時はブラウザの日付と時刻の選び (分まで) で、値の形はその欄が受ける「2026-11-06T14:30」(日付と時刻の間が T・秒なし) にする。
    不良の数は空なら 0、「この工程を完了にする」はいつも空で始める (作者の決め。?completes_step=on の付いた URL でも)。
    選んである工程 (送った値か ?workorder_step= の初期値) に完了の印の付いた実績があれば、「指示書の工程」の欄の下に
    STEP_COMPLETED_HELP を出す (作者の決め)。help_text は宣言でなく __init__ で置く (「?」の「入れる物」の表は宣言 = base_fields を読むので、字を変えない)。
    作業した人の 2 つの欄 (社員番号と、社員の一覧に居ない人の名前) は「指示書の工程」の次に宣言し、使用中の社員が 1 人も居なければ
    __init__ で外す (「入れる物」の表には宣言のまま 2 つとも出る。外し方は help_text と同じく __init__ で行う)。
    使用中の不良の区分が 1 つ以上あれば、__init__ で「不良の数」の欄を外し、不良の行の欄を 3 行ぶん「実績の数」の後に足す
    (_add_defect_fields。宣言は変えない)。そのときは services に不良の行の並び (defects) を渡し、不良の数 (defect_qty) を渡さない。
    """

    submit_label = "記録する"
    #: 行の頭 (幅 640 以上の 2 列の左) から始める欄 (入力のカード core/_form_card.html が読む)。
    #: 「社員番号」と「社員の一覧に居ない人の名前」、「実績の数」と「不良の数」を、それぞれ同じ行の左右に並べる。
    row_start_fields = ("staff_code", "qty")

    workorder_step = forms.ModelChoiceField(
        label="指示書の工程",
        queryset=_WorkOrderStep.objects.select_related("workorder", "process"),
        widget=RemoteSelect("workorders:workorderstep_select"),
    )
    staff_code = forms.CharField(label=STAFF_CODE_LABEL, max_length=10, required=False, help_text=STAFF_CODE_HELP)
    staff_name = forms.CharField(label=STAFF_NAME_LABEL, max_length=100, required=False, help_text=STAFF_NAME_HELP)
    qty = forms.DecimalField(
        label="実績の数",
        max_digits=12,
        decimal_places=2,
        help_text="この工程を終えた良品の数 (不良を除く)。",
    )
    defect_qty = forms.DecimalField(
        label="不良の数",
        max_digits=12,
        decimal_places=2,
        required=False,
        min_value=Decimal("0"),
        help_text="この工程で不良になった数。無ければ空のままにします。",
        error_messages={"min_value": "不良の数は 0 以上の数にしてください。"},
    )
    completes_step = forms.BooleanField(
        label="この工程を完了にする",
        required=False,
        help_text="この工程を終えたら付けます。最後の工程に付けると、指示書が「完了」になります。",
    )
    recorded_at = forms.DateTimeField(
        label="日時",
        initial=timezone.now,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    note = forms.CharField(label="備考", max_length=500, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 印はいつも空で始める (作者の決め)。土台の get_initial は新規登録の GET の鍵を全部初期値に写すので、ここで外す。
        self.initial.pop("completes_step", None)
        step_pk = _chosen_pk(self.data.get(self.add_prefix("workorder_step")) if self.is_bound else self.initial.get("workorder_step"))
        if step_pk is not None and services.step_completed(step_pk):
            self.fields["workorder_step"].help_text = STEP_COMPLETED_HELP
        if not masters_services.has_active_staff():
            _drop_staff_fields(self)
        if masters_services.has_active_defect_kinds():
            _add_defect_fields(self, [])
            self.fields["defect_1_qty"].help_text = DEFECT_FIRST_QTY_HELP

    def clean(self):
        cleaned_data = super().clean()
        self.staff = _clean_staff(self)
        self.defect_lines = _clean_defect_lines(self)
        return cleaned_data

    def service_kwargs(self):
        kwargs = {
            "workorder_step": self.cleaned_data["workorder_step"],
            "qty": self.cleaned_data["qty"],
            "completes_step": self.cleaned_data["completes_step"],
            "recorded_at": self.cleaned_data["recorded_at"],
            "note": self.cleaned_data["note"],
            # 欄を外した form (社員が 0 人の会社) では、社員は None で名前は空
            **_staff_kwargs(self),
        }
        kwargs.update(_defect_kwargs(self))
        return kwargs


class ProgressUpdateForm(DefectFoldMixin, forms.Form):
    """実績の編集 (作者の決め)。良品の数・不良の数・完了の印・日時・備考を直す。工程の欄は出さない (別の工程に入れた実績は消して入れ直す)。

    欄の名前・説明・誤りの字は実績の入力 (ProgressRecordForm) と同じ。
    ModelForm にしない。ModelForm は検査の前に新しい値を対象へ書き込み、検査の層 (R07) が直す前の数を読めなくなる。
    土台は名指しの instance に直す前の実績を渡すので、その値を初期値にだけ使う (instance には書き込まない。出荷の編集と同じ形)。
    どの工程の実績かは保存のボタンの字に出す (「<指示書の番号> <順> <工程の名前> の実績を直す」)。
    作業した人の 2 つの欄は入力と同じ字で、工程の欄が無いので先頭に置く。初めの値は、社員を指す実績なら社員番号の欄に社員の今の番号、
    応援の実績なら名前の欄に名前の写し。使用中の社員が 1 人も居ず、実績に社員も名前の写しも無いときは 2 つの欄を外す。
    社員を消した後の写しを持つ実績では 2 つの欄を外し、services に staff と staff_name を渡さない (既定の KEEP で写しを変えない)。
    不良の行の欄は、使用中の不良の区分が 1 つ以上あるか、その実績に区分のある行 (区分を指すか、区分の名前の写しがある行) か 2 行以上が
    あるときに足す (N407 の services は、区分のある行か 2 行以上の実績の不良の数を数で直すのを断るため)。今の行ごとに 1 組 (pk の隠しの欄つき。
    初めの値は今の区分・数・原因) と空の 3 組。足したときは「不良の数」の欄を外し、services に行の並び (defects) を渡す。
    足さないときは今の「不良の数」の欄のまま (services の既定の KEEP が区分の無い 1 行を合わせる)。
    """

    submit_label = "実績を直す"
    #: 行の頭から始める欄 (入力の ProgressRecordForm と同じ並び)
    row_start_fields = ("staff_code", "qty")

    staff_code = forms.CharField(label=STAFF_CODE_LABEL, max_length=10, required=False, help_text=STAFF_CODE_HELP)
    staff_name = forms.CharField(label=STAFF_NAME_LABEL, max_length=100, required=False, help_text=STAFF_NAME_HELP)
    qty = forms.DecimalField(
        label=ProgressRecordForm.base_fields["qty"].label,
        max_digits=12,
        decimal_places=2,
        help_text=ProgressRecordForm.base_fields["qty"].help_text,
    )
    defect_qty = forms.DecimalField(
        label=ProgressRecordForm.base_fields["defect_qty"].label,
        max_digits=12,
        decimal_places=2,
        required=False,
        min_value=Decimal("0"),
        help_text=ProgressRecordForm.base_fields["defect_qty"].help_text,
        error_messages={"min_value": ProgressRecordForm.base_fields["defect_qty"].error_messages["min_value"]},
    )
    completes_step = forms.BooleanField(
        label=ProgressRecordForm.base_fields["completes_step"].label,
        required=False,
        help_text=ProgressRecordForm.base_fields["completes_step"].help_text,
    )
    recorded_at = forms.DateTimeField(
        label="日時",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    note = forms.CharField(label="備考", max_length=500, required=False)

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.record = instance
        if instance is not None:
            for name in ("qty", "defect_qty", "completes_step", "recorded_at", "note"):
                self.initial.setdefault(name, getattr(instance, name))
            self.submit_label = f"{instance.workorder_step} の実績を直す"
        if _staff_copied_after_delete(instance) or not (_has_worker(instance) or masters_services.has_active_staff()):
            _drop_staff_fields(self)
        elif instance is not None and instance.staff_id is not None:
            self.initial.setdefault("staff_code", instance.staff.code)
        elif instance is not None:
            self.initial.setdefault("staff_name", instance.staff_name)
        rows = _current_defects(instance)
        if masters_services.has_active_defect_kinds() or len(rows) > 1 or any(_has_kind(row) for row in rows):
            _add_defect_fields(self, rows)

    def clean(self):
        cleaned_data = super().clean()
        self.staff = _clean_staff(self)
        self.defect_lines = _clean_defect_lines(self)
        return cleaned_data

    def service_kwargs(self):
        kwargs = {
            "qty": self.cleaned_data["qty"],
            "completes_step": self.cleaned_data["completes_step"],
            "recorded_at": self.cleaned_data["recorded_at"],
            "note": self.cleaned_data["note"],
        }
        # 欄を外した form (消した社員の写しの実績・社員が 0 人の会社) は作業した人を渡さない (services の既定 KEEP で変えない)
        if "staff_code" in self.fields:
            kwargs.update(_staff_kwargs(self))
        kwargs.update(_defect_kwargs(self))
        return kwargs


class ProgressDeleteForm(forms.Form):
    """実績の削除の確かめ (作者の決め)。欄は無い (土台が delete_progress(対象, user=…) を呼ぶ)。実行のボタンは赤の「削除」。"""

    submit_label = "削除"
    danger = True  # 実行のボタンを削除の確かめと同じ赤 (btn btn-error) にする (core/_form_card.html)

    def service_kwargs(self):
        return {}



class ProgressDefectNoteForm(forms.Form):
    """不良の行の原因と対策を書く (N407b)。欄は原因と対策だけ (区分と数は実績の編集で直す)。対策は字の多い欄。

    ModelForm にしない (土台が ModelForm の値を対象に写さないように、実績の編集と同じ形にする)。土台は名指しの instance に今の行を渡すので、
    その値を初めの値にだけ使う。どの実績の不良かは保存のボタンの字に出す (「<指示書の工程> の不良 <見せる字> <数> の原因と対策を保存する」。
    実績の編集の submit_label と同じ形)。保存していない行 (ボタンの字の試験が空の記録で作る) は class の字のまま。
    """

    submit_label = "原因と対策を保存する"

    cause_note = forms.CharField(label="原因", max_length=500, required=False)
    countermeasure = forms.CharField(label="対策", max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        if instance is not None and instance.pk is not None:
            self.initial.setdefault("cause_note", instance.cause_note)
            self.initial.setdefault("countermeasure", instance.countermeasure)
            self.submit_label = f"{instance.progress.workorder_step} の不良 {instance} の原因と対策を保存する"

    def service_kwargs(self):
        return {"cause_note": self.cleaned_data["cause_note"], "countermeasure": self.cleaned_data["countermeasure"]}

def _drop_staff_fields(form):
    """作業した人の 2 つの欄を form から外す (宣言 = base_fields は変えない。「?」の「入れる物」の表は宣言を読む)。"""
    for name in STAFF_FIELDS:
        form.fields.pop(name, None)


def _has_worker(record) -> bool:
    """実績に作業した人 (社員か、名前の写し) が入っているか。保存していない実績と None は偽。"""
    if record is None or record.pk is None:
        return False
    return record.staff_id is not None or bool(record.staff_name)


def _staff_copied_after_delete(record) -> bool:
    """社員を消した後の写しを持つ実績か (社員を指す欄が空で、番号の写しがある)。番号の写しを書くのは社員を消すときだけ (progress.models)。"""
    return record is not None and record.staff_id is None and bool(record.staff_code)


def _clean_staff(form):
    """作業した人の 2 つの欄を確かめ、引いた社員 (無ければ None) を返す。誤りは欄の下の赤い字 (add_error) にする。欄の無い form は None。

    社員番号は masters.services.staff_by_code で引く (先頭の 0 と全角の数字は同じ番号と見る)。居なければ STAFF_UNKNOWN_MESSAGE。
    番号と名前の両方が入っていれば名前の欄に STAFF_BOTH_MESSAGE。使用停止の社員は欄の誤りにせず、その社員を返す
    (知らせるか止めるかは、検査の層のポカよけ R28 が会社の設定 progress_inactive_staff で決める。本人の決め 11-11 13:1x)。
    訳: 実績に残す作業した人を、社員の一覧の 1 人か、社員の一覧に居ない人の名前のどちらか 1 つに決めるため (打ち間違えた番号で、
    違う人の実績にしないため)。辞めた人の番号は、前は欄の誤りで断ったが、運用の誤りを防ぐ物は、まず分かるようにして、止めるかは会社が選ぶ
    (北極星のポカよけの考え方 11-11 12:5x) ので R28 に任せる。
    出所 = 多くの工場の生産管理で要った動き (見比べのまとめの M02) と本人の決め 11-11 09:0x (依頼書 N401b)・11-11 13:1x (依頼書 N404)。
    変えてよい条件 = 社員の一覧に居ない人の名前と社員番号を両方残す運用が要ると本人が決めたとき (そのときは services の写しの決まりも変える)。
    """
    if "staff_code" not in form.fields:
        return None
    code = form.cleaned_data.get("staff_code") or ""
    name = form.cleaned_data.get("staff_name") or ""
    if not code:
        return None
    if name:
        form.add_error("staff_name", STAFF_BOTH_MESSAGE)
    staff = masters_services.staff_by_code(code)
    if staff is None:
        form.add_error("staff_code", STAFF_UNKNOWN_MESSAGE.format(code=code))
        return None
    return staff


def _staff_kwargs(form) -> dict:
    """services に渡す作業した人 (staff = 引いた社員か None、staff_name = 名前の欄の字。番号を入れたときは空)。欄の無い form は None と空。"""
    if "staff_code" not in form.fields:
        return {"staff": None, "staff_name": ""}
    staff = getattr(form, "staff", None)
    has_code = bool(form.cleaned_data.get("staff_code"))
    return {"staff": staff, "staff_name": "" if has_code else form.cleaned_data.get("staff_name") or ""}


def _chosen_pk(value):
    """選んである指示書の工程の pk (送った値か初期値。model・pk・pk の字のどれか)。読めなければ None。"""
    pk = str(getattr(value, "pk", value) or "")
    return int(pk) if pk.isdecimal() else None


def _defect_kind_field(n, keep=None):
    return forms.ModelChoiceField(
        label=f"不良 {n} の区分",
        queryset=masters_services.active_defect_kinds(keep=keep),
        required=False,
        empty_label=NO_KIND_TEXT,
    )


def _defect_qty_field(n):
    return forms.DecimalField(label=f"不良 {n} の数", max_digits=12, decimal_places=2, required=False)


def _defect_cause_field(n):
    return forms.CharField(label=f"不良 {n} の原因", max_length=500, required=False)


def _current_defects(record) -> list:
    """実績の今の不良の行 (pk の順)。保存していない実績と None は空。"""
    if record is None or record.pk is None:
        return []
    return list(record.defects.order_by("pk"))


def _has_kind(row) -> bool:
    """区分のある不良の行か (区分を指すか、区分の名前の写しがある)。"""
    return row.kind_id is not None or bool(row.kind_name)


def _copied_only(row) -> bool:
    """消した区分の名前の写しだけの不良の行か (区分を指す欄が空で、写しがある)。写しを書くのは区分を消すときだけ (progress.models)。"""
    return row is not None and row.kind_id is None and bool(row.kind_name)


def _add_defect_fields(form, rows):
    """不良の行の欄を「実績の数」の後に足し、「不良の数」の欄を外す (宣言 = base_fields は変えない。作業した人の欄と同じ外し方)。

    rows (今の不良の行) ごとに 1 組 (pk の隠しの欄・区分・数・原因。初めの値は今の行) と、空の DEFECT_EMPTY_SETS 組を足す。
    区分の候補は使用中の区分と「区分なし」(今の行の組は、今の区分を使用停止でも残す)。消した区分の写しだけの行の組は、区分を「区分なし」で開き、
    欄の下に写しの名前を出す。区分の欄は行の頭から始める (row_start_fields。区分と数を同じ行の左右に並べる)。
    form.defect_numbers に組の番号の並び、form.defect_rows に今の行 (pk → 行) を持つ (clean が読む)。
    form.fold_fields に、入力のカードの開け閉めの枠 (「不良を入れる」) に入れる欄の名前を持つ (DefectFoldMixin。初めは閉じて描く)。
    """
    added = {}
    numbers = []
    for n, row in enumerate([*rows, *([None] * DEFECT_EMPTY_SETS)], start=1):
        numbers.append(n)
        if row is not None:
            added[f"defect_{n}_pk"] = forms.IntegerField(widget=forms.HiddenInput, required=False)
            form.initial.setdefault(f"defect_{n}_pk", row.pk)
        kind = _defect_kind_field(n, keep=row.kind_id if row is not None else None)
        qty = _defect_qty_field(n)
        if row is not None:
            form.initial.setdefault(f"defect_{n}_kind", row.kind_id)
            form.initial.setdefault(f"defect_{n}_qty", row.qty)
            form.initial.setdefault(f"defect_{n}_cause", row.cause_note)
            qty.help_text = DEFECT_DROP_HELP
            if _copied_only(row):
                kind.help_text = DEFECT_COPIED_HELP.format(name=row.kind_name)
        added[f"defect_{n}_kind"] = kind
        added[f"defect_{n}_qty"] = qty
        added[f"defect_{n}_cause"] = _defect_cause_field(n)
    fields = {}
    for name, field in form.fields.items():
        if name == "defect_qty":
            continue
        fields[name] = field
        if name == "qty":
            fields.update(added)
    form.fields = fields
    form.initial.pop("defect_qty", None)
    form.defect_numbers = numbers
    form.defect_rows = {row.pk: row for row in rows}
    # 入力のカードの開け閉めの枠に入れる欄 (見える欄だけ。pk の隠しの欄は入れない。DefectFoldMixin)
    form.fold_fields = tuple(name for name in added if not name.endswith("_pk"))
    form.row_start_fields = (*type(form).row_start_fields, *(f"defect_{n}_kind" for n in numbers))


def _clean_defect_lines(form):
    """不良の行の欄を確かめ、services に渡す行の並び (defects) を返す。欄を足していない form は None。誤りは欄の下の赤い字 (add_error)。

    区分も数も原因も空の組は飛ばす。今の行の組 (pk の隠しの欄がある組) は、数を空にすると並びに入れない (services がその行を消す)。
    区分か原因が入っていて数が空の新しい組は、数の欄に DEFECT_QTY_REQUIRED。数が 0 以下なら、数の欄に DEFECT_QTY_POSITIVE。
    区分を選んでいない行は止めない (ポカよけ R30 が会社の設定で知らせるか止める)。今の行は送られた pk で決める (並びの位置で決めない)。
    消した区分の写しだけの行で区分を選び直さなかったときは "kind" の鍵を入れない (services が写しを変えない約束)。ほかは "kind" に区分か None。
    ほかの実績の行と既に無い行の pk は、そのまま渡す (services が「この実績に無い不良の行が含まれています。…」で断る)。
    訳: 数の無い行と 0 以下の数の行を、どの欄を直せばよいかが分かる欄の誤りで止めるため (services の行の数の断りは最後の守りで、どの行かを言えない)。
    数を空にした今の行を消すのは、行を消すボタンを JS 無しで置かないため (小窓と、現品票の QR から開くスマホの画面で同じに動かす)。
    出所 = 依頼書 N407b (本体の決め 11-11 16:3x) と、多くの工場の生産管理で要った動き (見比べのまとめの M09)。変えてよい条件 = 本人が決め直したとき。
    """
    numbers = getattr(form, "defect_numbers", None)
    if not numbers:
        return None
    data = form.cleaned_data
    lines = []
    for n in numbers:
        qty_name = f"defect_{n}_qty"
        if qty_name in form.errors:
            continue
        pk = data.get(f"defect_{n}_pk")
        kind = data.get(f"defect_{n}_kind")
        qty = data.get(qty_name)
        cause = data.get(f"defect_{n}_cause") or ""
        if qty is None:
            if pk is None and (kind is not None or cause):
                form.add_error(qty_name, DEFECT_QTY_REQUIRED)
            continue
        if qty <= 0:
            form.add_error(qty_name, DEFECT_QTY_POSITIVE)
            continue
        line = {"qty": qty, "cause_note": cause}
        if pk is not None:
            line["pk"] = pk
        if not (kind is None and _copied_only(form.defect_rows.get(pk))):
            line["kind"] = kind
        lines.append(line)
    return lines


def _defect_kwargs(form) -> dict:
    """services に渡す不良。不良の行の欄を足した form は行の並び (defects) だけ、ほかは今のまま不良の数 (defect_qty。空なら 0)。"""
    lines = getattr(form, "defect_lines", None)
    if lines is not None:
        return {"defects": lines}
    return {"defect_qty": form.cleaned_data.get("defect_qty") or Decimal("0")}
