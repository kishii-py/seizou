"""attachments の URL (core の urls.py が /attachments/ に入れる)。"""

from django.urls import path

from . import views

app_name = "attachments"

urlpatterns = [
    path("<int:pk>/download/", views.attachment_download, name="attachment_download"),
]
