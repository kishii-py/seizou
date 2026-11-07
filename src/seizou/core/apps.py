from django.apps import AppConfig


class CoreConfig(AppConfig):
    """土台。ほかの部品はこの app と、相手の services だけを呼ぶ。"""

    name = "seizou.core"
    label = "core"
    verbose_name = "土台"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # 標準のポカよけ (R01・R04・R06・R07) を検査の層に登録する。
        from .checks import builtin  # noqa: F401
        # 一覧の列 (columns) の書き方を、Django の起動の検査 (system check) に登録する。
        from . import system_checks  # noqa: F401

        # 選ぶ欄の空の選択肢の字 (Django の既定は英語の「- Select an option -」)。Django 6.1 は ready() での置き換えを認める。
        # 表の choices の欄・ModelChoiceField・RemoteSelect の空の選択肢が、この字になる (form の class を作る前に置く)。
        from django.db.models import fields as model_fields

        from .forms import EMPTY_LABEL

        model_fields.BLANK_CHOICE_LABEL = EMPTY_LABEL

        # 開発の設定 (DEBUG) のときだけ、起動時に決まりの検査 (書き方の決まり) を回す。
        # 本番の形では検査の module を読まない (05 の 4 節)。判定を import の前に置く。
        from django.conf import settings

        if settings.DEBUG:
            from .rules import check_at_startup

            check_at_startup()
