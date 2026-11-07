"""masters の画面 (02 の部品マスターと取引先)。全部 core の土台を継承する (R15)。

一覧は行を押すと詳細、部品の名前の字を押すと小窓 (core の master_link)。会社ごとの項目の列は土台が自動で足す。
部品の小窓は、取引先ごとの品番 (tabs) と会社ごとの項目も出す (masters/_modal.html)。
"""

import csv
from urllib.parse import quote

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET

from seizou.core import csvio, dashboard
from seizou.core.views import BaseDetailView, BaseFormView, BaseListView, BaseModalView, BaseSelectView, FilterSpec

from . import drawings
from . import services
from .forms import ItemForm, PartnerForm, ProcessForm
from .forms import ItemCsvImportForm, PartnerCsvImportForm
from .forms import DrawingRestoreForm
from .forms import HolidayDeleteForm, HolidayForm
from .forms import ItemProcessAddForm, ItemProcessMoveForm, ItemProcessRemoveForm
from .models import Item, Partner, Process
from .models import Holiday
from .models import ItemProcess


#: 部品と取引先 (N135) と工程 (N140) の一覧の絞りの印。既定は切 = 使用中だけを出す。入にすると使用停止の行も薄い字で出す。
#: when は何もしない (使用中だけに絞るのは画面の get_queryset。印が切のとき)。
INACTIVE_FILTER = {"name": "inactive", "label": "使用停止も出す", "kind": "flag", "when": FilterSpec.as_is}


#: 取引先の候補の口の GET の role → 出す取引先の種類 (精査 A9 の 4 節の 1。受注の得意先の欄は customer)。
#: 無い・知らない値なら種類で絞らない (今までどおり全部。マニュアルの紐付けの欄など)。
PARTNER_SELECT_ROLES = {
    "customer": (Partner.ROLE_CUSTOMER, Partner.ROLE_BOTH),
    "supplier": (Partner.ROLE_SUBCONTRACTOR, Partner.ROLE_BOTH),
}


def _hide_inactive(view, queryset):
    """印「使用停止も出す」が切 (既定) なら使用中だけ。件数と CSV も同じ条件になる。"""
    return queryset if view.flag_on("inactive") else queryset.filter(active=True)


class ItemListView(BaseListView):
    """部品マスターの一覧。

    使う人: 全員 / いつ: 部品の情報を見るときと、新しい部品を登録するとき / 何のために: 品番・図面・工程・取引先ごとの品番を 1 か所で持つ / 次にすること: 品名を押して小窓、行から詳細へ。直すのは生産管理
    """

    model = Item
    screen_name = "部品マスター"
    screen_help = "部品 (品番・品名・図面) を 1 か所に持つ表。品名を押すと、画面を移らずに中身を見られます。"
    columns = ("part_no", "name", "drawing_no", "unit", "standard_price")
    search_fields = ("part_no", "name", "drawing_no")
    filters = (INACTIVE_FILTER,)
    ordering = "part_no"
    template_name = "masters/item_list.html"

    def get_queryset(self):
        return _hide_inactive(self, super().get_queryset())


class ItemDetailView(BaseDetailView):
    """部品の詳細。

    使う人: 全員 / いつ: 部品の図面や工程を確かめるとき / 何のために: 項目と取引先ごとの品番と標準の工程を見る / 次にすること: 「編集」か図面ビューアへ。標準の工程は標準の工程のタブの「工程を足す」で足す
    """

    model = Item
    template_name = "masters/item_detail.html"
    screen_name = "部品の詳細"
    screen_help = "部品の項目と、取引先ごとの品番、標準の工程の並び。"
    # 標準の工程のタブの表 (masters/_item_processes.html) が工程のコード・名前・協力企業に出すか・使用中を読むので、工程を行と一緒に 1 回で引く (N174)
    tabs = (("取引先ごとの品番", "partner_items"), ("標準の工程", "item_processes", ("process",)))


class ItemCreateView(BaseFormView):
    """部品の新規登録の入力。

    使う人: 生産管理 / いつ: 新しい部品が増えたとき / 何のために: 品番・品名・図面を登録する / 次にすること: 部品の詳細の「工程を足す」で標準の工程を足し、取引先ごとの品番も足す
    """

    model = Item
    form_class = ItemForm
    service = staticmethod(services.create_item)
    screen_name = "部品の新規登録"
    screen_help = "新しい部品を部品マスターに足します。品番は会社の中で 1 つだけです。"


