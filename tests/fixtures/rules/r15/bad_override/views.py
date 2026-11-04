"""R15 の 2 文目の違反の見本 (土台を継承した画面が、12 の属性の外を上書きする)。検査が読むだけで、動かさない。"""

from seizou.core.views import BaseFormView, BaseListView

from . import services
from .forms import OrderForm
from .models import Order


class OrderListView(BaseListView):
    model = Order
    screen_name = "受注の一覧"
    paginate_by = 20

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs)


class OrderCreateView(BaseFormView):
    model = Order
    form_class = OrderForm
    service = staticmethod(services.create_order)


class OrderCopyView(OrderCreateView):
    screen_name = "受注を写す"
    success_url = "/orders/"
