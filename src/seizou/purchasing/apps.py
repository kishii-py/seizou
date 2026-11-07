from django.apps import AppConfig


class PurchasingConfig(AppConfig):
    """発注。協力企業への発注と発注の明細。呼ぶ相手は core と masters と connect だけ。

    ready で、送った発注の変更と取消の取適法の確かめ (purchasing.checks) を検査の層に登録する。
    """

    name = "seizou.purchasing"
    label = "purchasing"
    verbose_name = "発注"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import checks

        checks.register_checks()
