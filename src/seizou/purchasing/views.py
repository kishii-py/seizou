"""purchasing の画面 (02 の発注と注文を送る)。全部 core の土台を継承する (R15)。

class の中に書くのは、土台が上書きを許す属性だけ。保存は services の関数だけが行い (R13)、
送るのは connect.services.send_order だけ (R17。HTTP は atomic の外)。
"""

from seizou.core.views import BaseDetailView, BaseFormView, BaseListView

from seizou.core.views import BaseSelectView
from seizou.masters.services import active_partners

from . import services
from .forms import CancelForm, PurchaseOrderForm, SendForm
from .forms import ReceiptForm
from .models import PurchaseOrder
from .models import PurchaseOrderLine, Receipt


def _subcontractor_choices():
    """発注の一覧の取引先の絞りの候補 (取引先の種類が協力企業か両方で、使用中の物。発注を入れる form の候補と同じ = N135。名前の順)。"""
    partner_model = PurchaseOrder._meta.get_field("partner").related_model
    rows = active_partners().filter(role__in=(partner_model.ROLE_SUBCONTRACTOR, partner_model.ROLE_BOTH))
    return [(row.pk, str(row)) for row in rows.order_by("name", "pk")]


class PurchaseOrderListView(BaseListView):
    """発注の一覧。

    使う人: 生産管理 / いつ: 協力企業に出すと決めたときと、発注の状態を見るとき / 何のために: 協力企業への発注と送った記録を持つ / 次にすること: 「新規登録」か行から詳細へ。受入は受入の画面
    """

    model = PurchaseOrder
    screen_name = "発注"
    screen_help = "協力企業への発注。行を押すと明細と、相手へ送った記録が見られます。"
    # received_total・receive_remaining は annotate の列 (purchasing.services.annotate_order_received の Subquery)。
    # columns の印 (kind・sortable・total) で土台が右寄せ・赤・並び替え・合計の行を出す。
    columns = (
        "po_no",
        "partner",
        "order_date",
        "status",
        "replied_at",
        {"name": "received_total", "label": "受入の数", "kind": "number", "sortable": True, "total": True},
        {"name": "receive_remaining", "label": "受入の残", "kind": "number", "sortable": True, "total": True},
        "note",
    )
    search_fields = ("po_no", "partner__name", "partner__code")
    # 取引先は choice に候補の関数を渡す (lookup は FK の先の全部を候補にし、choices を見ないため)。
    # 納期は明細の項目 (lines__due_date)。明細が 2 行合うと同じ発注が 2 回出るので、get_queryset が distinct() で 1 回にする。
    filters = (
        {"name": "status", "label": "状態", "kind": "choice", "field": "status"},
        {"name": "partner", "label": "取引先", "kind": "choice", "field": "partner", "choices": _subcontractor_choices},
        {"name": "due", "label": "納期", "kind": "date_range", "field": "lines__due_date"},
    )
    ordering = ("-order_date", "-id")
    template_name = "purchasing/purchaseorder_list.html"

    def get_queryset(self):
        return services.annotate_order_notices(services.annotate_order_received(super().get_queryset().select_related("partner").distinct()))


class PurchaseOrderDetailView(BaseDetailView):
    """発注の詳細。

    使う人: 生産管理 / いつ: 発注の中身と相手の回答を確かめるとき / 何のために: 明細・送った記録・受入を見る / 次にすること: 「受入を登録」へ。「作成」のままなら送り直しか取消
    """

    model = PurchaseOrder
    screen_name = "発注の詳細"
    screen_help = "発注の項目と明細、相手へ送った記録。「作成」のときだけ送り直しと取消ができます。"
    tabs = (("送った記録", "outbox_messages"), ("受入", "receipts"))
    template_name = "purchasing/purchaseorder_detail.html"

    def get_queryset(self):
        return services.annotate_order_notices(services.annotate_order_received(super().get_queryset()))


class PurchaseOrderCreateView(BaseFormView):
    """発注の新規登録の入力。

    使う人: 生産管理 / いつ: 協力企業に出すと決めたとき / 何のために: 協力企業に注文を送る / 次にすること: 相手の受信した注文に届く。物が届いたら受入
    """

    model = PurchaseOrder
    form_class = PurchaseOrderForm
    service = staticmethod(services.create_purchase_order)
    template_name = "purchasing/purchaseorder_form.html"
    screen_name = "発注の新規登録"
    screen_help = "協力企業と明細を入れて保存すると発注の記録ができ、つながりのある相手へ同時に注文を送ります。"


