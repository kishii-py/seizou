"""画面の土台 (01 の 2 節と 10.3)。

部品の画面はここの Base*View を継承する (R15)。継承した画面が上書きしてよいのは
columns・search_fields・tabs・form_class・service・template_name・get_queryset・
screen_name・screen_help の 9 つだけ。

この版は名前と約束を置く最初の commit。中身 (検索・並び替え・ページ送り・会社ごとの項目・
検査の層の呼び出し・知らせ) は後の commit で足す。
"""

from __future__ import annotations

from django.views.generic import DetailView, FormView, ListView, View

#: 継承した画面が上書きしてよい属性 (R15 の 2 文目。決まりの検査が見る)
OVERRIDABLE = (
    "columns",
    "search_fields",
    "tabs",
    "form_class",
    "service",
    "template_name",
    "get_queryset",
    "screen_name",
    "screen_help",
)


class ScreenMixin:
    """画面の名前と 1 文。画面の目録 (manage.py seizou_screens)・説明書・左のメニューが読む。"""

    screen_name: str = ""
    screen_help: str = ""


class BaseListView(ScreenMixin, ListView):
    """一覧の土台。サーバーが行を描く。既定は 1 ページ 50 件 (20 / 50 / 100)。"""

    columns: tuple = ()
    search_fields: tuple = ()
    ordering = None
    paginate_by = 50
    page_sizes = (20, 50, 100)


class BaseDetailView(ScreenMixin, DetailView):
    """詳細の土台。1 件と、関係する物の一覧 (tabs)。"""

    tabs: tuple = ()


class BaseFormView(ScreenMixin, FormView):
    """入力の土台。form の値が通ったら検査の層を通し、service を呼ぶ。土台が ModelForm の save を呼ぶことは無い。"""

    form_class = None
    service = None
    action: str = ""  # 検査の層の action (例 "shipping.ship")


class BaseModalView(ScreenMixin, DetailView):
    """小窓の土台。見るだけと、詳細への移動のボタン。template は <app>/_modal.html。"""

    template_name_suffix = "/_modal"


class BaseSelectView(ScreenMixin, View):
    """候補の口。Tom Select の候補を 1 ページ返す (05 の 2 節)。"""

    per_page = 30
