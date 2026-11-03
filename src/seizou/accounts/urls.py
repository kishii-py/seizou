"""accounts の URL (core の urls.py が /accounts/ に入れる)。名前は model (Access) の約束 <model>_list / _detail / _create / _update。

行の移り先 (access_detail) は編集へ移るだけの関数の view。削除 (access_delete) は編集の画面の「削除」から入る。
"""

from django.urls import path

from . import views

app_name = "accounts"

#: 左のメニューに並べる入口 (アカウント管理を使えない人には core の access.visible_menu が出さない)
MENU = [
    ("アカウント管理", "accounts:access_list"),
]

urlpatterns = [
    path("", views.AccessListView.as_view(), name="access_list"),
    path("new/", views.AccessCreateView.as_view(), name="access_create"),
    path("<int:pk>/", views.access_detail, name="access_detail"),
    path("<int:pk>/edit/", views.AccessUpdateView.as_view(), name="access_update"),
    path("<int:pk>/delete/", views.AccessDeleteView.as_view(), name="access_delete"),
]