class ItemUpdateView(BaseFormView):
    """部品の編集の入力。

    使う人: 生産管理 / いつ: 図面や単価が変わったとき / 何のために: 部品の項目を直す / 次にすること: 部品の詳細で確かめる
    """

    model = Item
    form_class = ItemForm
    service = staticmethod(services.update_item)
    screen_name = "部品の編集"
    screen_help = "部品の項目を編集します。"


class ItemModalView(BaseModalView):
    """部品の小窓。

    使う人: 全員 / いつ: 受注や指示書の画面で部品を確かめたいとき / 何のために: 画面を移らずに要点と取引先ごとの品番を見る / 次にすること: 要れば部品の詳細へ
    """

    model = Item
    screen_name = "部品の小窓"
    screen_help = "部品の要点と取引先ごとの品番を見るだけの小窓。詳細へ移れます。"
    tabs = (("取引先ごとの品番", "partner_items"),)


class ItemSelectView(BaseSelectView):
    model = Item
    search_fields = ("part_no", "name")
    # 候補ごとに単位と標準単価も返す (選ぶ欄の JS が受注の明細の行の空の単位と単価の欄に写す。初見テスト R4 4-3b)。
    # 標準単価は金額の閲覧が無い人には返さない (土台の BaseSelectView が外す)。
    columns = ("unit", "standard_price")

    def get_queryset(self):
        # 候補は使用中だけ (N135。受注と発注の入力の欄が、使用停止の部品を出さない)
        return services.active_items()


class PartnerListView(BaseListView):
    """取引先の一覧。

    使う人: 営業・経理 / いつ: 取引先が増えたときと、締日と支払の条件を直すとき / 何のために: 得意先と協力企業を持つ / 次にすること: 行から詳細へ。受注と発注で選ぶ
    """

    model = Partner
    screen_name = "取引先"
    screen_help = "得意先と協力企業。締日・支払日と端数の扱いを取引先ごとに持ちます。"
    columns = ("code", "name", "short_name", "role", "closing_day", "payment_day", {"name": "order_inlet", "label": "注文の入口", "sortable": False})
    search_fields = ("code", "name", "short_name")
    filters = (INACTIVE_FILTER,)
    ordering = "code"
    template_name = "masters/partner_list.html"

    def get_queryset(self):
        # 注文の入口の列 (core.dashboard.annotate_order_inlet の annotate。並び替えと絞り込みはしない。N83 の 6 = N94 の 1b)
        return dashboard.annotate_order_inlet(_hide_inactive(self, super().get_queryset()))


class PartnerDetailView(BaseDetailView):
    """取引先の詳細。

    使う人: 営業・経理 / いつ: 取引先の条件と履歴を見るとき / 何のために: 項目と取引先ごとの品番、受注と発注の履歴を見る / 次にすること: 「編集」へ
    """

    model = Partner
    screen_name = "取引先の詳細"
    screen_help = "取引先の項目と、取引先ごとの品番、受注と発注の履歴。"
    # 受注と発注の履歴は、その部品が入っている会社だけ (土台が hasattr で飛ばす)。本人の決め 11-03 09:2x (協力企業管理の発注履歴)。
    tabs = (("取引先ごとの品番", "partner_items"), ("受注履歴", "orders"), ("発注履歴", "purchase_orders"))
    template_name = "masters/partner_detail.html"


class PartnerCreateView(BaseFormView):
    """取引先の新規登録の入力。

    使う人: 営業 / いつ: 取引先が増えたとき / 何のために: 得意先か協力企業を登録する / 次にすること: 受注と発注で選べるようになる

    消費税の端数の扱いはトータル (請求書の税率ごとの合計に 1 回だけ計算する) だけを選べる (インボイス制度。決め LAW-2)。1 円に丸める向きは「消費税の端数の向き」で選ぶ (決め C95-1b)。
    """

    model = Partner
    form_class = PartnerForm
    service = staticmethod(services.create_partner)
    screen_name = "取引先の新規登録"
    screen_help = "新しい取引先を足します。取引先コードは会社の中で 1 つだけです。"


class PartnerUpdateView(BaseFormView):
    """取引先の編集の入力。

    使う人: 営業・経理 / いつ: 締日・支払日・端数の扱いを直すとき / 何のために: 取引先の項目を直す / 次にすること: 取引先の詳細で確かめる

    消費税の端数の扱いはトータル (請求書の税率ごとの合計に 1 回だけ計算する) だけを選べる (インボイス制度。決め LAW-2)。1 円に丸める向きは「消費税の端数の向き」で選ぶ (決め C95-1b)。
    """

    model = Partner
    form_class = PartnerForm
    service = staticmethod(services.update_partner)
    screen_name = "取引先の編集"
    screen_help = "取引先の項目を編集します。"


