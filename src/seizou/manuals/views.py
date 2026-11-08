"""業務マニュアルの画面 (業務マニュアルの設計の決め)。画面は全部 core の土台を継承する (R15)。

一覧はフォルダの木 (template tag) と、土台の検索・行・ページ送り・CSV。検索の言葉があれば全部のフォルダを横断する。
画面でない口 (フォルダを開く・ルートへ移るだけ・htmx の部分・画像のファイル) は関数の view。
書く処理は services の関数だけ (R13)。core の使い方のページ (core:manual = /manual/) とは別の物。
"""

from __future__ import annotations

import os

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from seizou.core import access, reports
from seizou.core.views import BaseDetailView, BaseFormView, BaseListView, Notice, add_notice_trigger

from . import services
from .forms import (
    FolderDeleteForm,
    FolderEditForm,
    FolderForm,
    FolderMoveForm,
    ManualCreateForm,
    ManualDeleteForm,
    ManualForm,
    ManualMoveForm,
    ManualVersionForm,
    ProtectForm,
    RestoreForm,
)
from .models import FORMAT_CHOICES, Manual, ManualFolder, ManualImage, ManualVersion

FORMAT_VALUES = frozenset(value for value, _label in FORMAT_CHOICES)
#: PDF を作れないときの知らせ (shipping の PDF_NOTICE と同じ字。shipping の views は import しない)
PDF_NOTICE = "PDF の準備がありません。印刷用の画面をお使いください。"
#: 権限の表の action の名前 (字で書かず関数から作る = 名前がずれない。purchasing/acceptance.py と同じ形)
ADD_IMAGE_ACTION = access.action_name(services.add_image)
REMOVE_IMAGE_ACTION = access.action_name(services.remove_image)
PRINT_TEMPLATE = "manuals/manual_print.html"


def folder_from_param(value):
    """GET の folder の字からフォルダを引く。空はルート (None)。数でない字と無いフォルダは 404。"""
    text = (value or "").strip()
    if not text:
        return None
    if not text.isdecimal():
        raise Http404("フォルダの番号が数ではありません。")
    return get_object_or_404(ManualFolder, pk=int(text))


class ManualListView(BaseListView):
    """マニュアルの一覧 (フォルダの木)。

    使う人: 全員 / いつ: 手順を決めたときと、新人に教えるとき (書くのは各部署の担当) / 何のために: 手順書と社内の文書をフォルダごとに持ち、育てる / 次にすること: 読むなら行から、書くなら「新規登録」
    """

    model = Manual
    template_name = "manuals/manual_list.html"
    columns = ("title", ("folder_path_text", "フォルダ"), "format", ("protected", "保護 (移動・削除を止める)"), "updated_at")
    search_fields = ("title", "body")
    ordering = ("title", "pk")
    screen_name = "マニュアル"
    screen_help = "作業手順書と社内の文書をフォルダに分けて書き、版を残して育てます。"

    def get_queryset(self):
        manuals = super().get_queryset().select_related("folder")
        if self.search_terms():
            # 検索の言葉があれば全部のフォルダから (横断。行の「フォルダ」の列で場所が分かる)。
            return manuals
        folder = folder_from_param(self.request.GET.get("folder"))
        if folder is None:
            return manuals.filter(folder__isnull=True)
        return manuals.filter(folder=folder)


class FolderCreateView(BaseFormView):
    """新しいフォルダの入力。

    使う人: 全員 / いつ: マニュアルをフォルダに分けるとき / 何のために: フォルダを作る / 次にすること: マニュアルを置く
    """

    model = ManualFolder
    form_class = FolderForm
    service = staticmethod(services.create_folder)
    screen_name = "新しいフォルダ"
    screen_help = "今のフォルダの中に、新しいフォルダを作ります。色はフォルダの印になります。"


class ManualCreateView(BaseFormView):
    """マニュアルの新規登録の入力。

    使う人: 全員 / いつ: 手順を決めたとき (書くのは各部署の担当) / 何のために: フォルダと題と形式を決めて作る / 次にすること: 「編集」で本文を書く
    """

    model = Manual
    form_class = ManualCreateForm
    service = staticmethod(services.create_manual)
    screen_name = "マニュアルの新規登録"
    screen_help = "フォルダと題と形式を決めて作ります。本文は作った後の「編集」で書きます。"


