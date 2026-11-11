"""progress の URL。名前の約束は <model>_list / _create (core の土台が使う)。"""

from django.urls import path

from . import views

app_name = "progress"

#: 左のメニューに並べる入口 (core の context processor が読む)
MENU = [("進捗", "progress:progress_list"), ("実績", "progress:record_list"), ("不良", "progress:progressdefect_list")]

urlpatterns = [
    path("", views.ProgressBoardView.as_view(), name="progress_list"),
    path("records/", views.ProgressListView.as_view(), name="record_list"),
    path("records/new/", views.ProgressCreateView.as_view(), name="progress_create"),
    # 実績を直す・消す画面も実績の一覧 (records/) の下に置く (開いた間は左のメニューの「実績」に印が付く)。
    path("records/<int:pk>/edit/", views.ProgressUpdateView.as_view(), name="progress_update"),
    path("records/<int:pk>/delete/", views.ProgressDeleteView.as_view(), name="progress_delete"),
    path("steps/<int:pk>/completed/", views.step_completed, name="step_completed"),
    # 不良の一覧と、原因と対策を書く画面 (N407b)。行の削除の画面は無い (行は実績の編集で消す)
    path("defects/", views.ProgressDefectListView.as_view(), name="progressdefect_list"),
    path("defects/<int:pk>/edit/", views.ProgressDefectUpdateView.as_view(), name="progressdefect_update"),
]