class PartnerModalView(BaseModalView):
    """取引先の小窓。

    使う人: 全員 / いつ: 受注や出荷の画面で取引先を確かめたいとき / 何のために: 画面を移らずに要点を見る / 次にすること: 要れば取引先の詳細へ
    """

    model = Partner
    screen_name = "取引先の小窓"
    screen_help = "取引先の要点を見るだけの小窓。詳細へ移れます。"


class PartnerSelectView(BaseSelectView):
    model = Partner
    search_fields = ("code", "name", "short_name")

    def get_queryset(self):
        # 候補は使用中だけ (N135。受注の得意先の欄が、使用停止の取引先を出さない)。
        # GET の role があれば取引先の種類で絞る (customer = 得意先と両方 / supplier = 協力企業と両方。受注の得意先の欄に協力企業を出さない)。
        partners = services.active_partners()
        roles = PARTNER_SELECT_ROLES.get(self.request.GET.get("role", ""))
        return partners.filter(role__in=roles) if roles else partners


class ProcessListView(BaseListView):
    """工程の一覧。

    使う人: 生産管理 / いつ: 工程が増えたとき / 何のために: 工程と「計画に使う」の印を持つ / 次にすること: 印を付けた工程が生産計画の盤に出る
    """

    model = Process
    template_name = "masters/process_list.html"
    screen_name = "工程"
    screen_help = "工程の一覧。「計画に使う」に印を付けた工程ごとに、生産計画の盤が並びます。"
    columns = ("code", "name", "sort_order", "use_in_plan")
    filters = (INACTIVE_FILTER,)
    ordering = ("sort_order", "code")

    def get_queryset(self):
        return _hide_inactive(self, super().get_queryset())


class ProcessDetailView(BaseDetailView):
    """工程の詳細。

    使う人: 生産管理 / いつ: 工程の項目を確かめるとき / 何のために: 名前・並び順・「計画に使う」を見る / 次にすること: 「編集」へ
    """

    model = Process
    template_name = "masters/process_detail.html"
    screen_name = "工程の詳細"
    screen_help = "工程の項目。「編集」で名前や並び順と「計画に使う」の印を変えられます。"


class ProcessCreateView(BaseFormView):
    """工程の新規登録の入力。

    使う人: 生産管理 / いつ: 工程が増えたとき / 何のために: 工程コードと名前を登録する / 次にすること: 「計画に使う」なら生産計画の盤に出る
    """

    model = Process
    form_class = ProcessForm
    service = staticmethod(services.create_process)
    screen_name = "工程の新規登録"
    screen_help = "新しい工程を足します。工程コードは会社の中で 1 つだけです。"


class ProcessUpdateView(BaseFormView):
    """工程の編集の入力。

    使う人: 生産管理 / いつ: 並び順や「計画に使う」を変えるとき / 何のために: 工程の項目を直す / 次にすること: 生産計画の盤で確かめる
    """

    model = Process
    form_class = ProcessForm
    service = staticmethod(services.update_process)
    screen_name = "工程の編集"
    screen_help = "工程の項目を編集します。「計画に使う」に印を付けると、生産計画の盤に出ます。"



# ---------------------------------------------------------------- 標準の工程の並び (決め C19-3)
# 3 つとも model を並びの行 (ItemProcess) にする。部品 (Item) にすると、入力の土台の get_form が部品の会社ごとの項目の欄を
# 小窓に足すため (会社ごとの項目の登録は部品だけ)。3 つの口は部品を返すので、画面の全体の保存の後は部品の詳細へ移る。


class ItemProcessAddView(BaseFormView):
    """標準の工程の追加の入力。

    使う人: 生産管理 / いつ: 部品を新しく登録したときと、作り方の工程が変わったとき / 何のために: 部品の標準の工程の並びに工程を足す / 次にすること: 部品の詳細の標準の工程のタブで順を確かめる
    """

    model = ItemProcess
    form_class = ItemProcessAddForm
    service = staticmethod(services.add_item_processes)
    screen_name = "標準の工程の追加"
    screen_help = "部品の標準の工程の並びの最後に、工程の一覧にある工程を足します。選んだ工程は工程の並び順で並びます。"


class ItemProcessUpdateView(BaseFormView):
    """標準の工程の並べ替えの入力。

    使う人: 生産管理 / いつ: 工程の順が変わったときと、足した工程を途中に入れたいとき / 何のために: 標準の工程の並びで、工程の順を直す / 次にすること: 部品の詳細の標準の工程のタブで確かめる
    """

    model = ItemProcess
    form_class = ItemProcessMoveForm
    service = staticmethod(services.move_item_process)
    screen_name = "標準の工程の並べ替え"
    screen_help = "標準の工程の並びで、1 つの工程を何番目に置くかを直します。ほかの工程は 1 つずつずれます。"