class ManualDetailView(BaseDetailView):
    """マニュアルの本文を読む画面。

    使う人: 全員 / いつ: 手順を確かめるときと、新人に教えるとき / 何のために: 本文を読む / 次にすること: 直すなら「編集」、前の中身は版の履歴
    """

    model = Manual
    template_name = "manuals/manual_detail.html"
    screen_name = "マニュアル"
    screen_help = "本文を読む画面です。左の目次から手順や節へ移れます。"

    def get_queryset(self):
        return Manual.objects.select_related("folder", "created_by")


class ManualUpdateView(BaseFormView):
    """マニュアルの編集の入力。

    使う人: 全員 / いつ: 手順が変わったとき / 何のために: 本文を書く / 次にすること: プレビューで確かめて保存 (大きく直すなら「新しい版として保存」)
    """

    model = Manual
    form_class = ManualForm
    service = staticmethod(services.update_manual)
    template_name = "manuals/manual_form.html"
    screen_name = "マニュアルの編集"
    screen_help = "本文を Markdown の字で書きます。上の道具の列で部品を差し込み、「プレビュー」で見た目を確かめます。"


class ManualVersionCreateView(BaseFormView):
    """新しい版として保存する入力。

    使う人: 全員 / いつ: 大きく直すとき / 何のために: 前の中身を版として残して保存する / 次にすること: 版の履歴で差分を見る
    """

    model = Manual
    form_class = ManualVersionForm
    service = staticmethod(services.save_as_version)
    template_name = "manuals/manual_form.html"
    screen_name = "新しい版として保存"
    screen_help = "保存前の中身を版として残してから、今の中身を保存します。版の履歴から差分を見て戻せます。"


class ManualVersionListView(BaseDetailView):
    """版の履歴の一覧。

    使う人: 全員 / いつ: 前の中身を見たいとき / 何のために: 残した版の一覧を見る / 次にすること: 差分を見るか、この版に戻す
    """

    model = Manual
    template_name = "manuals/manualversion_list.html"
    screen_name = "版の履歴"
    screen_help = "今の版と、新しい版として保存したときに残った前の中身の一覧です。差分を見て、この版に戻せます。"


class ManualVersionDetailView(BaseDetailView):
    """版と今の中身の差分。

    使う人: 全員 / いつ: 何を直したかを確かめるとき / 何のために: 版と今の中身を並べて見る / 次にすること: 要れば「この版に戻す」
    """

    model = ManualVersion
    template_name = "manuals/manualversion_detail.html"
    screen_name = "差分"
    screen_help = "その版 (左) と今の中身 (右) を、Markdown の字の行のまま並べます。"

    def get_queryset(self):
        return ManualVersion.objects.select_related("manual")


class ManualVersionRestoreView(BaseFormView):
    """この版に戻す確かめの入力。

    使う人: 全員 / いつ: 直し間違えたとき / 何のために: 版の題と本文に戻す / 次にすること: マニュアルを読んで確かめる
    """

    model = ManualVersion
    form_class = RestoreForm
    service = staticmethod(services.restore_version)
    template_name = "manuals/confirm.html"
    screen_name = "この版に戻す"
    screen_help = "その版の題と本文に戻します。今の中身は版として残るので、履歴は消えません。"


class FolderUpdateView(BaseFormView):
    """フォルダの名前と色の変更の入力。

    使う人: 全員 / いつ: フォルダの名前や色を変えるとき / 何のために: 名前と色を直す / 次にすること: マニュアルの一覧へ戻る
    """

    model = ManualFolder
    form_class = FolderEditForm
    service = staticmethod(services.update_folder)
    screen_name = "名前と色の変更"
    screen_help = "フォルダの名前と色を変えます。保護中のフォルダも変えられます。"


class FolderMoveView(BaseFormView):
    """フォルダの移動の入力。

    使う人: 全員 / いつ: フォルダを整理するとき / 何のために: フォルダを中身ごと移す / 次にすること: マニュアルの一覧で確かめる
    """

    model = ManualFolder
    form_class = FolderMoveForm
    service = staticmethod(services.move_folder)
    screen_name = "フォルダの移動"
    screen_help = "フォルダを中身ごと、ほかのフォルダの中かトップへ移します。自分と自分の中のフォルダへは移せません。"


class FolderProtectView(BaseFormView):
    """フォルダの保護の入力。

    使う人: 全員 / いつ: 動かされたくないとき / 何のために: 移動と削除を止める / 次にすること: マニュアルの一覧へ戻る
    """

    model = ManualFolder
    form_class = ProtectForm
    service = staticmethod(services.protect_folder)
    template_name = "manuals/confirm.html"
    screen_name = "フォルダの保護"
    screen_help = "保護したフォルダは、移動と削除ができなくなります。"


