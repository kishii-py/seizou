"""AI チャットの口の URL (/assistant/ の下)。入口は画面の右下の丸いボタン「AI に質問」で、押すと右下に窓が開く (左のメニューの入口は本人の決め 11-07 16:5x で外した)。"""

from django.urls import path

from . import views

app_name = "assistant"

urlpatterns = [
    path("ask/", views.ask, name="ask"),
    path("clear/", views.clear, name="clear"),
    path("approve/", views.approve, name="approve"),
]
