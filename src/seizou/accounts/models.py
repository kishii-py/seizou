"""アカウント管理の表 (02 の 7 節)。

- Department: 部署。開ける部品 (seizou.parts.PARTS の短い名前の並び) と、部署を付けたときに付く既定の権限。
- Access: 利用者と 1 対 1 の、部署・編集の権限・金額の閲覧・フルコントロール・アカウント管理。
  削除は行を消さず deleted_at を入れる (記録の名前を残す)。使用停止は利用者の is_active。
- AccessLog: 発行・変更・使用停止・再開・削除の記録 (誰がいつ、前と後)。見る画面は無い。

利用者の表 (core.User) は変えない。書くのは services だけ (R13)。判定は seizou.core.access が持つ。
"""

from django.conf import settings
from django.db import models

from seizou.core import access


class Department(models.Model):
    name = models.CharField("部署", max_length=50, unique=True)
    apps = models.JSONField("開ける部品", default=list)
    default_permissions = models.JSONField("既定の権限", default=list)
    sort_order = models.PositiveIntegerField("並び順", default=0)

    class Meta:
        verbose_name = "部署"
        verbose_name_plural = "部署"
        ordering = ("sort_order", "pk")

    def __str__(self):
        return self.name


class Access(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name="ログイン ID",
        on_delete=models.CASCADE,
        related_name="seizou_access",
    )
    departments = models.ManyToManyField(Department, verbose_name="部署", blank=True, related_name="accesses")
    write_permissions = models.JSONField("編集の権限", default=list)
    see_money = models.BooleanField("金額の閲覧", default=False)
    full_control = models.BooleanField("フルコントロール", default=False)
    manage_accounts = models.BooleanField("アカウント管理", default=False)
    deleted_at = models.DateTimeField("削除した日時", null=True, blank=True)

    class Meta:
        verbose_name = "アカウントの権限"
        verbose_name_plural = "アカウントの権限"

    def __str__(self):
        return self.user.get_username()

    # --- 一覧の列 ---
    def department_names(self) -> str:
        """部署の名前を「・」で (無ければ空)。"""
        return "・".join(d.name for d in self.departments.all())

    def permission_names(self) -> str:
        """まとめと種類の表示名と、付いている「金額の閲覧」「フルコントロール」「アカウント管理」を「・」で。"""
        names = [access.permission_label(name) for name in access.PERMISSION_NAMES if name in (self.write_permissions or [])]
        if self.see_money:
            names.append("金額の閲覧")
        if self.full_control:
            names.append("フルコントロール")
        if self.manage_accounts:
            names.append("アカウント管理")
        return "・".join(names)

    def status_label(self) -> str:
        """「使用中」/「使用停止」/「削除」。"""
        if self.deleted_at is not None:
            return "削除"
        return "使用中" if self.user.is_active else "使用停止"

    def last_login(self):
        """利用者の最終ログイン (無ければ None)。"""
        return self.user.last_login


class AccessLog(models.Model):
    ACTION_ISSUE = "issue"
    ACTION_UPDATE = "update"
    ACTION_DISABLE = "disable"
    ACTION_ENABLE = "enable"
    ACTION_DELETE = "delete"
    ACTION_CHOICES = (
        (ACTION_ISSUE, "発行"),
        (ACTION_UPDATE, "変更"),
        (ACTION_DISABLE, "使用停止"),
        (ACTION_ENABLE, "再開"),
        (ACTION_DELETE, "削除"),
    )

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="変えた人", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    target = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="変えたアカウント", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    action = models.CharField("操作", max_length=20, choices=ACTION_CHOICES)
    before = models.JSONField("前", default=dict)
    after = models.JSONField("後", default=dict)
    password_changed = models.BooleanField("パスワードを変えた", default=False)
    created_at = models.DateTimeField("日時", auto_now_add=True)

    class Meta:
        verbose_name = "アカウントの変更の記録"
        verbose_name_plural = "アカウントの変更の記録"
        ordering = ("-created_at", "-pk")

    def __str__(self):
        return f"{self.get_action_display()} {self.created_at:%Y-%m-%d %H:%M}"
