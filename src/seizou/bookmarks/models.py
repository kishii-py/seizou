"""ブックマークの表。1 行 = 1 人が登録した 1 つの画面。

画面は URL の名前 (例 orders:order_list) で覚える。label は登録したときの画面の名前の控えで、
画面の目録から見つからなくなった画面の表示にだけ使う (ふだんの表示は目録の今の名前)。
書くのは services の関数だけ (R13)。
"""

from django.conf import settings
from django.db import models


class Bookmark(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bookmarks", verbose_name="利用者"
    )
    url_name = models.CharField("画面 (URL の名前)", max_length=100)
    label = models.CharField("登録したときの画面の名前", max_length=100)
    position = models.PositiveIntegerField("並び")
    created_at = models.DateTimeField("登録した日時", auto_now_add=True)

    class Meta:
        verbose_name = "ブックマーク"
        verbose_name_plural = "ブックマーク"
        ordering = ("position", "pk")
        constraints = [
            # 連打と 2 つのタブで、同じ人の同じ画面に 2 行を作らない。
            models.UniqueConstraint(fields=["user", "url_name"], name="bookmarks_unique_user_url_name"),
        ]

    def __str__(self) -> str:
        return self.label
