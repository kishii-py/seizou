"""connect の URL。URL の根 (seizou.core.urls) が connect/ の下に含める。

注文の受け口は /connect/receive/order/ になる。
"""

from django.urls import path

from . import views

app_name = "connect"

urlpatterns = [
    path("receive/order/", views.receive_order, name="receive"),
]
