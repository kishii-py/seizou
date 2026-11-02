"""R15 の 2 文目の違反しない見本 (11 の属性だけを上書きする)。検査が読むだけで、動かさない。

型の注釈つきの代入、staticmethod で包んだ service、get_queryset の定義、class の docstring を含む。
"""

from seizou.core.views import BaseDetailView, BaseFormView, BaseListView

from . import services
from .forms import OrderForm
from .models import Order


class OrderListView(BaseListView):
    """受注の一覧。取り消した受注は出さない。"""

    model = Order
    ordering = "-ordered_on"
    columns: tuple = ("order_no", "customer", "ordered_on")
    search_fields = ("order_no",)
    screen_name = "受注の一覧"
    screen_help = "受けた注文の一覧。行を押すと詳細を開きます。"

    def get_queryset(self):
        return super().get_queryset().filter(cancelled=False)


class OrderDetailView(BaseDetailView):
    model = Order
    tabs = (("明細", "lines"),)


class OrderCreateView(BaseFormView):
    model = Order
    form_class = OrderForm
    service = staticmethod(services.create_order)
    template_name = "orders/order_form.html"
    screen_name = "受注を入れる"


class OrderCopyView(OrderCreateView):
    screen_name = "受注を写す"
