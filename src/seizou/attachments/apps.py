from django.apps import AppConfig


class AttachmentsConfig(AppConfig):
    """添付ファイル。記録に写真・検査成績書・作業手順などのファイルを付ける。

    呼ぶ相手は core だけ。付ける先は contenttypes で指す (ほかの部品の model と services を import しない)。
    """

    name = "seizou.attachments"
    label = "attachments"
    verbose_name = "添付ファイル"
    default_auto_field = "django.db.models.BigAutoField"
