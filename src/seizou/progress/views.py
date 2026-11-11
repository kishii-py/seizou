"""progress の画面 (02 の進捗)。全部 core の土台を継承する (R15)。

一覧は実績の記録の一覧 (日時の新しい順)。実績の入力は検査の層 (予定より多い実績のポカよけ) を
通ってから services.record_progress を呼ぶ。
読むだけの口 step_completed は、実績の新規登録の JS (progress/progress-form.js) が工程を選び直したときに読む。
"""

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_GET

from seizou.core.views import BaseFormView, BaseListView, field_at

from . import services
from .forms import ProgressDeleteForm, ProgressRecordForm, ProgressUpdateForm
from .forms import ProgressDefectNoteForm
from .models import Progress, ProgressDefect


def _process_choices():
    """工程の絞りの候補。字は工程の名前だけ (工程コードを付けない)。並びは工程の ordering (並び順・工程コード)。"""
    process = field_at(Progress, "workorder_step__process").related_model
    return [(row.pk, row.name) for row in process._default_manager.all()]


class ProgressBoardView(BaseListView):
    """進捗の盤 (02 の進捗の一覧)。盤の中身は template tag (progress_board) が boards.build_board で作る。

    model を Progress にするのは、土台が htmx の部分を model の app の名前で選ぶため (progress/_rows.html)。
    土台の object_list (実績の 50 件) は使わない。

    使う人: 製造・生産管理 / いつ: 工程が終わるたびと、進み具合を見るとき / 何のために: 指示書の工程ごとの予定・実績・残を見る / 次にすること: 行から実績の新規登録へ
    """

    model = Progress
    template_name = "progress/progress_board.html"
    screen_name = "進捗"
    screen_help = "指示書の工程ごとの予定の数・実績の合計・残。10 秒ごとに、実績が入った行を差し替えます。"


class ProgressListView(BaseListView):
    """実績の一覧。

    作業した人の列 (staff_name。見出しは表の「作業した人」) の字は、土台の display_value が Progress.get_staff_name_display から取る
    (社員の今の番号と名前。消した社員と社員の一覧に居ない人は写し)。並び替えの鍵は社員の番号。「人」の列はログインした人のまま。
    検索は、社員を指す実績 (社員の番号と名前) と写しだけの実績 (番号と名前の写し) の両方を番号と名前の字で探す。絞り「作業した社員」は今居る社員で絞る。

    使う人: 製造・生産管理 / いつ: 入れた実績を確かめるとき / 何のために: 工程ごとの実績の記録を見る / 次にすること: 指示書の詳細へ
    """

    model = Progress
    screen_name = "実績の一覧"
    screen_help = "指示書の工程ごとに入れた実績の記録。日時の新しい順に並びます。作業した人の列と絞りがあります。"
    columns = (
        {"name": "workorder_step", "sort_key": "workorder_step__workorder__wo_no"},
        "qty",
        "defect_qty",
        "completes_step",
        "recorded_at",
        {"name": "staff_name", "sort_key": "staff__code"},
        "recorded_by",
        "note",
    )
    search_fields = ("workorder_step__workorder__wo_no", "staff__code", "staff__name", "staff_code", "staff_name")
    filters = (
        {"name": "recorded", "label": "期間", "kind": "date_range", "field": "recorded_at"},
        {"name": "process", "label": "工程", "kind": "lookup", "field": "workorder_step__process", "choices": _process_choices},
        {"name": "item", "label": "部品", "kind": "lookup", "field": "workorder_step__workorder__order_line__item"},
        {"name": "user", "label": "入れた人", "kind": "lookup", "field": "recorded_by"},
        {"name": "staff", "label": "作業した社員", "kind": "lookup", "field": "staff"},
    )
    ordering = ("-recorded_at", "-id")

    def get_queryset(self):
        # 作業した人の列の字 (get_staff_name_display) は社員を読むので、行と一緒に 1 回で引く (行ごとに社員を読まない)。
        # 土台が列の外部キーを引くのは、列の名前が外部キーのときだけ (staff_name は字の欄)。
        return super().get_queryset().select_related("staff")


