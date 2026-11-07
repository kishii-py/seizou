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


def app_labels() -> dict[str, str]:
    """部署の「開ける部品」の表示 (左のメニューの入口の字)。入口は要求ごとに会社の設定で変わるので、呼ぶたびに作る。

    部品の app の名前 → 入口の字。同じ app の入口は「・」でつなぐ (例 masters →「部品マスター・取引先・工程」)。
    入口の無い app (intake など) は入らない (呼ぶ側が access.app_label で部品の表示名を補う)。
    """
    from seizou.core.context_processors import menu

    labels: dict[str, list[str]] = {}
    for item in menu():
        names = labels.setdefault(item["app"], [])
        if item["label"] not in names:
            names.append(item["label"])
    return {app: "・".join(names) for app, names in labels.items()}


class Department(models.Model):
    name = models.CharField("部署", max_length=50, unique=True)
    apps = models.JSONField("開ける画面", default=list)
    default_permissions = models.JSONField("既定の権限", default=list)
    sort_order = models.PositiveIntegerField("並び順", default=0)

    #: 変更の履歴 (core.history。決め HIST-1 の「アカウントと部署」のうち詳細の画面のある部署) を残し、詳細に「履歴」のタブを出す。
    #: アカウント (Access と利用者) には付けない (決め C106-2 = a。今の AccessLog のまま書き、新しい表に二重に書かない)
    keeps_history = True

    class Meta:
        verbose_name = "部署"
        verbose_name_plural = "部署"
        ordering = ("sort_order", "pk")

    def __str__(self):
        return self.name

    # --- 一覧と詳細の表示 (土台の display_value が get_<名前>_display を先に見る。一覧・詳細・CSV の全部がこの字になる) ---
    def get_apps_display(self) -> str:
        """開ける部品を左のメニューの入口の字で「・」でつなぐ (入口の無い部品は部品の表示名。空なら空の字)。"""
        labels = app_labels()
        return "・".join(labels.get(app) or access.app_label(app) for app in (self.apps or []))

    def get_default_permissions_display(self) -> str:
        """既定の権限の表示名を「・」で (画面の並び = まとめ 2 つ → 種類 10。空なら空の字)。"""
        names = self.default_permissions or []
        return "・".join(access.permission_label(name) for name in access.PERMISSION_NAMES if name in names)

    @property
    def members(self):
        """所属する生きている人 (Access の queryset。削除済みは出さない)。詳細の「所属する人」のタブが読む。"""
        return self.accesses.filter(deleted_at__isnull=True).select_related("user").order_by("user__username")

    def member_count(self) -> int:
        """所属する生きている人の数 (一覧の「人数」の列)。"""
        return self.members.count()


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
