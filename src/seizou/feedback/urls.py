"""feedback の URL (core の urls.py が /feedback/ に入れる)。

入口は左のメニューの下の「フィードバック」(core/_sidebar.html の include) で、部品の MENU には出さない。
画面の URL の名前は土台の約束 (feedback_list / _detail / _create / _update)。口は confirm・sent・fixed・delete と、AI の聞き取りの ask・reset。
GitHub の口 (依頼書 N185): 画面 github_setup (GitHub とつなぐ) と、口 github_disconnect・github_send・github_sync。
github_setup は sensitive_post_parameters で包み、開発の設定 (DEBUG) の誤りの画面と誤りの記録に、トークンの欄の値を出さない。
"""

from django.urls import path
from django.views.decorators.debug import sensitive_post_parameters

from . import views

app_name = "feedback"

#: 左のメニューの部品の入口を出さない (入口は左のメニューの下の 1 つと、入力の画面の「キャンセル」)。
MENU = []

urlpatterns = [
    path("", views.FeedbackListView.as_view(), name="feedback_list"),
    path("new/", views.FeedbackCreateView.as_view(), name="feedback_create"),
    path("<int:pk>/", views.FeedbackDetailView.as_view(), name="feedback_detail"),
    path("<int:pk>/edit/", views.FeedbackUpdateView.as_view(), name="feedback_update"),
    path("<int:pk>/confirm/", views.confirm, name="confirm"),
    path("<int:pk>/sent/", views.sent, name="sent"),
    path("<int:pk>/fixed/", views.fixed, name="fixed"),
    path("<int:pk>/delete/", views.delete, name="delete"),
    path("github/", sensitive_post_parameters("token")(views.GithubSetupView.as_view()), name="github_setup"),
    path("github/disconnect/", views.github_disconnect, name="github_disconnect"),
    path("github/sync/", views.github_sync, name="github_sync"),
    path("<int:pk>/github/", views.github_send, name="github_send"),
    path("ask/", views.ask, name="ask"),
    path("reset/", views.reset, name="reset"),
]
