from django.apps import AppConfig


class ReceivingConfig(AppConfig):
    """入荷。得意先から受け入れた現物 (無償支給・有償支給・預かり) の記録と、受注の明細への引当。

    呼ぶ相手は core と masters・orders の services だけ。ほかの部品の model は外部キーの字で指す。
    ready で、入荷の引当が無い受注の明細の指示書の発行と出荷を見るポカよけ (receiving.checks) を検査の層に登録する。
    """

    name = "seizou.receiving"
    label = "receiving"
    verbose_name = "入荷"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import checks

        checks.register_checks()
