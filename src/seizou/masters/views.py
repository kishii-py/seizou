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
from django.utils.cache import add_never_cache_headers
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET

from seizou.core import csvio
from seizou.core.views import BaseDetailView, BaseFormView, BaseListView, BaseModalView, BaseSelectView

from . import drawings
from . import services
from .forms import ItemForm, PartnerForm, ProcessForm
from .forms import ItemCsvImportForm, PartnerCsvImportForm
from .forms import DrawingRestoreForm
from .models import Item, Partner, Process


class ItemListView(BaseListView):
    model = Item
    screen_name = "部品マスター"
    screen_help = "部品 (品番・品名・図面) を 1 か所に持つ表。品名を押すと、画面を移らずに中身を見られます。"
    columns = ("part_no", "name", "drawing_no", "unit", "standard_price")
    search_fields = ("part_no", "name", "drawing_no")
    ordering = "part_no"
    template_name = "masters/item_list.html"


class ItemDetailView(BaseDetailView):
    model = Item
    template_name = "masters/item_detail.html"
    screen_name = "部品の詳細"
    screen_help = "部品の項目と、取引先ごとの品番、標準の工程の並び。"
    tabs = (("取引先ごとの品番", "partner_items"), ("標準の工程", "item_processes"))


class ItemCreateView(BaseFormView):
    model = Item
    form_class = ItemForm
    service = staticmethod(services.create_item)
    screen_name = "部品の新規登録"
    screen_help = "新しい部品を部品マスターに足します。品番は会社の中で 1 つだけです。"


class ItemUpdateView(BaseFormView):
    model = Item
    form_class = ItemForm
    service = staticmethod(services.update_item)
    screen_name = "部品の編集"
    screen_help = "部品の項目を編集します。"


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
    template_name = "masters/partner_list.html"


class PartnerDetailView(BaseDetailView):
    model = Partner
    screen_name = "取引先の詳細"
    screen_help = "取引先の項目と、取引先ごとの品番。"
    tabs = (("取引先ごとの品番", "partner_items"),)


class PartnerCreateView(BaseFormView):
    model = Partner
    form_class = PartnerForm
    service = staticmethod(services.create_partner)
    screen_name = "取引先の新規登録"
    screen_help = "新しい取引先を足します。取引先コードは会社の中で 1 つだけです。"


class PartnerUpdateView(BaseFormView):
    model = Partner
    form_class = PartnerForm
    service = staticmethod(services.update_partner)
    screen_name = "取引先の編集"
    screen_help = "取引先の項目を編集します。"


class PartnerModalView(BaseModalView):
    model = Partner
    screen_name = "取引先の小窓"
    screen_help = "取引先の要点を見るだけの小窓。詳細へ移れます。"


class PartnerSelectView(BaseSelectView):
    model = Partner
    search_fields = ("code", "name", "short_name")


class ProcessListView(BaseListView):
    model = Process
    screen_name = "工程"
    screen_help = "工程の一覧。「計画に使う」に印を付けた工程ごとに、生産計画の盤が並びます。"
    columns = ("code", "name", "sort_order", "use_in_plan")
    ordering = ("sort_order", "code")


class ProcessDetailView(BaseDetailView):
    model = Process
    screen_name = "工程の詳細"
    screen_help = "工程の項目。「編集」で名前や並び順と「計画に使う」の印を変えられます。"


class ProcessCreateView(BaseFormView):
    model = Process
    form_class = ProcessForm
    service = staticmethod(services.create_process)
    screen_name = "工程の新規登録"
    screen_help = "新しい工程を足します。工程コードは会社の中で 1 つだけです。"


class ProcessUpdateView(BaseFormView):
    model = Process
    form_class = ProcessForm
    service = staticmethod(services.update_process)
    screen_name = "工程の編集"
    screen_help = "工程の項目を編集します。「計画に使う」に印を付けると、生産計画の盤に出ます。"


# ---------------------------------------------------------------- CSV 取り込み (05 の 8 節)
# 取り込みの画面は model を書かない (土台が会社ごとの項目の欄を form に足すため)。保存の後は土台の既定の移り先 (/) へ移る。


class ItemImportView(BaseFormView):
    form_class = ItemCsvImportForm
    service = staticmethod(services.import_items)
    template_name = "masters/csv_import.html"
    screen_name = "部品マスターの CSV 取り込み"
    screen_help = "CSV のファイルから部品をまとめて登録し、同じ自社品番の行は更新します。"


class PartnerImportView(BaseFormView):
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
# 版とページの選び方は template tag (masters_drawing の drawing_viewer) が GET から解く。class は土台の 11 の属性だけ。


class ItemDrawingViewerView(BaseDetailView):
    model = Item
    template_name = "masters/drawing_viewer.html"
    screen_name = "図面ビューア"
    screen_help = "部品の図面を拡大・回転して見ます。差し替えた前の図面は版として残り、見比べられます。"


class ItemDrawingRestoreView(BaseFormView):
    model = Item
    form_class = DrawingRestoreForm
    service = staticmethod(services.restore_drawing_version)
    template_name = "masters/drawing_restore.html"
    screen_name = "図面を版に戻す"
    screen_help = "選んだ版の図面を今の図面に戻します。戻す前の今の図面は新しい版として残ります。"
