"""stock の URL。一覧と詳細は名前の約束 (<model>_list / _detail) に当てる (core の土台が使う)。

在庫の一覧 (stock_list) は部品ごとの在庫数。入力の 3 つ (製造の完了を入庫にする・払い出し・棚卸しの調整) は
model の約束の外の名前にして、土台が「新規登録」のボタンを出さない形にする (入口は一覧のテンプレートが出す)。
"""

from django.urls import path

from . import views

app_name = "stock"

#: 左のメニューに並べる入口 (core の context processor が読む)
MENU = [("在庫", "stock:stock_list")]

urlpatterns = [
    path("", views.StockListView.as_view(), name="stock_list"),
    path("moves/", views.StockMoveListView.as_view(), name="stockmove_list"),
    path("moves/<int:pk>/", views.StockMoveDetailView.as_view(), name="stockmove_detail"),
    path("finished/new/", views.FinishedCreateView.as_view(), name="finished_create"),
    path("issue/new/", views.IssueCreateView.as_view(), name="issue_create"),
    path("adjust/new/", views.AdjustCreateView.as_view(), name="adjust_create"),
    path("items/<int:pk>/balance/", views.item_balance, name="item_balance"),
]
