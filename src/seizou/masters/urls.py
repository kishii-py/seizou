"""masters の URL。名前の約束は <model>_list / _detail / _create / _update / _modal / _select (core の土台が使う)。"""

from django.urls import path

from . import views

app_name = "masters"

#: 左のメニューに並べる入口 (core の context processor が読む)
MENU = [
    ("部品マスター", "masters:item_list"),
    ("取引先", "masters:partner_list"),
    ("工程", "masters:process_list"),
    ("休日", "masters:holiday_list"),
    ("社員", "masters:staff_list"),
]

urlpatterns = [
    path("items/", views.ItemListView.as_view(), name="item_list"),
    path("items/new/", views.ItemCreateView.as_view(), name="item_create"),
    path("items/<int:pk>/", views.ItemDetailView.as_view(), name="item_detail"),
    path("items/<int:pk>/edit/", views.ItemUpdateView.as_view(), name="item_update"),
    path("items/<int:pk>/modal/", views.ItemModalView.as_view(), name="item_modal"),
    path("items/select/", views.ItemSelectView.as_view(), name="item_select"),
    path("units/select/", views.QuantityUnitSelectView.as_view(), name="quantityunit_select"),
    path("items/<int:pk>/prices/pick/", views.ItemPricePickView.as_view(), name="item_price_pick"),
    path("partners/", views.PartnerListView.as_view(), name="partner_list"),
    path("partners/new/", views.PartnerCreateView.as_view(), name="partner_create"),
    path("partners/<int:pk>/", views.PartnerDetailView.as_view(), name="partner_detail"),
    path("partners/<int:pk>/edit/", views.PartnerUpdateView.as_view(), name="partner_update"),
    path("partners/<int:pk>/modal/", views.PartnerModalView.as_view(), name="partner_modal"),
    path("partners/select/", views.PartnerSelectView.as_view(), name="partner_select"),
    path("partneritems/add/", views.PartnerItemAddView.as_view(), name="partneritem_add"),
    path("partneritems/<int:pk>/", views.partneritem_detail, name="partneritem_detail"),
    path("partneritems/<int:pk>/edit/", views.PartnerItemUpdateView.as_view(), name="partneritem_update"),
    path("partneritems/<int:pk>/delete/", views.PartnerItemDeleteView.as_view(), name="partneritem_delete"),
    path("itemprices/add/", views.ItemPriceAddView.as_view(), name="itemprice_add"),
    path("itemprices/<int:pk>/edit/", views.ItemPriceUpdateView.as_view(), name="itemprice_update"),
    path("itemprices/<int:pk>/delete/", views.ItemPriceDeleteView.as_view(), name="itemprice_delete"),
    path("processes/", views.ProcessListView.as_view(), name="process_list"),
    path("processes/new/", views.ProcessCreateView.as_view(), name="process_create"),
    path("processes/<int:pk>/", views.ProcessDetailView.as_view(), name="process_detail"),
    path("processes/<int:pk>/edit/", views.ProcessUpdateView.as_view(), name="process_update"),
    path("items/import/", views.ItemImportView.as_view(), name="item_import"),
    path("partners/import/", views.PartnerImportView.as_view(), name="partner_import"),
    path("items/import/sample/", views.item_import_sample, name="item_import_sample"),
    path("partners/import/sample/", views.partner_import_sample, name="partner_import_sample"),
    path("items/<int:pk>/drawing/view/", views.ItemDrawingViewerView.as_view(), name="item_drawing_viewer"),
    path("items/<int:pk>/drawing/restore/", views.ItemDrawingRestoreView.as_view(), name="item_drawing_restore"),
    path("items/<int:pk>/drawing/<str:filename>", views.item_drawing, name="item_drawing"),
    path("holidays/", views.HolidayListView.as_view(), name="holiday_list"),
    path("holidays/new/", views.HolidayCreateView.as_view(), name="holiday_create"),
    path("holidays/<int:pk>/", views.HolidayDetailView.as_view(), name="holiday_detail"),
    path("holidays/<int:pk>/delete/", views.HolidayDeleteView.as_view(), name="holiday_delete"),
    path("staff/", views.StaffListView.as_view(), name="staff_list"),
    path("staff/new/", views.StaffCreateView.as_view(), name="staff_create"),
    path("staff/<int:pk>/", views.StaffDetailView.as_view(), name="staff_detail"),
    path("staff/<int:pk>/edit/", views.StaffUpdateView.as_view(), name="staff_update"),
    path("staff/<int:pk>/delete/", views.StaffDeleteView.as_view(), name="staff_delete"),
    # 部品の標準の工程の並び (作者の決め)。追加の道に部品の pk を入れない (権限の tag can_write が引数無しで引くため。部品は ?item=<pk>)
    path("itemprocesses/add/", views.ItemProcessAddView.as_view(), name="itemprocess_add"),
    path("itemprocesses/<int:pk>/edit/", views.ItemProcessUpdateView.as_view(), name="itemprocess_update"),
    path("itemprocesses/<int:pk>/remove/", views.ItemProcessRemoveView.as_view(), name="itemprocess_remove"),
]
