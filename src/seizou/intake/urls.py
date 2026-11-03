"""intake の URL。URL の根 (seizou.core.urls) が intake/ の下に含める。

画面の URL の名前は <model>_list / _create / _detail の約束 (core の土台が使う)。受注にするは読み取りの行ごと。
"""

from django.urls import path

from . import views

app_name = "intake"

#: 左のメニューに並べる入口 (core の context processor が読む)
#: 左のメニューの入口は無し。受注の画面 (orders:order_list) の「読み取る (OCR)」から intake:extraction_create へ入る (02 の 8 節の決め 6)。
#: URL と画面は残す (画面の目録・使い方・ブックマークからは開ける)。
MENU = []

urlpatterns = [
    path("", views.ExtractionListView.as_view(), name="extraction_list"),
    path("new/", views.ExtractionCreateView.as_view(), name="extraction_create"),
    path("<int:pk>/", views.ExtractionDetailView.as_view(), name="extraction_detail"),
    path("<int:pk>/accept/", views.ExtractionAcceptView.as_view(), name="extraction_accept"),
]
