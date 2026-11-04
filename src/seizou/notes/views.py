"""notes の画面と口 (決め 11-04 14:5x。N114)。画面は core の土台を継承する (R15)。

メモの枠 (notes/_note.html) は記録の詳細の画面の下に出る (閲覧の形)。「書く」は記入の形 (NoteEditView の画面の
#notes の節。Quill の編集の箱) を htmx で枠と入れ替える (hx-select)。「保存」は土台の入力が検査の層と
services.save_note を通し、付けた先の詳細の画面の #notes へ移る (note_detail)。「やめる」は詳細の画面の #notes を入れ直す。
写真の口 (image_upload) は 1 枚を受けて /media/notes/ に置き、URL を JSON で返す (Quill の写真の handler が呼ぶ)。
画面でない口 (移るだけ・写真) は関数の view (R15 は class だけを見る)。書く処理は services の関数だけ (R13)。
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from seizou.core import access
from seizou.core.views import BaseFormView, model_url

from . import services
from .forms import MESSAGE_TARGET_NOT_FOUND, NoteForm, target_of
from .models import Note

#: 権限の表の action の名前 (字で書かず関数から作る = 名前がずれない。manuals と同じ形)
ADD_IMAGE_ACTION = access.action_name(services.add_image)


class NoteEditView(BaseFormView):
    """メモを書く (記入の形)。

    使う人: 全員 / いつ: 指示書や受注に、段取りの注意や現場の写真を残すとき / 何のために: 記録に書式つきのメモを付ける / 次にすること: 保存して、詳細の画面のメモの枠で読む
    """

    model = Note
    form_class = NoteForm
    service = staticmethod(services.save_note)
    template_name = "notes/note_form.html"
    screen_name = "メモを書く"
    screen_help = "指示書や受注の詳細の「メモ」の枠の「書く」から開き、見出し・太字・箇条書き・色・写真・リンクのあるメモを書きます。"


@login_required
def note_detail(request, pk):
    """付けた先の記録の詳細の #notes へ移るだけの口 (保存の後の移り先)。付けた先か詳細の画面が無ければ / へ。"""
    note = get_object_or_404(Note, pk=pk)
    target = note.target
    url = model_url(target.__class__, "detail", target.pk) if target is not None else None
    return HttpResponseRedirect(f"{url}#notes" if url else "/")


@never_cache
@login_required
@require_POST
def image_upload(request):
    """写真を 1 枚受けて /media/notes/ に置き、{"url": …} を JSON で返す (Quill の写真の handler が呼ぶ)。

    付ける先は POST の content_type と object_id。断るときは {"error": 日本語の文} と 403 (権限)・404 (付ける先)・400 (写真)。
    メモがまだ無い記録では、services.add_image が空のメモの行を先に作る。
    """
    try:
        access.require_write(request.user, ADD_IMAGE_ACTION)
    except access.AccessDenied as exc:
        return JsonResponse({"error": exc.message}, status=403)
    target = target_of(request.POST.get("content_type"), request.POST.get("object_id"))
    if target is None:
        return JsonResponse({"error": MESSAGE_TARGET_NOT_FOUND}, status=404)
    try:
        image = services.add_image(services.note_for(target), request.FILES.get("file"), request.user)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"url": image.file.url})
