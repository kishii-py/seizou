from django.apps import AppConfig


class PurchasingConfig(AppConfig):
    """発注。協力企業への発注と発注の明細。呼ぶ相手は core と masters と connect だけ。"""

    name = "seizou.purchasing"
    label = "purchasing"
    verbose_name = "発注"
    default_auto_field = "django.db.models.BigAutoField"
