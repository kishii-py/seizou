"""stock の画面 (在庫の一覧・動きの一覧と詳細・製造の完了を入庫にする・払い出し・棚卸しの調整)。全部 core の土台を継承する (R15)。

class の中に書くのは、土台が上書きを許す属性だけ。保存は services の関数だけが行う (R13)。
- 在庫の一覧は、部品 (masters の Item。外部キーの先として引き、masters を import しない) に在庫数を足した一覧。
  行を押すと部品の詳細へ移る (土台の行の link)。動きの無い部品は並ばない。
- 入力の画面の URL の名前は model の約束の外 (finished_create・issue_create・adjust_create) にして、
  土台が「新規登録」のボタンを勝手に出さない形にする。入口のボタンは一覧のテンプレートが出す。
- 動きの一覧は GET の item (部品の pk) で絞れる (読めない字は無視)。
"""

from seizou.core.views import BaseDetailView, BaseFormView, BaseListView

from . import services
from .forms import AdjustForm, FinishedForm, IssueForm
from .models import KIND_CHOICES, KIND_SHIPPED, StockMove

#: 部品の表 (masters の Item)。在庫の動きの外部キーの先として引く (読むだけ)
ItemModel = StockMove._meta.get_field("item").related_model
#: 動きの一覧の種類の絞りの候補。出荷は動きの表に書かない (出荷の表から集計で引く) ので除く
MOVE_KIND_CHOICES = tuple(choice for choice in KIND_CHOICES if choice[0] != KIND_SHIPPED)


def _pk_or_none(raw):
    """pk の字を数にする。読めない字と 1 未満は None。"""
    try:
        pk = int(str(raw))
    except (TypeError, ValueError):
        return None
    return pk if pk > 0 else None


class StockListView(BaseListView):
    """在庫の一覧。

    使う人: 生産管理 / いつ: 注文が入ったときと、在庫で払い出せるかを見るとき / 何のために: 完成品の在庫数を部品ごとに見る / 次にすること: 行から部品の詳細へ。数え始めは棚卸しの調整
    """

    model = ItemModel
    screen_name = "在庫"
    screen_help = (
        "完成品の在庫数を部品ごとに見ます。在庫数 = 入庫 (製造の完了・受入) の合計 − 出庫 (出荷・払い出し) の合計。"
        "動きの無い部品は並びません。棚卸しの調整で数え始めます。行を押すと部品の詳細へ移ります。"
    )
    columns = (
        "part_no",
        "name",
        "unit",
        {"name": "stock_balance", "label": "在庫数", "kind": "number", "sortable": True},
        {"name": "last_moved_on", "label": "最終の動きの日", "kind": "date", "sortable": True},
    )
    search_fields = ("part_no", "name")
    ordering = ("part_no",)
    template_name = "stock/stock_list.html"

    def get_queryset(self):
        # 在庫数は Subquery で足す (動きのある部品だけ)。並びは土台の ordering のまま。
        return services.annotate_balance(super().get_queryset(), "pk").filter(stock_balance__isnull=False)


class StockMoveListView(BaseListView):
    """在庫の動きの一覧。

    使う人: 生産管理 / いつ: 在庫数の根拠を確かめるとき / 何のために: 入庫・払い出し・棚卸しの調整の記録を見る / 次にすること: 行から動きの詳細へ
    """

    model = StockMove
    screen_name = "在庫の動き"
    screen_help = (
        "入庫 (製造の完了・受入)・払い出し・棚卸しの調整の記録を新しい順に並べます。"
        "出荷はここに並ばず、出荷の記録から集計で在庫数を引きます。元の記録は指示書か受入です。"
    )
    columns = ("moved_on", "item", "kind", "qty", ("source_label", "元の記録"), "note", "moved_by")
    search_fields = ("item__part_no", "item__name", "note")
    filters = (
        {"name": "kind", "label": "種類", "kind": "choice", "field": "kind", "choices": MOVE_KIND_CHOICES},
        {"name": "item", "label": "部品", "kind": "lookup", "field": "item"},
        {"name": "moved", "label": "期間", "kind": "date_range", "field": "moved_on"},
    )
    ordering = ("-moved_on", "-pk")
    template_name = "stock/stockmove_list.html"

    def get_queryset(self):
        moves = super().get_queryset().select_related("item", "moved_by", "content_type").prefetch_related("source")
        item = _pk_or_none(self.request.GET.get("item"))
        if item is not None:
            moves = moves.filter(item_id=item)
        return moves


class StockMoveDetailView(BaseDetailView):
    """在庫の動きの詳細。

    使う人: 生産管理 / いつ: 1 件の動きを確かめるとき / 何のために: 部品・種類・数量・日付と入れた人を見る / 次にすること: 在庫の動きへ戻る
    """

    model = StockMove
    screen_name = "在庫の動きの詳細"
    screen_help = "在庫の動き 1 件の部品・種類・数量・日付・備考と、入れた人と日時。入庫は正、出庫は負の数です。"


class FinishedCreateView(BaseFormView):
    """製造の完了を入庫にする入力。

    使う人: 製造・生産管理 / いつ: 最後の工程が終わったとき / 何のために: 完成した数を入庫にする / 次にすること: 在庫に出る。出荷へ
    """

    model = StockMove
    form_class = FinishedForm
    service = staticmethod(services.record_finished)
    screen_name = "製造の完了を入庫にする"
    screen_help = (
        "発行の指示書を選び、完成した数を入庫にします。"
        "完成の数は最後の工程の実績の合計で、指示書を選んで開くと残りの数が入ります。"
    )


class IssueCreateView(BaseFormView):
    """払い出しの入力。

    使う人: 生産管理 / いつ: 検査や社内の使用で在庫から出したとき / 何のために: 出した数を記録する / 次にすること: 在庫で確かめる
    """

    model = StockMove
    form_class = IssueForm
    service = staticmethod(services.record_issued)
    screen_name = "払い出し"
    screen_help = "検査や社内の使用などで在庫から出した数を記録します。在庫より多くても止めません (マイナスの在庫として出ます)。"


class AdjustCreateView(BaseFormView):
    """棚卸しの調整の入力。

    使う人: 生産管理 / いつ: 棚卸しのときと、在庫を数え始めるとき / 何のために: 実地の数との差を動きとして残す / 次にすること: 在庫で確かめる
    """

    model = StockMove
    form_class = AdjustForm
    service = staticmethod(services.adjust)
    screen_name = "棚卸しの調整"
    screen_help = "実地の在庫数を入れると、今の在庫数との差を動きとして残し、入れた数がそのまま在庫数になります。在庫を数え始める部品は、この画面で今の数を入れます (数え始めた日より前の出荷は、在庫数から引きません)。"
