"""progress の画面 (02 の進捗)。全部 core の土台を継承する (R15)。

一覧は実績の記録の一覧 (日時の新しい順)。実績の入力は検査の層 (予定より多い実績のポカよけ) を
通ってから services.record_progress を呼ぶ。
"""

from seizou.core.views import BaseFormView, BaseListView

from . import services
from .forms import ProgressRecordForm
from .models import Progress


class ProgressListView(BaseListView):
    model = Progress
    screen_name = "進捗"
    screen_help = "指示書の工程ごとに入れた実績の一覧。日時の新しい順に並びます。"
    columns = ("workorder_step", "qty", "recorded_at", "recorded_by", "note")
    search_fields = ("workorder_step__workorder__wo_no",)
    ordering = ("-recorded_at", "-id")


class ProgressCreateView(BaseFormView):
    model = Progress
    form_class = ProgressRecordForm
    service = staticmethod(services.record_progress)
    screen_name = "実績を入れる"
    screen_help = "指示書の工程を選び、実績の数と日時を入れます。予定を超えた数も記録できます。"
