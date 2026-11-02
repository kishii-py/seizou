from django.apps import AppConfig


class IntakeConfig(AppConfig):
    """注文書の読み取り。写真か PDF の注文書を読み、人が確かめて受注にする。呼ぶ相手は core と masters と orders。"""

    name = "seizou.intake"
    label = "intake"
    verbose_name = "注文書の読み取り"
    default_auto_field = "django.db.models.BigAutoField"
