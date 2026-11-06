"""AI チャットの口の URL (/assistant/ の下)。入口は左のメニューの下の「AI に質問」で、押すと画面の右下に窓が開く (決め C37)。"""

from django.urls import path

from . import views

app_name = "assistant"

urlpatterns = [
    path("ask/", views.ask, name="ask"),
    path("clear/", views.clear, name="clear"),
    path("approve/", views.approve, name="approve"),
]
