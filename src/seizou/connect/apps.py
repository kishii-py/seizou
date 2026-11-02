from django.apps import AppConfig


class ConnectConfig(AppConfig):
    """つながり。取引先ごとの受け口と鍵の置き場、受け口、受信箱。呼ぶ相手は core と masters。"""

    name = "seizou.connect"
    label = "connect"
    verbose_name = "つながり"
    default_auto_field = "django.db.models.BigAutoField"
