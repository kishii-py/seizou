"""業務マニュアルの画面 (02 の 6 節)。画面は全部 core の土台を継承する (R15)。

一覧はフォルダの木 (template tag) と、土台の検索・行・ページ送り・CSV。検索の言葉があれば全部のフォルダを横断する。
画面でない口 (フォルダを開く・ルートへ移るだけ・htmx の部分・画像のファイル) は関数の view。
書く処理は services の関数だけ (R13)。core の使い方のページ (core:manual = /manual/) とは別の物。
"""

from __future__ import annotations

import os

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from seizou.core.views import BaseDetailView, BaseFormView, BaseListView

from . import services
from .forms import FolderForm, ManualCreateForm, ManualForm
from .models import FORMAT_CHOICES, Manual, ManualFolder, ManualImage

FORMAT_VALUES = frozenset(value for value, _label in FORMAT_CHOICES)


def folder_from_param(value):
    """GET の folder の字からフォルダを引く。空はルート (None)。数でない字と無いフォルダは 404。"""
    text = (value or "").strip()
    if not text:
        return None
    if not text.isdigit():
        raise Http404("フォルダの番号が数ではありません。")
    return get_object_or_404(ManualFolder, pk=int(text))


class ManualListView(BaseListView):
    model = Manual
    template_name = "manuals/manual_list.html"
    columns = ("title", ("folder_path_text", "置き場"), "format", "protected", "updated_at")
    search_fields = ("title", "body")
    ordering = ("title", "pk")
    screen_name = "マニュアル"
    screen_help = "作業手順書と社内の文書をフォルダに分けて書き、版を残して育てます。"

    def get_queryset(self):
        manuals = super().get_queryset().select_related("folder")
        if self.search_terms():
            # 検索の言葉があれば全部のフォルダから (横断。行の「置き場」の列で場所が分かる)。
            return manuals
        folder = folder_from_param(self.request.GET.get("folder"))
        if folder is None:
            return manuals.filter(folder__isnull=True)
        return manuals.filter(folder=folder)


class FolderCreateView(BaseFormView):
    model = ManualFolder
    form_class = FolderForm
    service = staticmethod(services.create_folder)
    screen_name = "新しいフォルダ"
    screen_help = "今のフォルダの中に、新しいフォルダを作ります。色はフォルダの印になります。"


class ManualCreateView(BaseFormView):
    model = Manual
    form_class = ManualCreateForm
    service = staticmethod(services.create_manual)
    screen_name = "マニュアルの新規登録"
    screen_help = "置き場と題と形式を決めて作ります。本文は作った後の「編集」で書きます。"


class ManualDetailView(BaseDetailView):
    model = Manual
    template_name = "manuals/manual_detail.html"
    screen_name = "マニュアル"
    screen_help = "本文を読む画面です。左の目次から手順や節へ移れます。"

    def get_queryset(self):
        return Manual.objects.select_related("folder", "created_by")


class ManualUpdateView(BaseFormView):
    model = Manual
    form_class = ManualForm
    service = staticmethod(services.update_manual)
    template_name = "manuals/manual_form.html"
    screen_name = "マニュアルの編集"
    screen_help = "本文を Markdown の字で書きます。上の道具の列で部品を差し込み、「プレビュー」で見た目を確かめます。"


@login_required
def folder_open(request, pk):
    """フォルダを開く口。一覧の ?folder=<pk> へ移す (土台の保存の後の移り先と、パンくずが使う)。"""
    folder = get_object_or_404(ManualFolder, pk=pk)
    return HttpResponseRedirect(f"{reverse('manuals:manual_list')}?folder={folder.pk}")


@login_required
def folder_root(request):
    """ルートの口。一覧 (ルートの直下) へ移す。"""
    return HttpResponseRedirect(reverse("manuals:manual_list"))


@login_required
@require_POST
def manual_preview(request, pk):
    """編集の画面のプレビュー (htmx の部分)。POST の本文と形式を描いて返す。表には書かない。"""
    manual = get_object_or_404(Manual, pk=pk)
    body = request.POST.get("body", "")
    manual_format = request.POST.get("format", "")
    if manual_format not in FORMAT_VALUES:
        manual_format = manual.format
    if len(body) > services.MAX_BODY_CHARS:
        return render(request, "manuals/_preview.html", {"error": services.BODY_TOO_LONG})
    return render(
        request,
        "manuals/_preview.html",
        {"manual": manual, "body": body, "manual_format": manual_format},
    )


@login_required
@require_POST
def image_upload(request, pk):
    """画像を追加する口 (htmx の部分)。画像の並びを返す (編集の画面の打ちかけの本文を消さないため、画面を移さない)。"""
    manual = get_object_or_404(Manual, pk=pk)
    error = None
    try:
        services.add_image(manual, request.FILES.get("file"), request.user)
    except ValueError as exc:
        error = str(exc)
    return render(request, "manuals/_images.html", {"manual": manual, "error": error})


@login_required
@require_POST
def image_delete(request, pk):
    """画像を削除する口 (htmx の部分)。画像の並びを返す。"""
    image = get_object_or_404(ManualImage.objects.select_related("manual"), pk=pk)
    manual = image.manual
    error = None
    try:
        manual = services.remove_image(image, request.user)
    except ValueError as exc:
        error = str(exc)
    return render(request, "manuals/_images.html", {"manual": manual, "error": error})


@login_required
@never_cache
def image_download(request, pk):
    """画像の口 (ログインした人にだけ返す)。無い番号と、storage に無いファイルは 404。"""
    image = get_object_or_404(ManualImage, pk=pk)
    ext = os.path.splitext(image.file.name)[1].lower()
    if ext not in services.IMAGE_TYPES:
        raise Http404("画像の形式が分かりません。")
    try:
        handle = image.file.open("rb")
    except OSError as exc:
        raise Http404("画像のファイルがありません。") from exc
    return FileResponse(
        handle,
        as_attachment=False,
        filename=image.original_name,
        content_type=services.IMAGE_TYPES[ext][0],
    )
