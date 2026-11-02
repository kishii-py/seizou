"""bookmarks の URL (core の urls.py が /bookmarks/ に入れる)。

登録と解除の口は toggle (model の名前の約束 <model>_list などに当たらない口なので、別の名前にする)。
"""

from django.urls import path

from . import views

app_name = "bookmarks"

#: 左のメニューに入口を出さない (ブックマークは上の帯の ☆ と、最初の画面の「よく使う画面」から使う)。
MENU = []

urlpatterns = [
    path("toggle/", views.toggle, name="toggle"),
]
