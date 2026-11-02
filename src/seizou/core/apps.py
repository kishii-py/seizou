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

        # 開発の設定 (DEBUG) のときだけ、起動時に決まりの検査 (書き方の決まり) を回す。
        # 本番の形では検査の module を読まない (05 の 4 節)。判定を import の前に置く。
        from django.conf import settings

        if settings.DEBUG:
            from .rules import check_at_startup

            check_at_startup()
