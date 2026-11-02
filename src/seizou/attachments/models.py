"""添付ファイルの表 (02 の 5 節の N30)。

どの記録にも付けられるように、付ける先は contenttypes の種類と番号で指す (GenericForeignKey)。
ほかの部品の表に列を足さず、ほかの部品の model を import しない。
ファイルはログインした人にだけ返す口 (attachments:attachment_download) を通して開く。
"""

import os
import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.urls import reverse
from django.utils import timezone

#: 添付ファイルの種類 (値, 画面の字)
KIND_CHOICES = (
    ("photo", "写真"),
    ("inspection", "検査成績書"),
    ("procedure", "作業手順"),
    ("other", "その他"),
)

#: 付けた先の記録が無いときの「対象」の字
DELETED_TARGET_LABEL = "(削除された記録)"


def attachment_upload_to(instance, filename):
    """保存の名前。attachments/<年>/<月>/<乱数 32 字><元の拡張子の小文字>。

    元の名前は保存の名前に使わない (開発の media の道から名前を当てて開けないように)。
    元の名前は original_name に残し、口が返すときのファイル名にする。
    """
    extension = os.path.splitext(filename)[1].lower()
    today = timezone.localdate()
    return f"attachments/{today:%Y}/{today:%m}/{uuid.uuid4().hex}{extension}"


class Attachment(models.Model):
    content_type = models.ForeignKey(ContentType, verbose_name="付けた先の種類", on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField("付けた先の番号")
    target = GenericForeignKey("content_type", "object_id")
    file = models.FileField("ファイル", upload_to=attachment_upload_to)
    original_name = models.CharField("ファイル名", max_length=100)
    kind = models.CharField("種類", max_length=20, choices=KIND_CHOICES, default="other")
    mime = models.CharField("形式", max_length=100)
    size = models.PositiveBigIntegerField("大きさ (バイト)")
    note = models.CharField("備考", max_length=500, blank=True)
    uploaded_at = models.DateTimeField("付けた日時", auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="付けた人",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name = "添付ファイル"
        verbose_name_plural = "添付ファイル"
        ordering = ("-uploaded_at", "-pk")
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return self.original_name

    def file_url(self, field_name: str) -> str:
        """core の詳細の土台がファイルの項目の URL を作るときに読む約束 (土台の file_url_for)。

        file の項目はログインした人にだけ返す口の URL。ほかの項目と保存前の行は空の字 (土台が storage の URL を使う)。
        """
        if field_name != "file" or self.pk is None:
            return ""
        return reverse("attachments:attachment_download", args=[self.pk])

    def target_label(self) -> str:
        """付けた先の記録の字 (一覧の「対象」の列)。付けた先が無ければ「(削除された記録)」。"""
        target = self.target
        return str(target) if target is not None else DELETED_TARGET_LABEL
