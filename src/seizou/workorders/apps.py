from django.apps import AppConfig


class WorkordersConfig(AppConfig):
    """指示書。指示書と指示書の工程。呼ぶ相手は core と masters と orders だけ。"""

    name = "seizou.workorders"
    label = "workorders"
    verbose_name = "指示書"
    default_auto_field = "django.db.models.BigAutoField"
