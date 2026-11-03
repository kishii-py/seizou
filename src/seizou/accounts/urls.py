"""accounts の URL (core の urls.py が /accounts/ に入れる)。名前は model (Access) の約束 <model>_list / _detail / _create / _update。

行の移り先 (access_detail) は編集へ移るだけの関数の view。削除 (access_delete) は編集の画面の「削除」から入る。
"""

from django.urls import path

from . import views

app_name = "accounts"

#: 左のメニューには出さない (普段は触らない物は右上の「設定」から開く = 決め 11-03 の候補 7)。設定の画面が入口を持つ。
MENU = []

urlpatterns = [
    path("", views.AccessListView.as_view(), name="access_list"),
    path("new/", views.AccessCreateView.as_view(), name="access_create"),
    path("<int:pk>/", views.access_detail, name="access_detail"),
    path("<int:pk>/edit/", views.AccessUpdateView.as_view(), name="access_update"),
    path("<int:pk>/delete/", views.AccessDeleteView.as_view(), name="access_delete"),
]
