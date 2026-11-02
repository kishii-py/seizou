"""intake の URL。URL の根 (seizou.core.urls) が intake/ の下に含める。

画面の URL の名前は <model>_list / _create / _detail の約束 (core の土台が使う)。受注にするは読み取りの行ごと。
"""

from django.urls import path

from . import views

app_name = "intake"

#: 左のメニューに並べる入口 (core の context processor が読む)
MENU = [("注文書の読み取り", "intake:extraction_list")]

urlpatterns = [
    path("", views.ExtractionListView.as_view(), name="extraction_list"),
    path("new/", views.ExtractionCreateView.as_view(), name="extraction_create"),
    path("<int:pk>/", views.ExtractionDetailView.as_view(), name="extraction_detail"),
    path("<int:pk>/accept/", views.ExtractionAcceptView.as_view(), name="extraction_accept"),
]
