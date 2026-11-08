"""AI チャットの表。1 行 = AI が書く道具の呼び出しを提案のカードにした 1 つ (AssistantProposal)。

書くのは services の関数だけ (R13)。会話の字は session に置き、表には持たない。
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class AssistantProposal(models.Model):
    """AI の提案 (受注の登録・出荷・工程の予定の置き直し) の 1 つと、承認の結果。

    決め: AI チャットの提案はこの表に持ち、session には提案の番号だけを置く。承認は短い atomic と select_for_update で
    承認待ちを処理中に移してから書く道具を呼ぶので、同じ提案は 1 度しか保存されない。session の書き戻しで状態が戻ると、
    同じ出荷や受注がもう 1 件できるため。11-03 の開発の記録 docs/nights/20261103/N40.md の「提案は session に持つ (表を作らない)」は表を第 5 波以降に
    残した作り方の決めで、表を断ったものではない。出所 = 本人の決め (11-07 23:3x。案 A = 表を足す)。
    変えてよい条件 = 提案を DB に持たない形で、process を増やしても二度目を断れる仕組みができたとき。
    表に書くのは seizou.assistant.services の propose と approve だけ (R13)。
    """

    STATE_PENDING = "pending"
    STATE_WARNED = "warned"
    STATE_RUNNING = "running"
    STATE_SAVED = "saved"
    STATE_REFUSED = "refused"
    STATE_CHOICES = [
        (STATE_PENDING, "承認待ち"),
        (STATE_WARNED, "知らせ"),
        (STATE_RUNNING, "処理中"),
        (STATE_SAVED, "保存済み"),
        (STATE_REFUSED, "断った"),
    ]

    proposal_id = models.CharField("提案の番号", max_length=32, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="提案を作った人",
    )
    tool = models.CharField("道具", max_length=40)
    title = models.CharField("題", max_length=100, blank=True)
    arguments = models.JSONField("引数", default=dict, blank=True)
    summary = models.JSONField("要約の行", default=list, blank=True)
    # 列の名前は status にしない (core の状態の色の表は、choices を持つ status の列を全部の部品から集めるため)。
    state = models.CharField("状態", max_length=10, choices=STATE_CHOICES, default=STATE_PENDING)
    notice = models.TextField("知らせの文", blank=True)
    code = models.CharField("決まりの番号", max_length=20, blank=True)
    warnings = models.JSONField("ポカよけの知らせ", default=list, blank=True)
    url = models.CharField("保存した物の道", max_length=500, blank=True)
    created_at = models.DateTimeField("作った日時", default=timezone.now)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="決めた人",
    )
    decided_at = models.DateTimeField("決めた日時", null=True, blank=True)

    class Meta:
        verbose_name = "AI の提案"
        verbose_name_plural = "AI の提案"
        ordering = ("pk",)

    def __str__(self) -> str:
        return f"{self.title or self.tool} ({self.proposal_id})"
