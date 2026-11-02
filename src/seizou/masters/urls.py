"""masters の URL。名前の約束は <model>_list / _detail / _create / _update / _modal / _select (core の土台が使う)。"""

from django.urls import path

from . import views

app_name = "masters"

#: 左のメニューに並べる入口 (core の context processor が読む)
MENU = [
    ("部品マスター", "masters:item_list"),
    ("取引先", "masters:partner_list"),
    ("工程", "masters:process_list"),
]

urlpatterns = [
    path("items/", views.ItemListView.as_view(), name="item_list"),
    path("items/new/", views.ItemCreateView.as_view(), name="item_create"),
    path("items/<int:pk>/", views.ItemDetailView.as_view(), name="item_detail"),
    path("items/<int:pk>/edit/", views.ItemUpdateView.as_view(), name="item_update"),
    path("items/<int:pk>/modal/", views.ItemModalView.as_view(), name="item_modal"),
    path("items/select/", views.ItemSelectView.as_view(), name="item_select"),
    path("partners/", views.PartnerListView.as_view(), name="partner_list"),
    path("partners/new/", views.PartnerCreateView.as_view(), name="partner_create"),
    path("partners/<int:pk>/", views.PartnerDetailView.as_view(), name="partner_detail"),
    path("partners/<int:pk>/edit/", views.PartnerUpdateView.as_view(), name="partner_update"),
    path("partners/<int:pk>/modal/", views.PartnerModalView.as_view(), name="partner_modal"),
    path("partners/select/", views.PartnerSelectView.as_view(), name="partner_select"),
    path("processes/", views.ProcessListView.as_view(), name="process_list"),
    path("processes/new/", views.ProcessCreateView.as_view(), name="process_create"),
    path("processes/<int:pk>/", views.ProcessDetailView.as_view(), name="process_detail"),
    path("processes/<int:pk>/edit/", views.ProcessUpdateView.as_view(), name="process_update"),
]
