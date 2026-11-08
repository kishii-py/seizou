from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """アカウント管理。アカウントごとに部署と権限を付け、使える範囲を決める (アカウントと権限の設計の決め)。

    呼ぶ相手は core だけ。判定は core の access が持つ (この部品は表と services と画面だけ)。
    """

    name = "seizou.accounts"
    label = "accounts"
    verbose_name = "アカウント管理"
    default_auto_field = "django.db.models.BigAutoField"
