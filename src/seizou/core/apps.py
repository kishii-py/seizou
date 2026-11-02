from django.apps import AppConfig


class CoreConfig(AppConfig):
    """土台。ほかの部品はこの app と、相手の services だけを呼ぶ。"""

    name = "seizou.core"
    label = "core"
    verbose_name = "土台"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # 同梱のポカよけ (R01・R04・R06・R07) を検査の層に登録する。
        from .checks import builtin  # noqa: F401