class ProgressCreateView(BaseFormView):
    """実績の新規登録の入力。

    使う人: 製造 / いつ: 工程が終わるたびと、現品票の QR を読んだとき / 何のために: 実績の数を入れる / 次にすること: 次の工程へ。最後の工程なら製造の完了を入庫にする
    """

    model = Progress
    form_class = ProgressRecordForm
    service = staticmethod(services.record_progress)
    template_name = "progress/progress_form.html"
    screen_name = "実績の新規登録"
    screen_help = (
        "指示書の工程を選び、実績の数と日時を入れます。予定を超えた数も記録できます。社員が居る会社では、作業した人を社員番号か名前で入れます。"
        "不良の区分がある会社では、不良を区分ごとの行で入れます。不良があるときは「不良を入れる」を開きます。"
    )


class ProgressUpdateView(BaseFormView):
    """実績の編集 (実績の一覧の行の ⋮ と右クリックの「編集」から小窓で開く。作者の決め・1e)。保存の後は ?next= の道 (実績の一覧) へ戻る。

    使う人: 製造・生産管理 / いつ: 入れた実績の数や完了の印を間違えたとき / 何のために: 実績を直す / 次にすること: 実績の一覧で確かめる
    """

    model = Progress
    form_class = ProgressUpdateForm
    service = staticmethod(services.update_progress)
    template_name = "progress/progress_form.html"
    screen_name = "実績の編集"
    screen_help = (
        "入れた実績の良品の数・不良の数・完了の印・日時・備考と作業した人を直します。不良の区分がある会社では、不良の行を直します。"
        "工程は直せません。別の工程に入れた実績は消して入れ直します。"
    )


class ProgressDeleteView(BaseFormView):
    """実績の削除の確かめ (実績の一覧の行の ⋮ と右クリックの「削除」から小窓で開く。作者の決め)。消した後は ?next= の道 (実績の一覧) へ戻る。

    使う人: 製造・生産管理 / いつ: 別の工程や二重に入れた実績を見つけたとき / 何のために: 間違えて入れた実績を消す / 次にすること: 実績の一覧で確かめる
    """

    model = Progress
    form_class = ProgressDeleteForm
    service = staticmethod(services.delete_progress)
    template_name = "progress/progress_delete.html"
    screen_name = "実績の削除"
    screen_help = "間違えて入れた実績を消します。消した実績は戻せません。"



# ---------------------------------------------------------------- 不良の行 (見比べのまとめの M09。2 段目 N407b)

#: 不良の一覧の日時の列の名前 (実績の日時を辿る。土台の列は __ で辿る名前の型を読まないので、行の部分 progress/_defect_row.html が 1 列目を分まで出す)
RECORDED_AT_COLUMN = "progress__recorded_at"


def _without_countermeasure(queryset):
    """対策の欄が空の不良の行だけ (絞り「対策の無い行だけ」)。"""
    return queryset.filter(countermeasure="")


