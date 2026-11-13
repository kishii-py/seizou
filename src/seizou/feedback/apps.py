from django.apps import AppConfig


class FeedbackConfig(AppConfig):
    """フィードバック。現場の人が困りごと・要望・不具合を伝える窓。

    呼ぶ相手は core と assistant (鍵・API・道具の回し方) だけ。下書きは会社の中に残る。アカウント管理の担当者がトークンを置いた会社では、
    確認済みの物を「GitHub に送る」で Issue にする (github.py)。トークンが無い会社は、アカウント管理の担当者が Issue の文を手で写す。
    """

    name = "seizou.feedback"
    label = "feedback"
    verbose_name = "フィードバック"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # 書いた本人がログインしたときの「直した」の知らせ (user_logged_in の受け手)。
        from . import signals  # noqa: F401
