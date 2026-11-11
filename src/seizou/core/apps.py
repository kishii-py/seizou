from django.apps import AppConfig


class CoreConfig(AppConfig):
    """土台。ほかの部品はこの app と、相手の services だけを呼ぶ。"""

    name = "seizou.core"
    label = "core"
    verbose_name = "土台"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # 端末の文字コードで出せない字を ? にして書く (置き場の manage.py の命令が、Windows の cp1252 や cp932 の出力で止まらないように)。
        # 置き場の manage.py を書き換えなくても効くよう、app の読み込みのときに最初に行う (seizou.console。命令の出力より前に走る)
        from seizou.console import replace_unencodable_output

        replace_unencodable_output()

        # 標準のポカよけ (R01・R04・R06・R07・R24・R26・R28) を検査の層に登録する。
        from .checks import builtin  # noqa: F401
        # 一覧の列 (columns) の書き方を、Django の起動の検査 (system check) に登録する。
        from . import system_checks  # noqa: F401
        # ログインの成功・失敗・ログアウトを記録 (seizou.login) に 1 行ずつ書く signal の受け手をつなぐ。
        from . import login_log  # noqa: F401

        # 選ぶ欄の空の選択肢の字 (Django の既定は英語の「- Select an option -」)。Django 6.1 は ready() での置き換えを認める。
        # 表の choices の欄・ModelChoiceField・RemoteSelect の空の選択肢が、この字になる (form の class を作る前に置く)。
        from django.db.models import fields as model_fields

        from .forms import EMPTY_LABEL

        model_fields.BLANK_CHOICE_LABEL = EMPTY_LABEL

        # 開発の設定 (DEBUG) のときだけ、起動時に決まりの検査 (書き方の決まり) を回す。
        # 本番の形では検査の module を読まない (決まりの検査の設計の決め)。判定を import の前に置く。
        from django.conf import settings

        if settings.DEBUG:
            from .rules import check_at_startup

            check_at_startup()
