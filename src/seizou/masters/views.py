"""masters の画面 (02 の部品マスターと取引先)。全部 core の土台を継承する (R15)。

一覧は行を押すと詳細、部品の名前の字を押すと小窓 (core の master_link)。会社ごとの項目の列は土台が自動で足す。
部品の小窓は、取引先ごとの品番 (tabs) と会社ごとの項目も出す (masters/_modal.html)。
"""

from seizou.core.views import BaseDetailView, BaseFormView, BaseListView, BaseModalView, BaseSelectView

from . import services
from .forms import ItemForm, PartnerForm
from .models import Item, Partner


class ItemListView(BaseListView):
    model = Item
    screen_name = "部品マスター"
    screen_help = "部品 (品番・品名・図面) を 1 か所に持つ表。品名を押すと、画面を移らずに中身を見られます。"
    columns = ("part_no", "name", "drawing_no", "unit", "standard_price")
    search_fields = ("part_no", "name", "drawing_no")
    ordering = "part_no"


class ItemDetailView(BaseDetailView):
    model = Item
    screen_name = "部品の詳細"
    screen_help = "部品の項目と、取引先ごとの品番、標準の工程の並び。"
    tabs = (("取引先ごとの品番", "partner_items"), ("標準の工程", "item_processes"))


class ItemCreateView(BaseFormView):
    model = Item
    form_class = ItemForm
    service = staticmethod(services.create_item)
    screen_name = "部品を入れる"
    screen_help = "新しい部品を部品マスターに足します。品番は会社の中で 1 つだけです。"


class ItemUpdateView(BaseFormView):
    model = Item
    form_class = ItemForm
    service = staticmethod(services.update_item)
    screen_name = "部品を直す"
    screen_help = "部品の項目を直します。"


class ItemModalView(BaseModalView):
    model = Item
    screen_name = "部品の小窓"
    screen_help = "部品の要点と取引先ごとの品番を見るだけの小窓。詳細へ移れます。"
    tabs = (("取引先ごとの品番", "partner_items"),)


class ItemSelectView(BaseSelectView):
    model = Item
    search_fields = ("part_no", "name")


class PartnerListView(BaseListView):
    model = Partner
    screen_name = "取引先"
    screen_help = "得意先と外注先。締日・支払日と端数の扱いを取引先ごとに持ちます。"
    columns = ("code", "name", "short_name", "role", "closing_day", "payment_day")
    search_fields = ("code", "name", "short_name")
    ordering = "code"


class PartnerDetailView(BaseDetailView):
    model = Partner
    screen_name = "取引先の詳細"
    screen_help = "取引先の項目と、取引先ごとの品番。"
    tabs = (("取引先ごとの品番", "partner_items"),)


class PartnerCreateView(BaseFormView):
    model = Partner
    form_class = PartnerForm
    service = staticmethod(services.create_partner)
    screen_name = "取引先を入れる"
    screen_help = "新しい取引先を足します。取引先コードは会社の中で 1 つだけです。"


class PartnerUpdateView(BaseFormView):
    model = Partner
    form_class = PartnerForm
    service = staticmethod(services.update_partner)
    screen_name = "取引先を直す"
    screen_help = "取引先の項目を直します。"


class PartnerModalView(BaseModalView):
    model = Partner
    screen_name = "取引先の小窓"
    screen_help = "取引先の要点を見るだけの小窓。詳細へ移れます。"


class PartnerSelectView(BaseSelectView):
    model = Partner
    search_fields = ("code", "name", "short_name")
