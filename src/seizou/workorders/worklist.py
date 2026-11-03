"""指示書の一覧の製造向けの絞り込み (02 の 8 節)。GET の読み方は 1 か所 (view と tag が同じ関数を呼ぶ)。

- 絞り込みは 3 つ: 工程 (process = 工程の pk)・未着手だけ (unaccepted=1)・今日の計画だけ (today=1)。
- 読むだけで、表に書かない (受けた印を書くのは services.accept_workorder)。
- 工程の表は、指示書の工程の外部キーの先として引く (masters の models を import しない)。
"""

from __future__ import annotations

from django.utils import timezone

from .models import WorkOrder, WorkOrderStep


def _positive_int(raw):
    """1 以上の整数の字なら int、ほかは None。"""
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return None
    return value if value >= 1 else None


def list_filters(params) -> dict:
    """GET の値から絞り込みの辞書を作る。読めない値は無い物として扱う (画面は 200 のまま)。"""
    return {
        "process": _positive_int(params.get("process")),
        "unaccepted": params.get("unaccepted") == "1",
        "today": params.get("today") == "1",
    }


def processes_for_filter():
    """絞り込みの欄に並べる工程の全部 (並び順 → 工程コードの順)。"""
    process_model = WorkOrderStep._meta.get_field("process").related_model
    return process_model._default_manager.order_by("sort_order", "code")


def apply_list_filters(queryset, filters, today=None):
    """指示書の queryset に絞り込みを当てて返す。工程の join を足したときだけ重なりを外す (distinct)。

    - process: その工程を持つ指示書。
    - unaccepted: 発行のままで、まだ受けていない指示書。
    - today: 今日の計画 (工程の予定の開始日 ≤ 今日 ≤ 終了日) を持つ指示書。
    """
    joined = False
    if filters.get("process") is not None:
        queryset = queryset.filter(steps__process_id=filters["process"])
        joined = True
    if filters.get("unaccepted"):
        queryset = queryset.filter(status=WorkOrder.STATUS_ISSUED, accepted_at__isnull=True)
    if filters.get("today"):
        if today is None:
            today = timezone.localdate()
        queryset = queryset.filter(steps__planned_start__lte=today, steps__planned_end__gte=today)
        joined = True
    if joined:
        queryset = queryset.distinct()
    return queryset
