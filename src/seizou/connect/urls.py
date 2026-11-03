"""connect の URL。URL の根 (seizou.core.urls) が connect/ の下に含める。

注文の受け口は /connect/receive/order/ になる。
画面の URL の名前は <model>_list / _detail の約束 (core の土台が使う)。受け入れると断るは届いた注文の行ごと。
注文回答 (承認か断りの結果) も、注文と同じ受け口 (receive) が受ける。
"""

from django.urls import path

from . import views

app_name = "connect"

#: 左のメニューに並べる入口 (core の context processor が読む)
#: 画面の名前は「届いた注文」。左のメニューの字は、core の試験 (tests/test_core_menu_sections.py) がこの字を見るので、
#: 本体がその試験を直すまで今の字のまま (N39 の記録の要望)。
MENU = [
    ("受信箱", "connect:inbox_list"),
]
# 取引先との接続は普段は触らないので、左のメニューでなく設定の画面 (core:settings) から開く (本人の指摘 11-03 昼)。

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
