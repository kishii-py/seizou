from django.apps import AppConfig


class ConnectConfig(AppConfig):
    """取引先との接続 (取引先ごとの受け口と鍵の置き場)、受け口、受信した注文、注文回答。呼ぶ相手は core・masters・orders・purchasing。

    表示名は本人の括りの名前「受注管理」(権限の 403 の文と、使い方の目録の区分に出る)。
    """

    name = "seizou.connect"
    label = "connect"
    verbose_name = "受注管理"
    default_auto_field = "django.db.models.BigAutoField"