class ItemProcessRemoveView(BaseFormView):
    """標準の工程から外す確かめ。

    使う人: 生産管理 / いつ: 間違えて足した工程や、もう使わない工程を並びから外すとき / 何のために: 標準の工程の並びから工程を外す / 次にすること: 部品の詳細の標準の工程のタブで確かめる
    """

    model = ItemProcess
    form_class = ItemProcessRemoveForm
    service = staticmethod(services.remove_item_process)
    template_name = "masters/itemprocess_remove.html"
    screen_name = "標準の工程から外す"
    screen_help = "部品の標準の工程の並びから工程を 1 つ外します。工程の一覧にある工程と、発行済みの指示書の工程は変わりません。"

# ---------------------------------------------------------------- CSV 取り込み (05 の 8 節)
# 取り込みの画面は model を書かない (土台が会社ごとの項目の欄を form に足すため)。保存の後は土台の既定の移り先 (/) へ移る。


class ItemImportView(BaseFormView):
    """部品マスターの CSV 取り込みの入力。

    使う人: 生産管理・総務 / いつ: 立ち上げのときと、部品をまとめて足すとき / 何のために: CSV から部品をまとめて登録・更新する / 次にすること: 部品マスターで確かめる
    """

    form_class = ItemCsvImportForm
    service = staticmethod(services.import_items)
    template_name = "masters/csv_import.html"
    screen_name = "部品マスターの CSV 取り込み"
    screen_help = "CSV のファイルから部品をまとめて登録し、同じ自社品番の行は更新します。"


class PartnerImportView(BaseFormView):
    """取引先の CSV 取り込みの入力。

    使う人: 営業・総務 / いつ: 立ち上げのときと、取引先をまとめて足すとき / 何のために: CSV から取引先をまとめて登録・更新する / 次にすること: 取引先の一覧で確かめる
    """

    form_class = PartnerCsvImportForm
    service = staticmethod(services.import_partners)
    template_name = "masters/csv_import.html"
    screen_name = "取引先の CSV 取り込み"
    screen_help = "CSV のファイルから取引先をまとめて登録し、同じ取引先コードの行は更新します。"


def _sample_csv(model, filename, fallback):
    """見出しだけの見本の CSV (BOM 付きの UTF-8)。ファイル名の形は一覧の CSV の書き出しと同じ。"""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"
    )
    response.write("\ufeff")
    csv.writer(response).writerow(csvio.header_labels(model))
    return response


@login_required
@require_GET
def item_import_sample(request):
    """部品マスターの見本の CSV (画面でない口)。"""
    return _sample_csv(Item, "部品マスターの見本.csv", "item_sample.csv")


@login_required
@require_GET
def partner_import_sample(request):
    """取引先の見本の CSV (画面でない口)。"""
    return _sample_csv(Partner, "取引先の見本.csv", "partner_sample.csv")


@login_required
@require_GET
@xframe_options_sameorigin
def item_drawing(request, pk, filename):
    """部品の図面のファイルを、ログインした人にだけ返す口 (画面でない口。05 の 7 節)。

    どのファイルを返すか (元か表示用・版・ページ・Content-Type・名前の見出し) は drawings.resolve の 1 か所で決める。
    返す物が無ければ 404。ブラウザに保存させない (ログアウトの後に残った図面を見せない)。
    同じサイトの枠の中では開ける (図面ビューアが PDF を iframe で出す。会社のプロジェクトは X-Frame-Options の DENY が既定)。
    """
    item = get_object_or_404(Item, pk=pk)
    found = drawings.resolve(item, filename, request.GET)
    if found is None:
        raise Http404("図面がありません。")
    response = FileResponse(found.storage.open(found.name, "rb"), content_type=found.content_type)
    response["Content-Disposition"] = found.disposition
    add_never_cache_headers(response)
    return response


# ---------------------------------------------------------------- 図面ビューアと版に戻す (第 3 波)
# 版とページの選び方は template tag (masters_drawing の drawing_viewer) が GET から解く。class は土台の 13 の属性だけ。


class ItemDrawingViewerView(BaseDetailView):
    """図面ビューア。

    使う人: 全員 / いつ: 図面を確かめるときと、前の版と見比べるとき / 何のために: 図面を拡大・回転して見る / 次にすること: 要れば「図面を版に戻す」へ
    """

    model = Item
    template_name = "masters/drawing_viewer.html"
    screen_name = "図面ビューア"
    screen_help = "部品の図面を拡大・回転して見ます。差し替えた前の図面は版として残り、見比べられます。"


