from django.apps import AppConfig


class ProgressConfig(AppConfig):
    """進捗。指示書の工程ごとの実績。呼ぶ相手は core と workorders だけ。"""

    name = "seizou.progress"
    label = "progress"
    verbose_name = "進捗"
    default_auto_field = "django.db.models.BigAutoField"
