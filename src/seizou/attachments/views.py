"""attachments の口 (02 の 5 節の N30)。

ファイルを返す口は画面でないので関数の view (R15 は class だけを見る)。ログインした人にだけ返し、
ブラウザに保存させない (土台の画面と同じ no-store)。media の生の URL と保存の名前は画面に出さない。
"""

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache

from . import services
from .models import Attachment


@never_cache
@login_required
def attachment_download(request, pk):
    """付けたファイルを返す。PNG・JPEG・WebP・PDF はブラウザの中で開き、ほかはダウンロード (services.ALLOWED)。

    ファイル名は元の名前 (日本語は Django が filename*=utf-8''… にする)。形式は表に入れた値。
    無い番号と、storage にファイルが無いときは 404。開いたファイルは FileResponse が閉じる。
    """
    attachment = get_object_or_404(Attachment, pk=pk)
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
