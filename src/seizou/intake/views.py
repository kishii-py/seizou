"""intake の画面 (注文書の読み取りの一覧・読み取る・詳細・受注にする)。

画面は core の土台を継承する (R15)。class の中で上書きするのは、約束の名前 (model・ordering・columns・
search_fields・form_class・service・template_name・screen_name・screen_help) だけ。
書く処理は services の extract_order・accept_extracted だけで、土台が検査の層を通してから呼ぶ。
鍵が無い・形式が違う・API の失敗は services の ValueError で、土台が赤の知らせにする。
"""

from seizou.core.views import BaseDetailView, BaseFormView, BaseListView

from . import services
from .forms import AcceptForm, UploadForm
from .models import Extraction


class ExtractionListView(BaseListView):
    """注文書の読み取りの一覧。

    使う人: 営業 / いつ: 紙や PDF の注文書が来たとき / 何のために: AI が読んだ中身を確かめて受注にする / 次にすること: 「読み取る」か行から詳細へ
    """

    model = Extraction
    screen_name = "注文書の読み取り"
    screen_help = "置いた注文書と、AI が読み取った中身。人が確かめて「受注登録」を押すまで、受注にはなりません。"
    columns = ("read_at", "customer", ("summary", "中身の要約"), "status", {"name": "order", "sort_key": "order__order_no"}, "read_by")
    search_fields = ("customer__name", "customer__code")
    ordering = ("-read_at", "-pk")


class ExtractionCreateView(BaseFormView):
    """注文書を読み取る入力。

    使う人: 営業 / いつ: 紙や PDF の注文書が来たとき / 何のために: 得意先と注文書を置いて AI に明細を読ませる / 次にすること: 読み取りの詳細で中身を確かめる
    """

    model = Extraction
    form_class = UploadForm
    service = staticmethod(services.extract_order)
    screen_name = "注文書を読み取る"
    screen_help = "得意先を選んで注文書の写真か PDF を置くと、AI が明細を読み取ります。受注にする前に、人が中身を確かめます。"
    template_name = "intake/extraction_form.html"


class ExtractionDetailView(BaseDetailView):
    """読み取りの詳細。

    使う人: 営業 / いつ: 読み取った中身を確かめるとき / 何のために: 得意先の品番のままの明細を見る / 次にすること: 「受注登録」へ
    """

    model = Extraction
    screen_name = "読み取りの詳細"
    screen_help = "AI が読み取った注文書の中身。明細は得意先の品番のまま並べています。"
    template_name = "intake/extraction_detail.html"


class ExtractionAcceptView(BaseFormView):
    """読み取りを受注にする入力。

    使う人: 営業 / いつ: 読み取りが正しいと確かめたとき / 何のために: 得意先の品番を自社の部品に対応させて受注を作る / 次にすること: 保存すると生産管理ボードに出る
    """

    model = Extraction
    form_class = AcceptForm
    service = staticmethod(services.accept_extracted)
    screen_name = "読み取りを受注にする"
    screen_help = "得意先の品番ごとに自社の品目を選んで保存すると、受注ができます。表に無い品番の対応は、その場で表に足します。"
    template_name = "intake/extraction_accept.html"
