from django.apps import AppConfig


class MastersConfig(AppConfig):
    """マスター。部品マスター・取引先・部品の取引先・工程・標準の工程の並び。呼ぶ相手は core だけ。"""

    name = "seizou.masters"
    label = "masters"
    verbose_name = "マスター"
    default_auto_field = "django.db.models.BigAutoField"
