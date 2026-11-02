from django.apps import AppConfig


class OrdersConfig(AppConfig):
    """受注。受注と受注の明細。呼ぶ相手は core と masters だけ。"""

    name = "seizou.orders"
    label = "orders"
    verbose_name = "受注"
    default_auto_field = "django.db.models.BigAutoField"
