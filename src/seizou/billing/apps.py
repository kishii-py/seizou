from django.apps import AppConfig


class BillingConfig(AppConfig):
    """請求書。出荷済みの出荷を得意先ごとにまとめる。呼ぶ相手は core と masters と shipping だけ。"""

    name = "seizou.billing"
    label = "billing"
    verbose_name = "請求書"
    default_auto_field = "django.db.models.BigAutoField"
