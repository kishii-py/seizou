"""connect の URL。URL の根 (seizou.core.urls) が connect/ の下に含める。

注文の受け口は /connect/receive/order/ になる。
画面の URL の名前は <model>_list / _detail の約束 (core の土台が使う)。受け入れると断るは受信箱の行ごと。
"""

from django.urls import path

from . import views

app_name = "connect"

#: 左のメニューに並べる入口 (core の context processor が読む)
MENU = [
    ("受信箱", "connect:inbox_list"),
]
# つながり (取引先との接続) は普段は触らないので、左のメニューでなく設定の画面 (core:settings) から開く (本人の指摘 11-03 昼)。

urlpatterns = [
    path("receive/order/", views.receive_order, name="receive"),
    path("inbox/", views.InboxListView.as_view(), name="inbox_list"),
    path("inbox/<int:pk>/", views.InboxDetailView.as_view(), name="inbox_detail"),
    path("inbox/<int:pk>/accept/", views.InboxAcceptView.as_view(), name="inbox_accept"),
    path("inbox/<int:pk>/reject/", views.InboxRejectView.as_view(), name="inbox_reject"),
    path("connections/", views.ConnectionListView.as_view(), name="connection_list"),
    path("connections/new/", views.ConnectionCreateView.as_view(), name="connection_create"),
    path("connections/<int:pk>/", views.ConnectionDetailView.as_view(), name="connection_detail"),
    path("connections/<int:pk>/edit/", views.ConnectionUpdateView.as_view(), name="connection_update"),
]
