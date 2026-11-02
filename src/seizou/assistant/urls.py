"""AI チャットの口の URL (/assistant/ の下)。入口は全部の画面の右下の窓なので、左のメニューの入口は置かない。"""

from django.urls import path

from . import views

app_name = "assistant"

urlpatterns = [
    path("ask/", views.ask, name="ask"),
    path("clear/", views.clear, name="clear"),
]