class PurchaseOrderSendView(BaseFormView):
    """注文を送り直す確かめの入力。

    使う人: 生産管理 / いつ: 相手に届いていないとき / 何のために: 「作成」の発注をもう一度送る / 次にすること: 発注の詳細へ戻る (届けば緑、届かなければ黄の知らせ)
    """

    model = PurchaseOrder
    form_class = SendForm
    service = staticmethod(services.resend_purchase_order)
    screen_name = "注文を送り直す"
    screen_help = "「作成」のままの発注を、つながりの相手へもう一度送ります。"


class PurchaseOrderCancelView(BaseFormView):
    """発注を取り消す確かめの入力。

    使う人: 生産管理 / いつ: 発注が要らなくなったとき / 何のために: 「作成」の発注を取り消す / 次にすること: 発注の一覧へ戻る
    """

    model = PurchaseOrder
    form_class = CancelForm
    service = staticmethod(services.cancel_purchase_order)
    screen_name = "発注を取り消す"
    screen_help = "「作成」の発注を取り消します。理由は備考の末尾に残ります。"


# --- 受入 (協力企業管理の 2 つ目の画面) ---


class ReceiptListView(BaseListView):
    """受入の一覧。

    使う人: 生産管理・経理 / いつ: 協力企業から物が届いたときと、月の支払の見込みを見るとき / 何のために: 受け入れた数と支払の見込みを見る / 次にすること: 「新規登録」で受入を記録
    """

    model = Receipt
    screen_name = "受入"
    screen_help = (
        "協力企業から受け入れた物の記録。受け入れた数 × 発注の単価が支払の金額になります。"
        "上の表は取引先ごとの先月と今月の支払の見込みです。"
    )
    columns = (
        ("received_on", "受入日"),
        ("line__purchase_order__po_no", "発注番号"),
        ("line__purchase_order__partner", "取引先"),
        ("line__item", "部品"),
        "qty",
        "amount",
        "note",
    )
    search_fields = (
        "line__purchase_order__po_no",
        "line__purchase_order__partner__name",
        "line__item__part_no",
        "line__item__name",
    )
    ordering = ("-received_on", "-pk")
    template_name = "purchasing/receipt_list.html"

    def get_queryset(self):
        return super().get_queryset().select_related("line__purchase_order__partner", "line__item")


class ReceiptDetailView(BaseDetailView):
    """受入の詳細。

    使う人: 生産管理・経理 / いつ: 受入 1 件を確かめるとき / 何のために: 発注の明細・受入日・数量・金額を見る / 次にすること: 発注の詳細へ
    """

    model = Receipt
    screen_name = "受入の詳細"
    screen_help = "受入 1 件の発注の明細・受入日・数量・金額 (受け入れた数 × 発注の単価) と、入れた人。"


class ReceiptCreateView(BaseFormView):
    """受入の新規登録の入力。

    使う人: 生産管理 / いつ: 協力企業から物が届いたとき / 何のために: 発注の明細ごとに受け入れた日と数を記録する (在庫の入庫になる) / 次にすること: 受入の一覧で支払の見込みを見る
    """

    model = Receipt
    form_class = ReceiptForm
    service = staticmethod(services.receive)
    screen_name = "受入の新規登録"
    screen_help = (
        "発注の明細を字で探して選び、受入日と数量を入れて保存します。"
        "発注の詳細の「受入を登録」から開くと、明細と残の数が入っています。"
    )


class PurchaseOrderLineSelectView(BaseSelectView):
    model = PurchaseOrderLine
    search_fields = ("purchase_order__po_no", "purchase_order__partner__name", "item__part_no", "item__name")

    def get_queryset(self):
        # 受入の残が 0 より大きく、発注が取消でない明細だけ。表示の字は PurchaseOrderLine.__str__ (発注番号-行番号 品番 品名)。
        lines = PurchaseOrderLine.objects.select_related("purchase_order", "item")
        return (
            services.annotate_received(lines)
            .filter(remaining_total__gt=0)
            .exclude(purchase_order__status=PurchaseOrder.STATUS_CANCELLED)
            # 集計 (Sum) を含む queryset では model の Meta.ordering が効かないので、並びをここで決める。
            .order_by("purchase_order__po_no", "line_no", "pk")
        )