class ItemDrawingRestoreView(BaseFormView):
    """図面を版に戻す入力。

    使う人: 生産管理 / いつ: 差し替えた図面を前の版に戻すとき / 何のために: 選んだ版を今の図面にする / 次にすること: 図面ビューアで確かめる
    """

    model = Item
    form_class = DrawingRestoreForm
    service = staticmethod(services.restore_drawing_version)
    template_name = "masters/drawing_restore.html"
    screen_name = "図面を版に戻す"
    screen_help = "選んだ版の図面を今の図面に戻します。戻す前の今の図面は新しい版として残ります。"


# ---------------------------------------------------------------- 会社の休日 (稼働日の数え方は core.calendar)
# 編集の画面は持たない (間違えた日は詳細から削除して足し直す)。詳細に「編集」は出ない (土台の edit_url が無い)。


def _holiday_year(offset: int) -> dict:
    """今年に offset を足した年の 1 月 1 日と 12 月 31 日を、休日の一覧の日付の絞り (f_date_from・f_date_to) の組にする。"""
    year = timezone.localdate().year + offset
    return {"f_date_from": f"{year:04d}-01-01", "f_date_to": f"{year:04d}-12-31"}


def _last_year_holidays():
    """前の年の休日の組 (休日の一覧のタブ「前の年」)。"""
    return _holiday_year(-1)


def _this_year_holidays():
    """今年の休日の組 (休日の一覧のタブ「今年」)。"""
    return _holiday_year(0)


def _next_year_holidays():
    """次の年の休日の組 (休日の一覧のタブ「次の年」)。"""
    return _holiday_year(1)


class HolidayListView(BaseListView):
    """休日の一覧。

    使う人: 生産管理・総務 / いつ: 年の初めと、会社の休みが決まったとき / 何のために: 祝日と会社の休みを 1 か所に持ち、納期までの稼働日を正しく数える / 次にすること: 「新規登録」で足す。間違えた日は詳細から削除する
    """

    model = Holiday
    screen_name = "休日"
    screen_help = "祝日と会社の休みを日付の順に並べる表。生産管理ボードの「納期まで」は、この休日と定休日の曜日を除いた稼働日で数えます。年で絞れます。"
    columns = ("date", "name", "kind", "created_by")
    search_fields = ("name",)
    # 年の絞りは、受注と発注の一覧と同じく、カードの中の絞りのタブと「絞り込み」の枠の「日付」(土台の FilterSpec と presets。N266)。
    # 絞りは土台の get_queryset が検索の後に当てるので、キーワードで探しても年が外れない。「すべて」は土台が足す。
    filters = ({"name": "date", "label": "日付", "kind": "date_range", "field": "date"},)
    presets = (
        {"label": "前の年", "params": _last_year_holidays},
        {"label": "今年", "params": _this_year_holidays},
        {"label": "次の年", "params": _next_year_holidays},
    )
    ordering = ("date",)
    template_name = "masters/holiday_list.html"


class HolidayCreateView(BaseFormView):
    """休日の新規登録の入力。

    使う人: 生産管理・総務 / いつ: 祝日や会社の休みが決まったとき / 何のために: 休日を 1 日足して、稼働日の数え方に入れる / 次にすること: 生産管理ボードの「納期まで」で確かめる
    """

    model = Holiday
    form_class = HolidayForm
    service = staticmethod(services.create_holiday)
    screen_name = "休日の新規登録"
    screen_help = "休日を 1 日足します。日付は 1 日に 1 件だけです。定休日の曜日 (土日など) は足さなくても休みとして数えます。"


class HolidayDetailView(BaseDetailView):
    """休日の詳細。

    使う人: 生産管理・総務 / いつ: 休日の中身を確かめるとき / 何のために: 日付・名前・種類と登録した人を見る / 次にすること: 間違えた日は「削除」で消す
    """

    model = Holiday
    template_name = "masters/holiday_detail.html"
    screen_name = "休日の詳細"
    screen_help = "休日の日付・名前・種類と、登録した人。間違えた日は「削除」で消します。"


class HolidayDeleteView(BaseFormView):
    """休日の削除の確かめ。

    使う人: 生産管理・総務 / いつ: 間違えて足した休日を消すとき / 何のために: 休日を消し、その日を稼働日として数える / 次にすること: 休日の一覧で確かめる
    """

    model = Holiday
    form_class = HolidayDeleteForm
    service = staticmethod(services.delete_holiday)
    template_name = "masters/holiday_delete.html"
    screen_name = "休日の削除"
    screen_help = "休日を消し、休日の一覧に戻ります。消した日は稼働日として数えます。"
