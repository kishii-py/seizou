"""purchasing の画面 (02 の発注と注文を送る)。全部 core の土台を継承する (R15)。

class の中に書くのは、土台が上書きを許す属性だけ。保存は services の関数だけが行い (R13)、
送るのは connect.services.send_order だけ (R17。HTTP は atomic の外)。
協力企業への発注書の印刷用の HTML と PDF は、請求書と納品書と同じ関数の view (R15 は class だけを見る)。
"""

from seizou.core.views import BaseDetailView, BaseFormView, BaseListView
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from seizou.core import access
from seizou.core.reports import PdfUnavailable, render_html, render_pdf

from seizou.core import edi_codes
from seizou.core.views import BaseSelectView
from django.db.models import Prefetch

from seizou.core.views import BaseModalView
from seizou.masters.services import active_items, active_partners
from seizou.masters.services import with_default_process

from . import services
from .forms import CancelForm, PurchaseOrderForm, SendForm
from .forms import PurchaseOrderChangeForm
from .forms import PurchaseOrderLineCompleteClearForm, PurchaseOrderLineCompleteForm
from .forms import ReceiptForm
from .forms import SelfInvoiceAcceptForm, SelfInvoiceCancelForm, SelfInvoiceForm
from .models import PurchaseOrder
from .models import PurchaseOrderLine, Receipt
from .models import SelfInvoice
from .reports import ORDER_SHEET_TEMPLATE, purchase_order_context

#: 発注書の PDF を作れない環境で、印刷用の HTML の上に出す知らせ (請求書と同じ字)
PDF_NOTICE = "PDF の準備がありません。印刷用の画面をお使いください"


def _subcontractor_choices():
    """発注の一覧の取引先の絞りの候補 (取引先の種類が協力企業か両方で、使用中の物。発注を入れる form の候補と同じ = 開発の記録 docs/nights/20261104/N135.md。名前の順)。"""
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
    # 絞りのタブ (土台の presets。作者の決め)。「すべて」は土台が先頭に足す。組は状態の絞り (f_status) の値だけ。
    # 土台のタブは 4 つまで (PRESET_LIMIT)。「相手が条件付で受けた」「相手が保留した」はタブにせず、状態の絞りで選ぶ (詳細の黄の 1 行で気づく)。
    presets = (
        {"label": "下書き", "params": {"f_status": PurchaseOrder.STATUS_DRAFT}},
        {"label": "送った", "params": {"f_status": PurchaseOrder.STATUS_SENT}},
        {"label": "相手が受け入れた", "params": {"f_status": PurchaseOrder.STATUS_ACCEPTED}},
        {"label": "一部納品", "params": {"f_status": PurchaseOrder.STATUS_PARTIAL}},
    )
    ordering = ("-order_date", "-id")
    template_name = "purchasing/purchaseorder_list.html"

    def get_queryset(self):
        return services.annotate_order_notices(services.annotate_order_received(super().get_queryset().select_related("partner").distinct()))


class PurchaseOrderDetailView(BaseDetailView):
    """発注の詳細。

    使う人: 生産管理 / いつ: 発注の中身と相手の回答を確かめるとき / 何のために: 明細・送った記録・受入を見る / 次にすること: 「受入を登録」へ。「下書き」のままなら送り直しか取消。相手が出荷し終えるまでは「変更」で直した中身を送れる
    """

    model = PurchaseOrder
    screen_name = "発注の詳細"
    screen_help = (
        "発注の項目と明細、相手へ送った記録。「下書き」のときは送り直し、「下書き」「断られた」の発注と相手がまだ出荷していない送った発注は取消ができます。"
        "相手が出荷し終えるまでは「変更」で直した中身を送れます。"
    )
    # 「会話」は connect の会話の言葉 (ChatMessage の related_name。相手の会社とのやり取り = 作者の決め)。最後に置く (件数のある最初のタブで開くので、送った発注は「送った記録」で開く)。
    # 中身は connect/_chat.html (purchaseorder_detail.html の block tab_rows・tab_empty)。connect の無い会社では、そのタブを出さない。
    # 「仕入明細」は、この発注の受入が入った仕入明細 (PurchaseOrder.self_invoices。取消も含む。行の字は番号・版・締め期間・状態。作者の決め)。「会話」の後に置く。
    tabs = (("送った記録", "outbox_messages"), ("受入", "receipts"), ("会話", "chat_messages"), ("仕入明細", "self_invoices"))
    template_name = "purchasing/purchaseorder_detail.html"

    def get_queryset(self):
        return services.annotate_order_notices(services.annotate_order_received(super().get_queryset()))


