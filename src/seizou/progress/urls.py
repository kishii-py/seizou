"""progress の URL。名前の約束は <model>_list / _create (core の土台が使う)。"""

from django.urls import path

from . import views

app_name = "progress"

#: 左のメニューに並べる入口 (core の context processor が読む)
MENU = [("進捗", "progress:progress_list"), ("実績", "progress:record_list")]

urlpatterns = [
    path("", views.ProgressBoardView.as_view(), name="progress_list"),
    path("records/", views.ProgressListView.as_view(), name="record_list"),
    path("new/", views.ProgressCreateView.as_view(), name="progress_create"),
]
