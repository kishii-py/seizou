"""attachments の URL (core の urls.py が /attachments/ に入れる)。

名前の約束は <model>_list / _detail (core の土台が使う)。付ける画面は attachment_add
(attachment_create にすると、土台の一覧が「新規登録」のボタンを出すため)。
"""

from django.urls import path

from . import views

app_name = "attachments"

#: 左のメニューに並べる入口 (core の context processor が読む。区分は「その他」)
MENU = [("添付ファイル", "attachments:attachment_list")]

urlpatterns = [
    path("", views.AttachmentListView.as_view(), name="attachment_list"),
    path("new/", views.AttachmentAddView.as_view(), name="attachment_add"),
    path("<int:pk>/", views.attachment_detail, name="attachment_detail"),
    path("<int:pk>/delete/", views.AttachmentDeleteView.as_view(), name="attachment_delete"),
    path("<int:pk>/download/", views.attachment_download, name="attachment_download"),
]