class FolderDeleteView(BaseFormView):
    """フォルダの削除の確かめ。

    使う人: 全員 / いつ: 要らなくなったとき / 何のために: フォルダを (中身ごと) 削除する / 次にすること: マニュアルの一覧へ戻る
    """

    model = ManualFolder
    form_class = FolderDeleteForm
    service = staticmethod(services.delete_folder)
    template_name = "manuals/confirm.html"
    screen_name = "フォルダの削除"
    screen_help = "フォルダを削除します。中身があるときは「中身も削除する」を選びます。"


class ManualMoveView(BaseFormView):
    """マニュアルの移動の入力。

    使う人: 全員 / いつ: フォルダを整理するとき / 何のために: マニュアルをほかのフォルダへ移す / 次にすること: マニュアルの一覧で確かめる
    """

    model = Manual
    form_class = ManualMoveForm
    service = staticmethod(services.move_manual)
    screen_name = "マニュアルの移動"
    screen_help = "マニュアルをほかのフォルダかトップへ移します。"


class ManualProtectView(BaseFormView):
    """マニュアルの保護の入力。

    使う人: 全員 / いつ: 動かされたくないとき / 何のために: 移動と削除を止める / 次にすること: マニュアルの一覧へ戻る
    """

    model = Manual
    form_class = ProtectForm
    service = staticmethod(services.protect_manual)
    template_name = "manuals/confirm.html"
    screen_name = "マニュアルの保護"
    screen_help = "保護したマニュアルは、移動と削除ができなくなります。本文の編集と版はそのまま使えます。"


class ManualDeleteView(BaseFormView):
    """マニュアルの削除の確かめ。

    使う人: 全員 / いつ: 要らなくなったとき / 何のために: 版と画像ごと削除する / 次にすること: マニュアルの一覧へ戻る
    """

    model = Manual
    form_class = ManualDeleteForm
    service = staticmethod(services.delete_manual)
    template_name = "manuals/confirm.html"
    screen_name = "マニュアルの削除"
    screen_help = "マニュアルを、版と画像と一緒に削除します。"


@login_required
@never_cache
def manual_pdf(request, pk):
    """A4 の PDF (題・版・最終更新・作った日時の帯とページ番号)。作れなければ印刷用の HTML の上に知らせを出して返す。"""
    manual = get_object_or_404(Manual.objects.select_related("folder"), pk=pk)
    context = {"manual": manual, "path": services.folder_path(manual.folder), "pdf": True}
    try:
        data = reports.render_pdf(PRINT_TEMPLATE, context)
    except reports.PdfUnavailable:
        return HttpResponse(reports.render_html(PRINT_TEMPLATE, {**context, "pdf": False, "pdf_notice": PDF_NOTICE}))
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="manual-{manual.pk}-v{manual.version}.pdf"'
    return response


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
    """画像を追加する口 (htmx の部分)。画像の並びを返す (編集の画面の打ちかけの本文を消さないため、画面を移さない)。

    断った時も枠を返し、断りは見出し HX-Trigger の seizou:notice で右上の赤い知らせに出す (決め 11-07)。
    """
    manual = get_object_or_404(Manual, pk=pk)
    try:
        access.require_write(request.user, ADD_IMAGE_ACTION)
    except access.AccessDenied as exc:
        # 権限の断りはそのまま渡す (add_notice_trigger が文 = message だけを出す)。
        return add_notice_trigger(render(request, "manuals/_images.html", {"manual": manual}), exc)
    notice = None
    try:
        services.add_image(manual, request.FILES.get("file"), request.user)
    except ValueError as exc:
        notice = Notice("入力", str(exc))
    return add_notice_trigger(render(request, "manuals/_images.html", {"manual": manual}), notice)


@login_required
@require_POST
def image_delete(request, pk):
    """画像を削除する口 (htmx の部分)。画像の並びを返す。

    断った時も枠を返し、断りは見出し HX-Trigger の seizou:notice で右上の赤い知らせに出す (決め 11-07)。
    """
    image = get_object_or_404(ManualImage.objects.select_related("manual"), pk=pk)
    manual = image.manual
    try:
        access.require_write(request.user, REMOVE_IMAGE_ACTION)
    except access.AccessDenied as exc:
        # 権限の断りはそのまま渡す (add_notice_trigger が文 = message だけを出す)。
        return add_notice_trigger(render(request, "manuals/_images.html", {"manual": manual}), exc)
    notice = None
    try:
        manual = services.remove_image(image, request.user)
    except ValueError as exc:
        notice = Notice("入力", str(exc))
    return add_notice_trigger(render(request, "manuals/_images.html", {"manual": manual}), notice)


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
