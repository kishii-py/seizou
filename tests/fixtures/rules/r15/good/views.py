"""R15 の違反しない見本。検査が読むだけで、動かさない。

土台を継承する画面、それを同じファイルの中で継承する画面、Mixin を持つ画面、画面でない class。
"""

from django import forms

from seizou.core import views as core_views
from seizou.core.views import BaseDetailView, BaseListView

from .models import Item


class PrintButtonMixin:
    print_label = "印刷"


class ItemListView(BaseListView):
    model = Item
    screen_name = "品目の一覧"
    columns = ("part_no", "name")


class ItemPrintListView(ItemListView):
    template_name = "masters/item_print.html"


class ItemDetailView(PrintButtonMixin, BaseDetailView):
    model = Item
    tabs = ("drawings",)


class ItemSelectView(core_views.BaseSelectView):
    screen_name = "品目の候補"


class ItemSearchForm(forms.Form):
    query = forms.CharField(label="品番か品名", required=False)
