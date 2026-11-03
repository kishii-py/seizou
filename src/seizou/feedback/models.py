"""フィードバックの表。

現場の人のフィードバックの 1 件。下書きは会社の中に残り、世話役が Issue の文を GitHub に写す。書くのは services の関数だけ (R13)。
画面は URL の名前 (例 orders:order_list) で覚える。会話の記録は JSON の並びで持ち、詳細のテンプレートが吹き出しで描く。
"""

from django.conf import settings
from django.db import models


class Feedback(models.Model):
    """現場の人のフィードバックの 1 件。下書きは会社の中に残り、世話役が Issue の文を GitHub に写す。書くのは services の関数だけ (R13)。"""

    KIND_CHOICES = [("usage", "使い方"), ("company", "自社の作業"), ("common", "皆の要望"), ("bug", "不具合")]
    STATUS_CHOICES = [("draft", "下書き"), ("confirmed", "確認済み"), ("sent", "送った")]

    kind = models.CharField("種類", max_length=10, choices=KIND_CHOICES, default="common")
    screen = models.CharField("画面 (URL の名前)", max_length=100, blank=True)
    title = models.CharField("題", max_length=100)
    problem = models.TextField("困りごと")
    situation = models.TextField("場面", blank=True)
    wish = models.TextField("望む形", blank=True)
    screens = models.TextField("影響する画面 (URL の名前。空白で区切る)", blank=True)
    status = models.CharField("状態", max_length=10, choices=STATUS_CHOICES, default="draft")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="書いた人",
    )
    created_at = models.DateTimeField("書いた日時", auto_now_add=True)
    updated_at = models.DateTimeField("変えた日時", auto_now=True)
    input_tokens = models.PositiveIntegerField("入力 token", default=0)
    output_tokens = models.PositiveIntegerField("出力 token", default=0)
    transcript = models.JSONField("会話の記録", default=list, blank=True)
    sent_url = models.URLField("送った先 (URL)", blank=True)

    #: 土台の詳細と小窓に出さない項目 (JSON をそのまま出さない。会話の記録は詳細のテンプレートが描く)
    detail_hidden_fields = ("transcript",)

    class Meta:
        verbose_name = "フィードバック"
        verbose_name_plural = "フィードバック"
        ordering = ("-created_at", "-pk")

    def __str__(self) -> str:
        return self.title

    def get_screen_display(self) -> str:
        """画面の表示。目録にある画面なら「名前 (URL の名前)」、目録に無ければ URL の名前、空なら空の字。

        土台の display_value が get_<name>_display を使うので、一覧と詳細に画面の名前が出る。
        services は models を import するので、目録はこの method の中で import して引く (頭で import すると輪になる)。
        """
        if not self.screen:
            return ""
        from seizou.feedback.services import screen_name

        name = screen_name(self.screen)
        return f"{name} ({self.screen})" if name else self.screen