class PurchaseOrderModalView(BaseModalView):
    """発注の小窓。

    使う人: 生産管理 / いつ: 発注の一覧で行の中身を画面を移らずに確かめたいとき / 何のために: 協力企業・日付・状態・明細の先頭の数行・合計を見る / 次にすること: 要れば「詳細を見る」で発注の詳細へ

    発注の一覧の行の「要点を見る」(目の印。core/_row_summary_link.html) が開く (受け渡しの seizou-部品の6案-20261107/指示書.md の 6。
    本人の決め 11-07 18:2x〜18:4x「5はmodalでいいかな」= 右の覗き窓でなく今の小窓。形は受注の小窓 OrderModalView と同じ)。
    中身は purchasing/_modal.html (頭の札と土台の項目に、明細の先頭の数行を足した物)。読むだけ。
    """

    model = PurchaseOrder
    screen_name = "発注の小窓"
    screen_help = "発注の要点 (協力企業・日付・状態・明細・合計) を見るだけの小窓。発注の一覧の行の目の印から開き、詳細へ移れます。"

    def get_queryset(self):
        # 明細は受入の数 (annotate_received の received_total) を付けて、行番号の順に 1 回で読む (頭の札と明細の表が同じ明細を読む)。
        lines = services.annotate_received(PurchaseOrderLine.objects.select_related("item")).order_by("line_no")
        return PurchaseOrder.objects.select_related("partner").prefetch_related(Prefetch("lines", queryset=lines))


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

    使う人: 生産管理 / いつ: 相手に届いていないとき / 何のために: 「下書き」の発注をもう一度送る (届いていない変更と取消も同じ) / 次にすること: 発注の詳細へ戻る (届けば緑、届かなければ黄の知らせ)
    """

    model = PurchaseOrder
    form_class = SendForm
    service = staticmethod(services.resend_purchase_order)
    screen_name = "注文を送り直す"
    screen_help = "「下書き」のままの発注と、相手に届いていない変更と取消を、つながりの相手へもう一度送ります。"


class PurchaseOrderCancelView(BaseFormView):
    """発注を取り消す確かめの入力。

    使う人: 生産管理 / いつ: 発注が要らなくなったとき / 何のために: 「下書き」の発注を取り消す (「断られた」の発注と、相手がまだ出荷していない送った発注も。送った発注は取消を相手へ送る) / 次にすること: 発注の一覧へ戻る

    取適法: 送った発注の取消に黄色の確かめを出す (purchasing.checks の LawOrderCancel)。
    """

    model = PurchaseOrder
    form_class = CancelForm
    service = staticmethod(services.cancel_purchase_order)
    template_name = "purchasing/purchaseorder_cancel.html"
    screen_name = "発注を取り消す"
    screen_help = (
        "「下書き」「断られた」の発注と、相手がまだ出荷していない送った発注を取り消します (送った発注は取消を相手へ送ります)。"
        "相手が一部を出荷した発注は取り消せないので、「変更」で残りを止めます。理由は備考の末尾に残ります。"
    )


class PurchaseOrderChangeView(BaseFormView):
    """送った発注の変更の入力。

    使う人: 生産管理 / いつ: 送った発注の数・納期・単価を直したいとき (相手が出荷し終えるまで) / 何のために: 直した中身を第 n 版の変更として相手へ送る / 次にすること: 発注の詳細で相手の答えを見る

    取適法: 単価を下げる・数を減らす・納期を早める・明細を取り消す変更に黄色の確かめを出す (purchasing.checks)。
    """

    model = PurchaseOrder
    form_class = PurchaseOrderChangeForm
    service = staticmethod(services.change_purchase_order)
    template_name = "purchasing/purchaseorder_change.html"
    screen_name = "発注の変更"
    screen_help = "送った発注の数・納期・単価・備考を直し、保存と同時に相手へ「第 n 版の変更」として送ります。相手が出荷した明細は直せる所が限られます。"

    def get_queryset(self):
        # 変更を送れる状態 (「送った」「相手が受け入れた」) の発注だけ (外は 404)。
        return PurchaseOrder.objects.filter(status__in=services.CHANGEABLE_STATUSES)


class PurchaseOrderLineCompleteView(BaseFormView):
    """発注の明細を完了にする (受入の足りないまま終える) 確かめの入力。

    使う人: 生産管理 / いつ: 協力企業からの戻りが足りないまま終えるとき (目減り・不良で作り直さない分) / 何のために: 明細を完了にし、受入の候補と「受入の残」から外す / 次にすること: 発注の詳細へ戻る

    入口は発注の詳細の明細の ⋮ の「完了にする」だけ (取消でも断られたでもない発注の、取り消していない終わっていない明細・発注を書ける人だけ。小窓。JS の無いときは画面の全体と ?next=)。
    テンプレートは土台の入力の画面と小窓のまま。残りの数と「変更」の案内の 1 行は form の lead_text (core/_form_card.html が描く)。
    """

    model = PurchaseOrderLine
    form_class = PurchaseOrderLineCompleteForm
    service = staticmethod(services.mark_completed)
    screen_name = "完了にする"
    screen_help = "発注の明細を、残りを受け入れずに完了にします。受け入れた数と金額はそのまま残り、完了は明細の ⋮ の「完了を外す」で外せます。相手の seizou には伝わりません。"


class PurchaseOrderLineCompleteClearView(BaseFormView):
    """発注の明細の完了を外す確かめの入力。

    使う人: 生産管理 / いつ: 誤って完了にしたときや、協力企業がやはり残りを納めると言ったとき / 何のために: 完了の明細の受入の残を戻し、また受け入れられるようにする / 次にすること: 発注の詳細から受入を登録する

    入口は発注の詳細の明細の ⋮ の「完了を外す」だけ (完了の印のある明細・取消でない発注・発注を書ける人だけ。小窓。JS の無いときは画面の全体と ?next=)。
    テンプレートは土台の入力の画面と小窓のまま。
    """

    model = PurchaseOrderLine
    form_class = PurchaseOrderLineCompleteClearForm
    service = staticmethod(services.clear_completed)
    screen_name = "完了を外す"
    screen_help = "発注の明細の完了を外します。足りない分がまた受入の残に数えられ、受入の候補に戻ります。相手の seizou へは何も送りません。"


# --- 受入 (協力企業管理の 2 つ目の画面) ---


class ReceiptListView(BaseListView):
    """受入の一覧。

    使う人: 生産管理・経理 / いつ: 協力企業から物が届いたときと、月の支払の見込みを見るとき / 何のために: 受け入れた数と支払の見込みを見る / 次にすること: 「新規登録」で受入を記録
    """

    model = Receipt
    screen_name = "受入"
    screen_help = (
        "協力企業から受け入れた物の記録。受け入れた数 × 発注の単価が支払の金額になります。"
        "相手が仕入明細で承諾した明細は、仕入明細の金額で数えます。"
        "上の表は取引先ごとの先月と今月の支払の見込みです。"
    )
    columns = (
        ("received_on", "受入日"),
        ("line__purchase_order__po_no", "発注番号"),
        ("line__purchase_order__partner", "取引先"),
        ("line__item", "部品"),
        "qty",
        "amount",
        # 取消でない仕入明細の番号 (Receipt.self_invoice_no。入っていなければ空。作者の決め)
        ("self_invoice_no", "仕入明細"),
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
        # 「仕入明細」の列は受入の仕入明細の明細 (外部キーの逆) を読むので、行ごとに問い合わせないよう prefetch する。
        return (
            super()
            .get_queryset()
            .select_related("line__purchase_order__partner", "line__item")
            .prefetch_related("self_invoice_lines__self_invoice")
        )


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
    template_name = "purchasing/receipt_form.html"
    screen_name = "受入の新規登録"
    screen_help = (
        "発注の明細を字で探して選び、受入日と数量を入れて保存します。"
        "発注の詳細の「受入を登録」から開くと、明細と残の数が入っています。"
    )


# --- 仕入明細 (受入をまとめて相手へ送る書類。作者の決め) ---


class SelfInvoiceListView(BaseListView):
    """仕入明細の一覧。

    使う人: 経理・生産管理 / いつ: 仕入明細の相手の答えを確かめるとき / 何のために: 送った仕入明細と答えの状態を一覧で見る / 次にすること: 行から詳細へ。新しくまとめるなら「新規登録」
    """

    model = SelfInvoice
    screen_name = "仕入明細"
    screen_help = "受入をまとめて協力企業へ送る仕入明細。行を押すと、明細ごとの相手の答えが見られます。"
    columns = ("self_invoice_no", "partner", "issued_on", "period_to", "due_on", "total", "status", "revision_no")
    search_fields = ("self_invoice_no", "partner__name", "partner__code")
    filters = (
        {"name": "status", "label": "状態", "kind": "choice", "field": "status"},
        {"name": "partner", "label": "取引先", "kind": "choice", "field": "partner", "choices": _subcontractor_choices},
        {"name": "issued", "label": "発行日", "kind": "date_range", "field": "issued_on"},
    )
    presets = (
        {"label": "答え待ち", "params": {"f_status": SelfInvoice.STATUS_SENT}},
        {"label": "確かめ中", "params": {"f_status": SelfInvoice.STATUS_PARTIAL}},
    )
    ordering = ("-issued_on", "-id")

    def get_queryset(self):
        return super().get_queryset().select_related("partner")


class SelfInvoiceDetailView(BaseDetailView):
    """仕入明細の詳細。

    使う人: 経理・生産管理 / いつ: 相手の答えを確かめるとき / 何のために: 明細ごとの受領日・数・金額と相手の答え、受入の金額との差を見る / 次にすること: 提案があれば受け入れて直す。確認できない明細は会話で相手と話す

    インボイス制度: 仕入明細書は売り手 (相手) の確認で適格請求書と同じ扱いになる (確かめた資料 = 中小企業共通 EDI インボイス仕様編 3.1.8)。
    取適法: 受領日から 60 日を超える支払期日を黄の 1 行で知らせる (止めない。作者の決め)。
    電子帳簿保存法: 送った中身は送り合った記録に残る。
    """

    model = SelfInvoice
    screen_name = "仕入明細の詳細"
    screen_help = "仕入明細の項目と明細ごとの受領日・数・金額・相手の答え。下の枠で、仕入明細の小計と受入の金額の合計の差を見られます。"
    # 明細のタブの表が受入の発注と部品を読むので、行と一緒に 1 回で引く (請求書の詳細と同じ形)
    tabs = (("明細", "lines", ("receipt__line__purchase_order", "receipt__line__item")),)
    template_name = "purchasing/selfinvoice_detail.html"

    def get_queryset(self):
        return super().get_queryset().select_related("partner").prefetch_related("lines__receipt")


class SelfInvoiceCreateView(BaseFormView):
    """仕入明細の新規登録の入力。

    使う人: 経理・生産管理 / いつ: 協力企業の締日の後 / 何のために: 受け入れた数と金額を仕入明細にまとめて相手に確かめてもらう / 次にすること: 仕入明細の詳細で相手の答えを待つ

    消費税は billing の 1 か所 (consumption_tax) が、仕入明細の合計に 1 回だけ計算する (インボイス制度。この画面は計算しない)。
    """

    model = SelfInvoice
    form_class = SelfInvoiceForm
    service = staticmethod(services.create_self_invoice)
    template_name = "purchasing/selfinvoice_form.html"
    screen_name = "仕入明細を作る"
    screen_help = (
        "取引先を選び、まだ仕入明細に入っていない受入に印を付けてまとめます。"
        "相手 (seizou どうしで注文を送った協力企業) に送ると、相手が明細ごとに承諾か提案か確認できないかを答えます。"
    )


class SelfInvoiceCancelView(BaseFormView):
    """仕入明細を取り消す確かめの入力。

    使う人: 経理・生産管理 / いつ: 受入を入れ間違えた仕入明細を、相手が答える前に直すとき / 何のために: 仕入明細を取り消し、取消を相手へ送る / 次にすること: 受入を選び直して仕入明細を作り直す
    """

    model = SelfInvoice
    form_class = SelfInvoiceCancelForm
    service = staticmethod(services.cancel_self_invoice)
    template_name = "purchasing/selfinvoice_cancel.html"
    screen_name = "仕入明細を取り消す"
    screen_help = "相手がまだどの明細にも答えていない仕入明細を取り消し、取消を相手へ送ります。入っていた受入は、また仕入明細に入れられます。"


class SelfInvoiceAcceptProposalsView(BaseFormView):
    """相手の提案を受け入れる入力。

    使う人: 経理・生産管理 / いつ: 相手から提案の答えが届き、内容が合っていると分かったとき / 何のために: 提案のとおりに仕入明細を直して相手へ送る / 次にすること: 相手の修正の承諾を待つ

    取適法: 提案を受け入れて単価が下がる明細があれば、保存の前に黄色の確かめを出す (purchasing.checks の LawSelfInvoicePriceDown。止めない)。
    """

    model = SelfInvoice
    form_class = SelfInvoiceAcceptForm
    service = staticmethod(services.accept_self_invoice_proposals)
    screen_name = "提案を受け入れる"
    screen_help = "相手が提案と答えた明細のうち、印を付けた明細を提案の数と単価に直し、修正仕入明細として相手へ送ります。"

    def get_queryset(self):
        # 取消でない仕入明細だけ (取消は 404)。
        return SelfInvoice.objects.exclude(status=SelfInvoice.STATUS_CANCELLED)


class PurchaseOrderLineSelectView(BaseSelectView):
    model = PurchaseOrderLine
    search_fields = ("purchase_order__po_no", "purchase_order__partner__name", "item__part_no", "item__name")

    def get_queryset(self):
        # 受入の残が 0 より大きく、発注が取消でない明細だけ。表示の字は PurchaseOrderLine.__str__ (発注番号-行番号 品番 品名 / 取引先 残り n)。
        lines = PurchaseOrderLine.objects.select_related("purchase_order", "purchase_order__partner", "item")
        return (
            services.annotate_received(lines)
            .filter(remaining_total__gt=0)
            .exclude(purchase_order__status=PurchaseOrder.STATUS_CANCELLED)
            # 取り消した明細 (明細状態コード 2) は候補に出さない (受入は断らない = services.receive の現物優先)。
            .exclude(line_status_code=edi_codes.LINE_DELETED)
            # 集計 (Sum) を含む queryset では model の Meta.ordering が効かないので、並びをここで決める。
            .order_by("purchase_order__po_no", "line_no", "pk")
        )


class PurchaseItemSelectView(BaseSelectView):
    model = PurchaseOrderLine._meta.get_field("item").related_model
    search_fields = ("part_no", "name")
    # 単位だけを返す。部品の標準単価は受注の売りの単価で、協力企業への単価と違うので、発注の明細に写さない (作者の決め)。
    # 単位のコード (数量単位コード) も返す (選ぶ欄の JS が明細の単位の欄 (一覧から選ぶ欄) に写す。作者の決め)。
    # default_process は部品の標準の工程に協力企業に出す使用中の工程が 1 つだけのときの工程の pk (masters.services.with_default_process)。
    # 選ぶ欄の JS が同じ行の工程の欄に写す (作者の決め)。
    columns = ("unit", "unit_code", "default_process")

    def get_queryset(self):
        # 候補は使用中だけ (開発の記録 docs/nights/20261104/N135.md。発注の明細の部品の欄。送られた値は form の queryset が同じ絞りで確かめる)
        return with_default_process(active_items())


@login_required
def purchaseorder_print(request, pk):
    """協力企業への発注書の印刷用の HTML (新しいタブで開き、ブラウザで印刷する)。金額が主な書面なので、金額の閲覧の無い人 (on) は 403。"""
    refused = access.refuse_money_document(request, "発注書")
    if refused is not None:
        return refused
    order = get_object_or_404(PurchaseOrder.objects.select_related("partner"), pk=pk)
    return HttpResponse(render_html(ORDER_SHEET_TEMPLATE, purchase_order_context(order)))


@login_required
def purchaseorder_pdf(request, pk):
    """協力企業への発注書の PDF。作れない環境では、印刷用の HTML の上に知らせを出して返す。"""
    refused = access.refuse_money_document(request, "発注書")
    if refused is not None:
        return refused
    order = get_object_or_404(PurchaseOrder.objects.select_related("partner"), pk=pk)
    context = purchase_order_context(order)
    try:
        data = render_pdf(ORDER_SHEET_TEMPLATE, context)
    except PdfUnavailable:
        return HttpResponse(render_html(ORDER_SHEET_TEMPLATE, {**context, "pdf_notice": PDF_NOTICE}))
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{order.po_no}.pdf"'
    return response
