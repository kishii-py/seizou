"""manuals の URL (/manuals/ の下)。名前の約束は <model>_list / _detail / _create / _update (core の土台が使う)。

model の約束に当たらない口 (プレビュー・画像の追加と削除と口) は別の名前にする。
フォルダの _detail と _list は、一覧の ?folder=<pk> とルートへ移るだけの口 (土台の保存の後の移り先とパンくずが使う)。
"""

from django.urls import path

from . import views

app_name = "manuals"

#: 左のメニューの入口 (core の context processor が読む。区分は「その他」)
MENU = [("マニュアル", "manuals:manual_list")]

urlpatterns = [
    path("", views.ManualListView.as_view(), name="manual_list"),
    path("new/", views.ManualCreateView.as_view(), name="manual_create"),
    path("<int:pk>/", views.ManualDetailView.as_view(), name="manual_detail"),
    path("<int:pk>/edit/", views.ManualUpdateView.as_view(), name="manual_update"),
    path("<int:pk>/preview/", views.manual_preview, name="manual_preview"),
    path("<int:pk>/images/new/", views.image_upload, name="manualimage_upload"),
    path("<int:pk>/versions/new/", views.ManualVersionCreateView.as_view(), name="manual_version_create"),
    path("<int:pk>/versions/", views.ManualVersionListView.as_view(), name="manualversion_list"),
    path("<int:pk>/move/", views.ManualMoveView.as_view(), name="manual_move"),
    path("<int:pk>/protect/", views.ManualProtectView.as_view(), name="manual_protect"),
    path("<int:pk>/delete/", views.ManualDeleteView.as_view(), name="manual_delete"),
    path("<int:pk>/pdf/", views.manual_pdf, name="manual_pdf"),
    path("versions/<int:pk>/", views.ManualVersionDetailView.as_view(), name="manualversion_detail"),
    path("versions/<int:pk>/restore/", views.ManualVersionRestoreView.as_view(), name="manualversion_restore"),
    path("folders/", views.folder_root, name="manualfolder_list"),
    path("folders/new/", views.FolderCreateView.as_view(), name="manualfolder_create"),
    path("folders/<int:pk>/", views.folder_open, name="manualfolder_detail"),
    path("folders/<int:pk>/edit/", views.FolderUpdateView.as_view(), name="manualfolder_update"),
    path("folders/<int:pk>/move/", views.FolderMoveView.as_view(), name="manualfolder_move"),
    path("folders/<int:pk>/protect/", views.FolderProtectView.as_view(), name="manualfolder_protect"),
    path("folders/<int:pk>/delete/", views.FolderDeleteView.as_view(), name="manualfolder_delete"),
    path("images/<int:pk>/", views.image_download, name="manualimage_download"),
    path("images/<int:pk>/delete/", views.image_delete, name="manualimage_delete"),
]
