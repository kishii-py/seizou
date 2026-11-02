from django.apps import AppConfig


class ManualsConfig(AppConfig):
    """業務マニュアル (会社の人が書く作業手順書と社内の文書)。core の使い方のページ (core:manual) とは別。呼ぶ相手は core だけ。"""

    name = "seizou.manuals"
    label = "manuals"
    verbose_name = "マニュアル"
    default_auto_field = "django.db.models.BigAutoField"
