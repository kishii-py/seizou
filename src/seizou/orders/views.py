"""orders の画面 (02 の受注)。全部 core の土台を継承する (R15)。

受注の明細の候補の口 (orderline_select) は、出荷の入力が受注の明細を選ぶ欄に使う。
"""

from seizou.core.views import BaseSelectView

from .models import Order, OrderLine
from .services import annotate_remaining


class OrderLineSelectView(BaseSelectView):
    model = OrderLine
    search_fields = ("order__order_no", "item__part_no", "item__name")

    def get_queryset(self):
        # 残が 0 より大きく、受注が取消でない明細だけ。表示の字は OrderLine.__str__ (受注番号-行番号 品番 品名)。
        lines = OrderLine.objects.select_related("order", "item")
        return (
            annotate_remaining(lines)
            .filter(remaining_total__gt=0)
            .exclude(order__status=Order.STATUS_CANCELLED)
        )
