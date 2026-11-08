"""業務マニュアルの表 (業務マニュアルの設計の決め)。フォルダ・マニュアル・版・画像の 4 つ。

書くのは services だけ (R13)。本文は Markdown の字で持ち、描くのは manuals.markdown の 1 か所。
本文の画像は `![説明](image:<番号>)` の形で ManualImage の pk を指す。同じマニュアルの画像だけが解ける。
"""

from __future__ import annotations

import os
import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.urls import reverse
from django.utils import timezone

#: フォルダの 7 色 (値は色の名前の英字、表示は日本語)。印は _folder_color.html が要素ごとに書き分ける
COLOR_CHOICES = (
    ("gray", "灰"),
    ("red", "赤"),
    ("yellow", "黄"),
    ("green", "緑"),
    ("blue", "青"),
    ("pink", "桃"),
    ("teal", "青緑"),
)

FORMAT_STEPS = "steps"
FORMAT_PAGE = "page"
FORMAT_CHOICES = (
    (FORMAT_STEPS, "ステップ形式"),
    (FORMAT_PAGE, "ページ形式"),
)

#: 親を辿る段の上限 (壊れた親の輪で止まらないため)
MAX_DEPTH = 50

DELETED_LABEL = "(削除された記録)"


class ManualFolder(models.Model):
    parent = models.ForeignKey(
        "self",
        verbose_name="親のフォルダ",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    name = models.CharField("名前", max_length=100)
    color = models.CharField("色", max_length=10, choices=COLOR_CHOICES, default="gray")
    protected = models.BooleanField("保護", default=False)
    sort_order = models.PositiveIntegerField("並び", default=0)

    class Meta:
        verbose_name = "マニュアルのフォルダ"
        verbose_name_plural = "マニュアルのフォルダ"
        ordering = ("sort_order", "name", "pk")

    def __str__(self) -> str:
        return self.name

    def path(self) -> list[ManualFolder]:
        """ルートの直下から自分までのフォルダの並び (親を辿る。MAX_DEPTH 段で打ち切る)。"""
        chain: list[ManualFolder] = []
        node: ManualFolder | None = self
        while node is not None and len(chain) < MAX_DEPTH:
            chain.append(node)
            node = node.parent
        chain.reverse()
        return chain

    def path_text(self) -> str:
        return " / ".join(folder.name for folder in self.path())


class Manual(models.Model):
    folder = models.ForeignKey(
        ManualFolder,
        verbose_name="フォルダ",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="manuals",
    )
    title = models.CharField("題", max_length=200)
    format = models.CharField("形式", max_length=10, choices=FORMAT_CHOICES, default=FORMAT_STEPS)
    body = models.TextField("本文", blank=True)
    protected = models.BooleanField("保護", default=False)
    version = models.PositiveIntegerField("版", default=1)
    linked_type = models.ForeignKey(
        ContentType,
        verbose_name="紐付け先の種類",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    linked_id = models.PositiveBigIntegerField("紐付け先の番号", null=True, blank=True)
    linked = GenericForeignKey("linked_type", "linked_id")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="作った人",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField("作った日時", auto_now_add=True)
    updated_at = models.DateTimeField("最終更新", auto_now=True)

    #: 見る画面の基本情報に出さない列 (Markdown の字と紐付けの番号。紐付けは帯に字で出す)
    detail_hidden_fields = ("body", "linked_type", "linked_id")

    class Meta:
        verbose_name = "マニュアル"
        verbose_name_plural = "マニュアル"
        ordering = ("title", "pk")

    def __str__(self) -> str:
        return self.title

    def folder_path_text(self) -> str:
        """フォルダの道 (フォルダに入っていなければ「トップ」)。一覧の「フォルダ」の列と横断検索が使う。"""
        return self.folder.path_text() if self.folder_id else "トップ"

    def linked_object(self):
        """紐付け先の記録。紐付けが無いか、紐付け先が消えていれば None。"""
        if self.linked_type_id is None or self.linked_id is None:
            return None
        try:
            return self.linked
        except (AttributeError, LookupError):
            return None

    def linked_label(self) -> str:
        """紐付け先の str()。紐付けが無ければ空の字、紐付け先が消えていれば「(削除された記録)」。"""
        if self.linked_type_id is None or self.linked_id is None:
            return ""
        obj = self.linked_object()
        return DELETED_LABEL if obj is None else str(obj)


class ManualVersion(models.Model):
    manual = models.ForeignKey(Manual, verbose_name="マニュアル", on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField("版の番号")
    title = models.CharField("題", max_length=200)
    body = models.TextField("本文", blank=True)
    note = models.CharField("メモ", max_length=200, blank=True)
    saved_at = models.DateTimeField("日時", auto_now_add=True)
    saved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="保存した人",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name = "マニュアルの版"
        verbose_name_plural = "マニュアルの版"
        ordering = ("-number",)
        constraints = [models.UniqueConstraint(fields=("manual", "number"), name="manuals_version_number_unique")]

    def __str__(self) -> str:
        return f"版 {self.number}"


def image_upload_to(instance, filename: str) -> str:
    """画像の保存の名前。manuals/<年>/<月>/<uuid4 の hex><元の拡張子の小文字>。元の名前は使わない。"""
    ext = os.path.splitext(filename)[1].lower()
    now = timezone.localtime()
    return f"manuals/{now:%Y}/{now:%m}/{uuid.uuid4().hex}{ext}"


class ManualImage(models.Model):
    manual = models.ForeignKey(Manual, verbose_name="マニュアル", on_delete=models.CASCADE, related_name="images")
    file = models.FileField("ファイル", upload_to=image_upload_to)
    original_name = models.CharField("元の名前", max_length=100)
    size = models.PositiveBigIntegerField("大きさ (バイト)")
    uploaded_at = models.DateTimeField("日時", auto_now_add=True)

    class Meta:
        verbose_name = "マニュアルの画像"
        verbose_name_plural = "マニュアルの画像"
        ordering = ("pk",)

    def __str__(self) -> str:
        return self.original_name

    def file_url(self, field_name: str) -> str:
        """ファイルの URL (ログインした人にだけ返す口)。土台の詳細の画面が読む。"""
        if field_name == "file":
            return reverse("manuals:manualimage_download", args=[self.pk])
        return ""
