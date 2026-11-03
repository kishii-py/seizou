from django.apps import AppConfig


class FeedbackConfig(AppConfig):
    """フィードバック。現場の人が困りごと・要望・不具合を伝える窓。

    呼ぶ相手は core と assistant (鍵・API・道具の回し方) だけ。下書きは会社の中に残り、GitHub へは送らない (世話役が写す)。
    """

    name = "seizou.feedback"
    label = "feedback"
    verbose_name = "フィードバック"
    default_auto_field = "django.db.models.BigAutoField"
