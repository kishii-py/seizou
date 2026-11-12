"""指示書の一覧の製造向けの印 (一覧の画面の設計の決め)。一覧の絞り (WorkOrderListView.filters) の flag が呼ぶ when の関数。

- 印は 2 つ: 未着手だけ (f_unaccepted=1)・今日の計画だけ (f_today=1)。工程は土台の lookup の絞り (f_process)。
- 読むだけで、表に書かない。未着手は指示書の進みの状態 (実績から出す) と同じ物で、着手の印は無い (作者の決め)。
- when は filter だけを掛ける。工程の行の join で重なった指示書は、一覧の view の get_queryset が distinct で外す。
"""

from __future__ import annotations

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Exists, OuterRef
from django.utils import timezone

from .models import WorkOrder, WorkOrderStep


def unaccepted_only(queryset):
    """未着手だけ: 発行の指示書で、まだ実績の無い指示書 (どの工程にも実績が 1 件も無い。取り消した指示書は入れない)。

    指示書の進みの状態の「未着手」(WorkOrder.progress_state) と同じ物を DB の条件で絞る (行を Python で回さない)。
    実績の表は progress を import せず、工程の逆の関係の先として引く。進捗の部品が無い置き場では、発行の指示書を全部返す。
    絞りの名前 (unaccepted)・字 (未着手だけ)・URL の f_unaccepted は、古い URL が効くように変えない (作者の決め)。
    """
    issued = queryset.filter(status=WorkOrder.STATUS_ISSUED)
    try:
        records = WorkOrderStep._meta.get_field("progress_records").related_model
    except FieldDoesNotExist:
        return issued
    return issued.filter(~Exists(records._default_manager.filter(workorder_step__workorder=OuterRef("pk"))))


def to_subcontract_only(queryset):
    """協力企業に出していない工程があるだけ: これから協力企業に出す工程 (purchasing.services.steps_to_subcontract) を持つ指示書。

    今日やることの「協力企業に出していない工程」の数 (core.dashboard) と同じ関数で絞る (数は工程の行の数、この一覧は指示書の数)。
    工程の副問い合わせで絞るので、join を足さず、同じ指示書は重ならない。purchasing の部品が無い置き場では何も絞らない。
    """
    if not apps.is_installed("seizou.purchasing"):
        return queryset
    from seizou.purchasing.services import steps_to_subcontract

    return queryset.filter(pk__in=steps_to_subcontract().values("workorder_id"))


def planned_today(queryset):
    """今日の計画だけ: 今日の計画 (工程の予定の開始日 ≤ 今日 ≤ 終了日) を持つ指示書。今日は呼んだ時の日付。"""
    today = timezone.localdate()
    return queryset.filter(steps__planned_start__lte=today, steps__planned_end__gte=today)
