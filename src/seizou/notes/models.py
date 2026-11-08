"""書式つきのメモの表 (決め 11-04 14:5x。開発の記録 docs/nights/20261104/N114.md)。

どの記録にも付けられるように、付ける先は contenttypes の種類と番号で指す (attachments と同じ GenericForeignKey)。
1 つの記録にメモは 1 つ (同じ付け先の 2 つ目は作らず、上書きする)。ほかの部品の表に列を足さない。
本文 (body_html) は services の掃除 (nh3) を通した HTML だけを入れる。写真は /media/notes/ の下に置く
(core.media の口がログインした人にだけ返す)。
"""

import os
import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone


def note_image_upload_to(instance, filename):
    """保存の名前。notes/<年>/<月>/<乱数 32 字><元の拡張子の小文字>。

    元の名前は保存の名前に使わない (本文の <img src> に日本語や空白を入れないため)。元の名前は original_name に残す。
    """
    extension = os.path.splitext(filename)[1].lower()
    today = timezone.localdate()
    return f"notes/{today:%Y}/{today:%m}/{uuid.uuid4().hex}{extension}"


class Note(models.Model):
    content_type = models.ForeignKey(ContentType, verbose_name="付けた先の種類", on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField("付けた先の番号")
    target = GenericForeignKey("content_type", "object_id")
    body_html = models.TextField("本文", blank=True)
    updated_at = models.DateTimeField("更新した日時", auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="更新した人",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name = "メモ"
        verbose_name_plural = "メモ"
        constraints = [
            models.UniqueConstraint(fields=["content_type", "object_id"], name="notes_note_one_per_record"),
        ]

    def __str__(self):
        target = self.target
        return f"{target} のメモ" if target is not None else "メモ"


class NoteImage(models.Model):
    note = models.ForeignKey(Note, verbose_name="メモ", on_delete=models.CASCADE, related_name="images")
    file = models.FileField("写真", upload_to=note_image_upload_to)
    original_name = models.CharField("ファイル名", max_length=100)
    size = models.PositiveBigIntegerField("大きさ (バイト)")
    uploaded_at = models.DateTimeField("貼った日時", auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="貼った人",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name = "メモの写真"
        verbose_name_plural = "メモの写真"
        ordering = ("-uploaded_at", "-pk")

    def __str__(self):
        return self.original_name
