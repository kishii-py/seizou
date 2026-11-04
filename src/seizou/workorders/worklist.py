"""指示書の一覧の製造向けの印 (02 の 8 節)。一覧の絞り (WorkOrderListView.filters) の flag が呼ぶ when の関数。

- 印は 2 つ: 未着手だけ (f_unaccepted=1)・今日の計画だけ (f_today=1)。工程は土台の lookup の絞り (f_process)。
- 読むだけで、表に書かない (受けた印を書くのは services.accept_workorder)。
- when は filter だけを掛ける。工程の行の join で重なった指示書は、一覧の view の get_queryset が distinct で外す。
"""

from __future__ import annotations

from django.utils import timezone

from .models import WorkOrder


def unaccepted_only(queryset):
    """未着手だけ: 発行のままで、まだ受けていない指示書 (取り消した指示書は入れない)。"""
    return queryset.filter(status=WorkOrder.STATUS_ISSUED, accepted_at__isnull=True)


def planned_today(queryset):
    """今日の計画だけ: 今日の計画 (工程の予定の開始日 ≤ 今日 ≤ 終了日) を持つ指示書。今日は呼んだ時の日付。"""
    today = timezone.localdate()
    return queryset.filter(steps__planned_start__lte=today, steps__planned_end__gte=today)
