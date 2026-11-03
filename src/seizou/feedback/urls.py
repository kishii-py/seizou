"""feedback の URL (core の urls.py が /feedback/ に入れる)。

入口は左のメニューの下の「フィードバック」(core/_sidebar.html の include) で、部品の MENU には出さない。
画面の URL の名前は土台の約束 (feedback_list / _detail / _create / _update)。口は confirm・sent と、AI の聞き取りの ask・reset。
"""

from django.urls import path

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
    path("ask/", views.ask, name="ask"),
    path("reset/", views.reset, name="reset"),
]
