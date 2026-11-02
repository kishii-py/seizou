from django.apps import AppConfig


class BookmarksConfig(AppConfig):
    """ブックマーク。よく使う画面を、その人ごとに ☆ で登録する。

    呼ぶ相手は core だけ。画面は URL の名前で覚える (リンク先の字は覚えず、表示のたびに reverse で作る)。
    """

    name = "seizou.bookmarks"
    label = "bookmarks"
    verbose_name = "ブックマーク"
    default_auto_field = "django.db.models.BigAutoField"
