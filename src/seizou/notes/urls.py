"""notes の URL (core の urls.py が /notes/ に入れる)。

書く画面は note_edit (note_create・note_update にすると、土台の一覧と詳細が「新規登録」「編集」の入口を探すため)。
保存の後は note_detail (付けた先の記録の詳細へ移るだけの口) へ移る。
"""

from django.urls import path

from . import views

app_name = "notes"

#: 左のメニューに並べる入口 (core の context processor が読む)。メモは各詳細の枠から書くので出さない
MENU = []

urlpatterns = [
    path("edit/", views.NoteEditView.as_view(), name="note_edit"),
    path("<int:pk>/", views.note_detail, name="note_detail"),
    path("images/new/", views.image_upload, name="noteimage_upload"),
]
