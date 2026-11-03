from django.apps import AppConfig


class StockConfig(AppConfig):
    """在庫。完成品の在庫。表は在庫の動きだけで、在庫数は動きの合計から出す。

    呼ぶ相手は core だけ (部品は外部キーの字で指し、指示書・受入・出荷は汎用の参照と集計で読む)。
    """

    name = "seizou.stock"
    label = "stock"
    verbose_name = "在庫"
    default_auto_field = "django.db.models.BigAutoField"
