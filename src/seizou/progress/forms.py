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
"""

from decimal import Decimal

from django import forms
from django.utils import timezone

from seizou.core.widgets import RemoteSelect
from seizou.masters import services as masters_services

from . import services
from .models import Progress

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


class ProgressRecordForm(forms.Form):
    """実績の入力。日時の既定は今。予定を超えた実数も入れられる (現物優先)。

    日時はブラウザの日付と時刻の選び (分まで) で、値の形はその欄が受ける「2026-11-06T14:30」(日付と時刻の間が T・秒なし) にする。
    不良の数は空なら 0、「この工程を完了にする」はいつも空で始める (作者の決め。?completes_step=on の付いた URL でも)。
    選んである工程 (送った値か ?workorder_step= の初期値) に完了の印の付いた実績があれば、「指示書の工程」の欄の下に
    STEP_COMPLETED_HELP を出す (作者の決め)。help_text は宣言でなく __init__ で置く (「?」の「入れる物」の表は宣言 = base_fields を読むので、字を変えない)。
    作業した人の 2 つの欄 (社員番号と、社員の一覧に居ない人の名前) は「指示書の工程」の次に宣言し、使用中の社員が 1 人も居なければ
    __init__ で外す (「入れる物」の表には宣言のまま 2 つとも出る。外し方は help_text と同じく __init__ で行う)。
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

    def clean(self):
        cleaned_data = super().clean()
        self.staff = _clean_staff(self)
        return cleaned_data

    def service_kwargs(self):
        return {
            "workorder_step": self.cleaned_data["workorder_step"],
            "qty": self.cleaned_data["qty"],
            "defect_qty": self.cleaned_data["defect_qty"] or Decimal("0"),
            "completes_step": self.cleaned_data["completes_step"],
            "recorded_at": self.cleaned_data["recorded_at"],
            "note": self.cleaned_data["note"],
            # 欄を外した form (社員が 0 人の会社) では、社員は None で名前は空
            **_staff_kwargs(self),
        }


class ProgressUpdateForm(forms.Form):
    """実績の編集 (作者の決め)。良品の数・不良の数・完了の印・日時・備考を直す。工程の欄は出さない (別の工程に入れた実績は消して入れ直す)。

    欄の名前・説明・誤りの字は実績の入力 (ProgressRecordForm) と同じ。
    ModelForm にしない。ModelForm は検査の前に新しい値を対象へ書き込み、検査の層 (R07) が直す前の数を読めなくなる。
    土台は名指しの instance に直す前の実績を渡すので、その値を初期値にだけ使う (instance には書き込まない。出荷の編集と同じ形)。
    どの工程の実績かは保存のボタンの字に出す (「<指示書の番号> <順> <工程の名前> の実績を直す」)。
    作業した人の 2 つの欄は入力と同じ字で、工程の欄が無いので先頭に置く。初めの値は、社員を指す実績なら社員番号の欄に社員の今の番号、
    応援の実績なら名前の欄に名前の写し。使用中の社員が 1 人も居ず、実績に社員も名前の写しも無いときは 2 つの欄を外す。
    社員を消した後の写しを持つ実績では 2 つの欄を外し、services に staff と staff_name を渡さない (既定の KEEP で写しを変えない)。
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

    def clean(self):
        cleaned_data = super().clean()
        self.staff = _clean_staff(self)
        return cleaned_data

    def service_kwargs(self):
        kwargs = {
            "qty": self.cleaned_data["qty"],
            "defect_qty": self.cleaned_data["defect_qty"] or Decimal("0"),
            "completes_step": self.cleaned_data["completes_step"],
            "recorded_at": self.cleaned_data["recorded_at"],
            "note": self.cleaned_data["note"],
        }
        # 欄を外した form (消した社員の写しの実績・社員が 0 人の会社) は作業した人を渡さない (services の既定 KEEP で変えない)
        if "staff_code" in self.fields:
            kwargs.update(_staff_kwargs(self))
        return kwargs


class ProgressDeleteForm(forms.Form):
    """実績の削除の確かめ (作者の決め)。欄は無い (土台が delete_progress(対象, user=…) を呼ぶ)。実行のボタンは赤の「削除」。"""

    submit_label = "削除"
    danger = True  # 実行のボタンを削除の確かめと同じ赤 (btn btn-error) にする (core/_form_card.html)

    def service_kwargs(self):
        return {}


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
