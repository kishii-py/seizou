from django.apps import AppConfig


class NotesConfig(AppConfig):
    """書式つきのメモ。記録の詳細に、見出し・太字・箇条書き・色・写真・link のある自由記入のメモを 1 つ付ける。

    呼ぶ相手は core だけ。付ける先は contenttypes で指す (ほかの部品の model と services を import しない)。
    """

    name = "seizou.notes"
    label = "notes"
    verbose_name = "メモ"
    default_auto_field = "django.db.models.BigAutoField"
