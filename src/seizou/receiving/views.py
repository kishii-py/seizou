"""receiving の画面 (入荷の一覧・詳細・新規登録と、受注の明細の候補の口)。全部 core の土台を継承する (R15)。

class の中に書くのは、土台が上書きを許す属性だけ。保存は services の関数だけが行う (R13)。
- 新規登録は土台の小窓の形でも開ける (htmx の見出しで小窓の中身を返すのは土台)。
  受注の明細から ?order_line=<pk> を付けて開くと、form がその明細の部品・得意先・残りの数・預かりを初期値にする。
- 受注の明細の候補の口は、取消でない受注の、引当が受注の数に届いていない明細だけを返す (画面の目録には出さない)。
"""

from django.db.models import F

from seizou.core.views import BaseDetailView, BaseFormView, BaseListView, BaseSelectView

from . import services
from .forms import OrderLineModel, ReceivingForm
from .models import SOURCE_CHOICES, Receiving

#: 受注の表 (受注の明細の外部キーの先。取消の状態の字を読む)
OrderModel = OrderLineModel._meta.get_field("order").related_model


class ReceivingListView(BaseListView):
    """入荷の一覧。

    使う人: 生産管理・製造 / いつ: 得意先からワークや支給材が届いたときと、届いた物を確かめるとき / 何のために: 受け入れた現物 (無償支給・有償支給・預かり) の記録を見る / 次にすること: 「新規登録」で入荷を記録する。行から詳細で引当を見る
    """

    model = Receiving
    screen_name = "入荷"
    screen_help = (
        "得意先から受け入れた現物 (無償支給・有償支給・預かり) の記録を新しい順に並べます。"
        "行を押すと詳細で、どの受注の明細に引き当てたかが見られます。"
    )
    columns = ("received_on", "item", "partner", "source", "qty", "lot")
    search_fields = ("item__part_no", "item__name", "partner__name")
    filters = ({"name": "source", "label": "出所", "kind": "choice", "field": "source", "choices": SOURCE_CHOICES},)
    ordering = ("-received_on", "-pk")

    def get_queryset(self):
        return super().get_queryset().select_related("item", "partner")


class ReceivingDetailView(BaseDetailView):
    """入荷の詳細。

    使う人: 生産管理・製造 / いつ: 1 件の入荷を確かめるとき / 何のために: 部品・支給元・出所・数量・ロットと、引き当てた受注の明細を見る / 次にすること: 引当のタブの明細で指示書の発行と出荷へ
    """

    model = Receiving
    screen_name = "入荷の詳細"
    screen_help = "入荷 1 件の部品・支給元・出所・数量・入荷日・ロット・備考と入れた人。「引当」のタブに、引き当てた受注の明細と数を並べます。"
    tabs = (("引当", "allocations"),)


class ReceivingCreateView(BaseFormView):
    """入荷の新規登録の入力。

    使う人: 生産管理・製造 / いつ: 得意先からワークや支給材が届いたとき / 何のために: 受け入れた現物を記録し、受注の明細に引き当てる / 次にすること: 指示書を発行する。出荷する
    """

    model = Receiving
    form_class = ReceivingForm
    service = staticmethod(services.create_receiving)
    screen_name = "入荷の新規登録"
    screen_help = (
        "受注の明細を選ぶと、部品・支給元・残りの数が入り、保存すると入荷をその明細に引き当てます。"
        "受注の明細が無い入荷は、部品と出所を選んで保存します。"
    )


class OrderLineSelectView(BaseSelectView):
    model = OrderLineModel
    search_fields = ("order__order_no", "order__customer__name", "item__part_no", "item__name")

    def get_queryset(self):
        # 取消でない受注の、引当の合計が受注の数より少ない明細だけ。表示の字は OrderLine.__str__ (受注番号-行番号 品番 品名)。
        lines = OrderLineModel._default_manager.select_related("order__customer", "item")
        return (
            services.annotate_allocated(lines)
            .filter(allocated_total__lt=F("qty"))
            .exclude(order__status=OrderModel.STATUS_CANCELLED)
            # 集計を含む queryset の並びをここで決める (ページの境目を DB に任せない)。
            .order_by("order__order_no", "line_no", "pk")
        )
