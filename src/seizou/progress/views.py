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
from .models import Progress


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
    screen_help = "指示書の工程を選び、実績の数と日時を入れます。予定を超えた数も記録できます。社員が居る会社では、作業した人を社員番号か名前で入れます。"


class ProgressUpdateView(BaseFormView):
    """実績の編集 (実績の一覧の行の ⋮ と右クリックの「編集」から小窓で開く。作者の決め・1e)。保存の後は ?next= の道 (実績の一覧) へ戻る。

    使う人: 製造・生産管理 / いつ: 入れた実績の数や完了の印を間違えたとき / 何のために: 実績を直す / 次にすること: 実績の一覧で確かめる
    """

    model = Progress
    form_class = ProgressUpdateForm
    service = staticmethod(services.update_progress)
    template_name = "progress/progress_form.html"
    screen_name = "実績の編集"
    screen_help = "入れた実績の良品の数・不良の数・完了の印・日時・備考と作業した人を直します。工程は直せません。別の工程に入れた実績は消して入れ直します。"


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
