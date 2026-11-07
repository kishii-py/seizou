"""feedback の URL (core の urls.py が /feedback/ に入れる)。

入口は左のメニューの下の「フィードバック」(core/_sidebar.html の include) で、部品の MENU には出さない。
画面の URL の名前は土台の約束 (feedback_list / _detail / _create / _update)。口は confirm・sent・fixed・delete と、AI の聞き取りの ask・reset。候補の口 screen_select (画面の欄の字を打って探す欄。GET)。
GitHub の口 (依頼書 N185): 画面 github_setup (GitHub とつなぐ) と、口 github_disconnect・github_send・github_sync。
github_setup は sensitive_post_parameters で包み、開発の設定 (DEBUG) の誤りの画面と誤りの記録に、トークンの欄の値を出さない。
GitHub でログインしてつなぐ (依頼書 N320): 口 github_device_start (始める)・github_device_poll (待ちの札が聞く)・github_device_cancel (やめる)。
Issue の返事 (依頼書 N324): 口 reply_add (書く)・reply_send (GitHub に送る)・reply_mark_sent (手で写した後に送ったにする)。
「うちも」(依頼書 N323): 口 me_too (みんなの Issue の番号。ログインした全員が押せる)。
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
    path("github/device/", views.github_device_start, name="github_device_start"),
    path("github/device/poll/", views.github_device_poll, name="github_device_poll"),
    path("github/device/cancel/", views.github_device_cancel, name="github_device_cancel"),
    path("issues/<int:number>/me-too/", views.me_too, name="me_too"),
    path("<int:pk>/github/", views.github_send, name="github_send"),
    path("ask/", views.ask, name="ask"),
    path("reset/", views.reset, name="reset"),
    # Issue の返事 (依頼書 N324): 書く (書いた人か世話役)・GitHub に送る・手で写した後に送ったにする (世話役)
    path("<int:pk>/replies/", views.reply_add, name="reply_add"),
    path("replies/<int:reply_pk>/send/", views.reply_send, name="reply_send"),
    path("replies/<int:reply_pk>/sent/", views.reply_mark_sent, name="reply_mark_sent"),
    path("screens/select/", views.screen_select, name="screen_select"),
]
