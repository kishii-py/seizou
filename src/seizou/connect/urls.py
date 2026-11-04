"""connect の URL。URL の根 (seizou.core.urls) が connect/ の下に含める。

注文の受け口は /connect/receive/order/ になる。
画面の URL の名前は <model>_list / _detail の約束 (core の土台が使う)。受け入れると断るは受信した注文の行ごと。差出人を選ぶも同じ。
注文回答 (承認か断りの結果) も、注文と同じ受け口 (receive) が受ける。
"""

from django.urls import path

from . import views
from . import reply
from . import sender

app_name = "connect"

#: 左のメニューに並べる入口 (core の context processor が読む)
#: 左のメニューの字も画面の名前も「受信した注文」(決め 11-03 の候補 3。「受信箱」は使わない)。
MENU = [
    ("受信した注文", "connect:inbox_list"),
]
# 取引先との接続は普段は触らないので、左のメニューでなく設定の画面 (core:settings) から開く (本人の指摘 11-03 昼)。

urlpatterns = [
    path("receive/order/", views.receive_order, name="receive"),
    path("inbox/", views.InboxListView.as_view(), name="inbox_list"),
    path("inbox/<int:pk>/", views.InboxDetailView.as_view(), name="inbox_detail"),
    path("inbox/<int:pk>/accept/", views.InboxAcceptView.as_view(), name="inbox_accept"),
    path("inbox/<int:pk>/reject/", views.InboxRejectView.as_view(), name="inbox_reject"),
    path("inbox/<int:pk>/reply/resend/", reply.resend_reply, name="inbox_reply_resend"),
    path("inbox/<int:pk>/sender/", sender.choose_sender, name="inbox_sender"),
    path("connections/", views.ConnectionListView.as_view(), name="connection_list"),
    path("connections/new/", views.ConnectionCreateView.as_view(), name="connection_create"),
    path("connections/<int:pk>/", views.ConnectionDetailView.as_view(), name="connection_detail"),
    path("connections/<int:pk>/edit/", views.ConnectionUpdateView.as_view(), name="connection_update"),
]