class ProgressDefectListView(BaseListView):
    """不良の一覧 (実績で入れた不良の行)。

    区分の列 (kind_name。見出しは「区分」) の字は、土台の display_value が ProgressDefect.get_kind_name_display から取る
    (区分の今の名前。消した区分は写しの名前、区分の無い行は「区分なし」)。集計 (?view=summary) の区分の軸は外部キーで数えるので、
    消した区分の行と区分の無い行は土台の空の行の字「未設定」に入る (見せる字の「区分なし」とは別の字のまま。本体の 11-11 16:3x の答え)。
    日時と指示書の工程の列は実績を辿る名前なので、行と一緒に実績・指示書の工程・指示書・工程・区分を 1 回で引く (行ごとに読まない)。
    新規登録と削除の画面は無い (行は実績の入力と編集で足して消す)。行の ⋮ と右クリックの「原因と対策を書く」で原因と対策を書き足す。

    使う人: 品質・生産管理・社長 / いつ: 品質の問合せが来たときと、月に 1 回の振り返り / 何のために: どの工程でいつ何の不良が出たかを見て、多い不良から手を打つ / 次にすること: 行の ⋮ の「原因と対策を書く」で原因と対策を書く
    """

    model = ProgressDefect
    screen_name = "不良の一覧"
    screen_help = (
        "実績で入れた不良の行。区分・工程・部品・月で絞り、「集計」で区分ごとの数と割合を見て CSV に出せます。"
        "区分を消した行と区分の無い行は、集計で「未設定」に入ります。"
    )
    # 1 列目は日時 (行の部分 progress/_defect_row.html が 1 列目を分まで出す。会社ごとの項目の列は土台が最後に足すので、1 列目は動かない)
    columns = (
        {"name": RECORDED_AT_COLUMN, "label": "日時", "sort_key": RECORDED_AT_COLUMN},
        {"name": "progress__workorder_step", "label": "指示書の工程", "sort_key": "progress__workorder_step__workorder__wo_no"},
        {"name": "kind_name", "label": "区分", "sort_key": "kind__name"},
        "qty",
        "cause_note",
        "countermeasure",
    )
    search_fields = (
        ("progress__workorder_step__workorder__wo_no", "指示書の番号"),
        ("kind__name", "区分"),
        ("kind_name", "区分"),
        "cause_note",
        "countermeasure",
    )
    filters = (
        {"name": "recorded", "label": "期間", "kind": "date_range", "field": RECORDED_AT_COLUMN},
        {"name": "kind", "label": "区分", "kind": "lookup", "field": "kind"},
        {"name": "process", "label": "工程", "kind": "lookup", "field": "progress__workorder_step__process", "choices": _process_choices},
        {"name": "item", "label": "部品", "kind": "lookup", "field": "progress__workorder_step__workorder__order_line__item"},
        {"name": "no_countermeasure", "label": "対策の無い行だけ", "kind": "flag", "when": _without_countermeasure},
    )
    ordering = ("-progress__recorded_at", "-pk")

    def get_queryset(self):
        # 日時・指示書の工程の字 (指示書の番号と工程の名前)・区分の見せる字を、行と一緒に 1 回で引く。
        # 土台が列の外部キーを引くのは、列の名前が表の外部キーのときだけ (ここの列は __ で辿る名前と字の欄)。
        return super().get_queryset().select_related(
            "progress__workorder_step__workorder", "progress__workorder_step__process", "kind"
        )


class ProgressDefectUpdateView(BaseFormView):
    """不良の行の原因と対策を書く (不良の一覧の行の ⋮ と右クリックの「原因と対策を書く」から小窓で開く)。保存の後は ?next= の道 (不良の一覧) へ戻る。

    区分と数は実績の編集で直す (ここでは出さない)。削除の画面は無い (行は実績の編集で数を空にして消す)。

    使う人: 品質 / いつ: 不良の原因を調べた後 / 何のために: 原因と対策を不良の行に残す / 次にすること: 不良の一覧で確かめる
    """

    model = ProgressDefect
    form_class = ProgressDefectNoteForm
    service = staticmethod(services.update_defect_note)
    screen_name = "不良の原因と対策"
    screen_help = "不良の行に原因と対策を書きます。区分と数は実績の編集で直します。"

    def get_queryset(self):
        # 保存のボタンの字 (指示書の工程と区分の見せる字) が読む物を、対象と一緒に引く
        return ProgressDefect.objects.select_related("progress__workorder_step__workorder", "progress__workorder_step__process", "kind")


@login_required
@require_GET
def step_completed(request, pk):
    """指示書の工程に完了の印の付いた実績があるかを {"completed": 真偽} で返す読むだけの口。

    実績の新規登録の JS (progress/progress-form.js) が「指示書の工程」を選び直したときに 1 回読み、真なら欄の下に
    「この工程には完了の印が付いています。実績は足せます。」を出す (作者の決め。断らない)。部署は見ない (在庫の item_balance と同じ)。
    """
    step = get_object_or_404(field_at(Progress, "workorder_step").related_model, pk=pk)
    response = JsonResponse({"completed": services.step_completed(step)})
    add_never_cache_headers(response)
    return response
