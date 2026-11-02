"""shipping の画面 (02 の出荷)。画面は全部 core の土台を継承する (R15)。

出荷の入力は、土台が検査の層 (受注より多い出荷のポカよけ) を通してから services.ship を呼ぶ。
止めるか、知らせて「このまま保存」か、通すかは会社の設定に従う (画面では数を比べない)。
出荷を直す画面は作らない (出荷を直す services の関数が無い)。
"""

from seizou.core.views import BaseDetailView, BaseFormView, BaseListView

from . import services
from .forms import ShipmentForm
from .models import Shipment


class ShipmentListView(BaseListView):
    model = Shipment
    screen_name = "出荷"
    screen_help = "出荷の一覧。出荷日の新しい順に並びます。得意先と品目の名前を押すと小窓が開きます。"
    columns = (
        "shipment_no",
        ("order_line__order__order_no", "受注番号"),
        ("order_line__order__customer", "得意先"),
        ("order_line__item", "品目"),
        "qty",
        "shipped_at",
        ("delivery_note__note_no", "納品書番号"),
    )
    search_fields = (
        "shipment_no",
        "order_line__order__order_no",
        "order_line__item__part_no",
        "order_line__order__customer__name",
    )
    ordering = ("-shipped_at", "-id")

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("order_line__order__customer", "order_line__item", "delivery_note")
        )


class ShipmentDetailView(BaseDetailView):
    model = Shipment
    screen_name = "出荷の詳細"
    screen_help = "出荷の項目。出荷を直す画面はありません。"


class ShipmentCreateView(BaseFormView):
    model = Shipment
    form_class = ShipmentForm
    service = staticmethod(services.ship)
    screen_name = "出荷を入れる"
    screen_help = (
        "受注の明細と数量と出荷日を入れて、出荷を保存します。"
        "受注の残を超える数は、会社の設定に従って止めるか知らせます。"
    )
