"""progress の画面 (02 の進捗)。全部 core の土台を継承する (R15)。

一覧は実績の記録の一覧 (日時の新しい順)。実績の入力は検査の層 (予定より多い実績のポカよけ) を
通ってから services.record_progress を呼ぶ。
"""

from seizou.core.views import BaseFormView, BaseListView

from . import services
from .forms import ProgressRecordForm
from .models import Progress


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

    使う人: 製造・生産管理 / いつ: 入れた実績を確かめるとき / 何のために: 工程ごとの実績の記録を見る / 次にすること: 指示書の詳細へ
    """

    model = Progress
    screen_name = "実績の一覧"
    screen_help = "指示書の工程ごとに入れた実績の記録。日時の新しい順に並びます。"
    columns = ("workorder_step", "qty", "recorded_at", "recorded_by", "note")
    search_fields = ("workorder_step__workorder__wo_no",)
    ordering = ("-recorded_at", "-id")


class ProgressCreateView(BaseFormView):
    """実績の新規登録の入力。

    使う人: 製造 / いつ: 工程が終わるたびと、現品票の QR を読んだとき / 何のために: 実績の数を入れる / 次にすること: 次の工程へ。最後の工程なら製造の完了を入庫にする
    """

    model = Progress
    form_class = ProgressRecordForm
    service = staticmethod(services.record_progress)
    screen_name = "実績の新規登録"
    screen_help = "指示書の工程を選び、実績の数と日時を入れます。予定を超えた数も記録できます。"
