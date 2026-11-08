"""フィードバックの表。

現場の人のフィードバックの 1 件。下書きは会社の中に残り、トークンがあれば世話役が「GitHub に送る」で送り、無ければ Issue の文を
GitHub に手で写す。送った先の URL は sent_url に残す。書くのは services の関数だけ (R13)。
画面は URL の名前 (例 orders:order_list) で覚える。会話の記録は JSON の並びで持ち、詳細のテンプレートが吹き出しで描く。
送った件の Issue の返事は FeedbackReply に 1 件ずつ持つ (開発の記録 docs/nights/20261107/N324.md)。
"""

from django.conf import settings
from django.db import models


class Feedback(models.Model):
    """現場の人のフィードバックの 1 件。下書きは会社の中に残り、トークンがあれば世話役が「GitHub に送る」で送り、無ければ手で写す。書くのは services の関数だけ (R13)。"""

    #: 種類。前の 5 つは CONTRIBUTING.md の Issue の型の名前と並び (決め 11-04 14:4x の 7)。値の英字は既にある 4 つを残し、
    #: 足りない型 (決まりの反例・決まりの提案) だけ足した。使い方は CONTRIBUTING の型に無い (説明書で答える会社の中の問い) ので最後に置く。
    KIND_CHOICES = [
        ("bug", "不具合"),
        ("counter", "決まりの反例"),
        ("proposal", "決まりの提案"),
        ("company", "業務の流れの違い"),
        ("common", "要望"),
        ("usage", "使い方"),
    ]
    #: 見送った (dismissed) は、GitHub で対応しない・重複として閉じた Issue の件 (決め 11-07 09:0x〜09:4x)。直したと分けて、書いた人に見送りを知らせる
    STATUS_CHOICES = [("draft", "下書き"), ("confirmed", "確認済み"), ("sent", "送った"), ("fixed", "直した"), ("dismissed", "見送った")]

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
    fixed_at = models.DateTimeField("直した日時", null=True, blank=True)
    fixed_note = models.CharField("直した場所", max_length=200, blank=True)
    fixed_seen_at = models.DateTimeField("知らせた日時", null=True, blank=True)
    #: 見送りの日時と知らせた印は直したと別の列に持つ (直した日時の意味を変えない。決め 11-07 09:0x〜09:4x の 3)
    dismissed_at = models.DateTimeField("見送った日時", null=True, blank=True)
    dismissed_note = models.CharField("見送った理由の場所", max_length=200, blank=True)
    dismissed_seen_at = models.DateTimeField("見送りを知らせた日時", null=True, blank=True)
    #: 閉じた Issue のマイルストーンの名前 (直る版)。会社が入れている版がこれより古い間は送ったのまま、世話役に見せる (同じ決めの 4)
    fix_version = models.CharField("直る版", max_length=20, blank=True)

    #: 土台の詳細と小窓に出さない項目 (JSON をそのまま出さない。会話の記録・直した・見送った・直る版は詳細のテンプレートが節で描く)
    detail_hidden_fields = (
        "transcript",
        "fixed_at",
        "fixed_note",
        "fixed_seen_at",
        "dismissed_at",
        "dismissed_note",
        "dismissed_seen_at",
        "fix_version",
    )

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


class FeedbackReply(models.Model):
    """送ったフィードバックの Issue の返事の 1 件 (開発の記録 docs/nights/20261107/N324.md。決め 11-07 09:1x〜09:4x)。書くのは services の関数だけ (R13)。

    誰が何を書くか:
    - GitHub から読んだ返事 (作者や皆が Issue に書いた物): login あり・created_by 無し。取り込み (services.import_replies) が書き、
      github_id・posted_at・sent_at (posted_at と同じ) が入る。この会社の口座が書いた返事は、知らせた日時 (seen_at) も入れて知らせない。
    - 会社の中で書いた下書き (書いた人か世話役が詳細で書いた返事): created_by あり・sent_at 無し。github_id は空。
    - 送った返事 (世話役が伏せ字を直して GitHub に送った下書き): created_by と sent_at の両方あり。GitHub に送ったなら github_id と login も入る。
      手で写して「送ったにする」を押した物は sent_at だけが入る。
    """

    feedback = models.ForeignKey(Feedback, on_delete=models.CASCADE, related_name="replies", verbose_name="フィードバック")
    github_id = models.BigIntegerField("GitHub の id", null=True, blank=True, unique=True)
    login = models.CharField("書いた口座", max_length=100, blank=True)
    body = models.TextField("本文")
    posted_at = models.DateTimeField("GitHub に出た日時", null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="書いた人",
    )
    created_at = models.DateTimeField("書いた日時", auto_now_add=True)
    sent_at = models.DateTimeField("送った日時", null=True, blank=True)
    seen_at = models.DateTimeField("知らせた日時", null=True, blank=True)

    class Meta:
        verbose_name = "フィードバックの返事"
        verbose_name_plural = "フィードバックの返事"
        ordering = ("posted_at", "created_at", "pk")

    def __str__(self) -> str:
        return self.body[:40]
