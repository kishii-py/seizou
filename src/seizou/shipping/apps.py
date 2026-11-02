from django.apps import AppConfig


class ShippingConfig(AppConfig):
    """出荷。出荷と納品書。呼ぶ相手は core と masters と orders だけ。"""

    name = "seizou.shipping"
    label = "shipping"
    verbose_name = "出荷"
    default_auto_field = "django.db.models.BigAutoField"
