"""attachments の画面と口 (添付ファイルの設計の決め。開発の記録 docs/nights/20261103/N30.md)。画面は core の土台を継承する (R15)。

付けるのは記録の詳細の「添付ファイル」の枠 (attachments/_panel.html) からだけ。付ける画面の名前は
attachment_add にする (attachment_create にすると、土台の一覧が付ける先の無い「新規登録」を出すため)。
保存の後と一覧の行は attachment_detail (付けた先の記録の詳細へ移るだけの口) へ移る。
削除の後は、remove_attachment が返す付けていた先の記録の詳細へ土台が移す。

ファイルを返す口と、移るだけの口は画面でないので関数の view (R15 は class だけを見る)。
ファイルはログインした人にだけ返し、ブラウザに保存させない (土台の画面と同じ no-store)。
media の生の URL と保存の名前は画面に出さない。
開く・削除・一覧・移るだけの口は、付けた先の記録を開ける人の行だけを引く (services.openable_by。外は 404)。
"""

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache

from seizou.core import access
from seizou.core.views import BaseFormView, BaseListView, model_url, url_or_none

from . import services
from .forms import AttachmentDeleteForm, AttachmentForm
from .models import Attachment


class AttachmentListView(BaseListView):
    """添付ファイルの一覧。

    使う人: 全員 / いつ: どこに何を付けたかを探すとき / 何のために: 付けたファイルを全部まとめて探す / 次にすること: 行から付けた先の詳細へ
    """

    model = Attachment
    columns = ("uploaded_at", "kind", "original_name", ("target_label", "付けた先"), "note")
    search_fields = ("original_name", "note")
    ordering = ("-uploaded_at", "-pk")
    screen_name = "添付ファイル"
    screen_help = "受注や部品などの詳細の「添付ファイル」の枠から付けたファイルを、全部まとめて探す一覧。行を押すと、付けた先の詳細を開きます。"

    def get_queryset(self):
        # 付けた先の記録を開ける人の行だけ (部署の外の記録・ほかの人のフィードバックの題とファイル名を出さない。Claude Security の走査の指摘 (11-07))
        return services.openable_by(super().get_queryset(), self.request.user)


class AttachmentAddView(BaseFormView):
    """添付ファイルを付ける入力。

    使う人: 全員 / いつ: 写真や検査成績書を残すとき / 何のために: 記録の詳細にファイルを付ける / 次にすること: 付けた先の詳細の枠から開く
    """

    model = Attachment
    form_class = AttachmentForm
    service = staticmethod(services.add_attachment)
    template_name = "attachments/attachment_form.html"
    screen_name = "添付ファイルを付ける"
    screen_help = "記録の詳細の「添付ファイル」の枠から開き、写真・検査成績書・作業手順などのファイルを付けます。"


class AttachmentDeleteView(BaseFormView):
    """添付ファイルの削除の確かめ。

    使う人: 全員 / いつ: 付け間違えたとき / 何のために: 添付ファイルを削除する / 次にすること: 付けた先の詳細へ戻る
    """

    model = Attachment
    form_class = AttachmentDeleteForm
    service = staticmethod(services.remove_attachment)
    template_name = "attachments/attachment_delete.html"
    screen_name = "添付ファイルの削除"
    screen_help = "添付ファイルを削除し、付けた先の記録の詳細に戻ります。削除したファイルは元に戻せません。"

    def get_queryset(self):
        # 土台の dispatch がこの絞りの中から対象を引く (外は確かめの画面も削除も 404。Claude Security の走査の指摘 (11-07))
        return services.openable_by(Attachment.objects.all(), self.request.user)


@login_required
def attachment_detail(request, pk):
    """付けた先の記録の詳細へ移るだけの口 (保存の後と一覧の行の移り先)。

    付けた先が無いか、付けた先に詳細の画面が無ければ一覧へ (一覧も無ければ /)。
    付けた先の記録を開けない人には 404 (services.openable_by。付けた先の種類と番号を移り先で見せない。Claude Security の走査の指摘 (11-07))。
    """
    attachment = get_object_or_404(services.openable_by(Attachment.objects.all(), request.user), pk=pk)
    target = attachment.target
    url = model_url(target.__class__, "detail", target.pk) if target is not None else None
    return HttpResponseRedirect(url or url_or_none("attachments:attachment_list") or "/")


@never_cache
@login_required
def attachment_download(request, pk):
    """付けたファイルを返す。PNG・JPEG・WebP・PDF はブラウザの中で開き、ほかはダウンロード (services.ALLOWED)。

    ファイル名は元の名前 (日本語は Django が filename*=utf-8''… にする)。形式は表に入れた値。
    無い番号と、storage にファイルが無いときは 404。開いたファイルは FileResponse が閉じる。
    付けた先の記録を開けない人 (部署の外の記録・ほかの人のフィードバックの画面の絵) にも 404 (services.openable_by)。
    訳: 記録を開けない人に、番号を順に変えるだけで付けたファイルを開かせないため。「無い」と「見せない」は外から見分けさせない
    (上げたファイルの口 core.media と同じ 404)。出所 = Claude Security の走査の指摘 (11-07)。
    変えてよい条件 = 記録の部品の開ける人の決めが変わったとき (services.openable_by を直す)。
    金額が主な記録 (services.MONEY_TARGETS) に付けたファイルは、金額の閲覧が log の会社では返して seizou.access に 1 行残す
    (on の会社の金額を見られない人には openable_by が外すので 404。本人の決め 11-08 00:13)。
    """
    attachment = get_object_or_404(services.openable_by(Attachment.objects.all(), request.user), pk=pk)
    if services.is_money_target(attachment):
        access.can_see_money(request.user, what="金額が主な記録の添付を開いた")
    try:
        handle = attachment.file.open("rb")
    except FileNotFoundError:
        raise Http404("ファイルが見つかりません。") from None
    return FileResponse(
        handle,
        as_attachment=not services.opens_inline(attachment.original_name),
        filename=attachment.original_name,
        content_type=attachment.mime,
    )
