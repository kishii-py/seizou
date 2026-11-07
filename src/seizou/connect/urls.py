"""connect の URL。URL の根 (seizou.core.urls) が connect/ の下に含める。

注文の受け口は /connect/receive/order/ になる。
画面の URL の名前は <model>_list / _detail の約束 (core の土台が使う)。受け入れると断るは受信した注文の行ごと。差出人を選ぶも同じ。
注文回答 (承認か断りの結果) も、注文と同じ受け口 (receive) が受ける。
送り合った記録 (送った物と受けた物の中身。読むだけ) は exchanges/ (exchange_list) と exchanges/<pk>/ (exchange_detail)。左のメニューでなく設定の画面から開く。
会話 (受注と発注の 1 件ごとの相手とのやり取り) の書く画面は chat/new/ (名前を _create にしない = 一覧の土台の「新規登録」と取り違えない)、送り直す口は chat/<pk>/resend/。添付を開く口は chat/files/<pk>/、相手から届いた添付を取りに行く口は chat/files/<pk>/fetch/。
"""

from django.urls import path

from . import views
from . import chat
from . import reply
from . import sender

app_name = "connect"

#: 左のメニューに並べる入口 (core の context processor が読む)
#: 左のメニューの字も画面の名前も「受信した注文」(決め 11-03 の候補 3。「受信箱」は使わない)。
MENU = [
    ("受信した注文", "connect:inbox_list"),
    # 得意先 (seizou どうし) から届いた仕入明細 (候補 C111)。届いた物に気づく入口なので、受信した注文の次に置く。
    ("受けた仕入明細", "connect:receivedselfinvoice_list"),
]
# 取引先との接続は普段は触らないので、左のメニューでなく設定の画面 (core:settings) から開く (本人の指摘 11-03 昼)。

urlpatterns = [
    path("receive/order/", views.receive_order, name="receive"),
    path("inbox/", views.InboxListView.as_view(), name="inbox_list"),
    path("inbox/<int:pk>/", views.InboxDetailView.as_view(), name="inbox_detail"),
    path("inbox/<int:pk>/accept/", views.InboxAcceptView.as_view(), name="inbox_accept"),
    # 単価を直して受け入れると保留する (中小企業共通 EDI の条件付受諾と保留)。受け入れの URL (accept/) の下に置かない
    # (受け入れの URL が字に無いことを見る試験と取り違えないため)。
    path("inbox/<int:pk>/conditional/", views.InboxConditionalAcceptView.as_view(), name="inbox_accept_conditional"),
    path("inbox/<int:pk>/hold/", views.InboxHoldView.as_view(), name="inbox_hold"),
    path("inbox/<int:pk>/reject/", views.InboxRejectView.as_view(), name="inbox_reject"),
    path("inbox/<int:pk>/reply/resend/", reply.resend_reply, name="inbox_reply_resend"),
    path("inbox/<int:pk>/sender/", sender.choose_sender, name="inbox_sender"),
    path("connections/", views.ConnectionListView.as_view(), name="connection_list"),
    path("connections/new/", views.ConnectionCreateView.as_view(), name="connection_create"),
    path("connections/<int:pk>/", views.ConnectionDetailView.as_view(), name="connection_detail"),
    path("connections/<int:pk>/edit/", views.ConnectionUpdateView.as_view(), name="connection_update"),
    path("exchanges/", views.ExchangeListView.as_view(), name="exchange_list"),
    path("exchanges/<int:pk>/", views.ExchangeDetailView.as_view(), name="exchange_detail"),
    path("selfinvoices/", views.ReceivedSelfInvoiceListView.as_view(), name="receivedselfinvoice_list"),
    path("selfinvoices/<int:pk>/", views.ReceivedSelfInvoiceDetailView.as_view(), name="receivedselfinvoice_detail"),
    path("chat/new/", views.ChatMessageAddView.as_view(), name="chatmessage_add"),
    path("chat/<int:pk>/resend/", chat.resend_chat, name="chatmessage_resend"),
    path("chat/files/<int:pk>/", chat.chat_file_download, name="chatfile_download"),
    path("chat/files/<int:pk>/fetch/", chat.fetch_chat_file, name="chatfile_fetch"),
]
