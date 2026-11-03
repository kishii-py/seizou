"""アカウント管理の画面 (02 の 7 節)。一覧・発行・編集 (使用停止と再開を含む)・削除。

門: 全部の画面は app が accounts なので、core の土台 (seizou.core.access.screen_denied) がアカウント管理の権限を見る
(会社の設定 access_control の 3 状態の外で、いつも効く)。断る変更は services の日本語の ValueError で、土台が赤の知らせにする。
URL の名前は model (Access) の約束 (access_list / access_create / access_update) に合わせる。土台が「新規登録」のボタンと
行の link と保存の後の移り先を作るため。詳細の画面は作らず、行の移り先 (access_detail) は編集へ移るだけの口。
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.urls import reverse

from seizou.core.views import BaseFormView, BaseListView

from . import services
from .forms import AccountDeleteForm, AccountForm
from .models import Access


class AccessListView(BaseListView):
    """アカウント管理の一覧。

    使う人: 総務 / いつ: 人が入ったときと辞めたとき / 何のために: アカウントと部署と権限を持つ / 次にすること: 右上の「新規登録」で発行、行から編集
    """

    model = Access
    screen_name = "アカウント管理"
    screen_help = "アカウントの一覧。ログイン ID で絞り込み、行を押すと編集へ移ります。発行は右上の「新規登録」から。"
    columns = (
        ("user__username", "ログイン ID"),
        ("department_names", "部署"),
        ("permission_names", "権限"),
        ("status_label", "状態"),
        ("last_login", "最終ログイン"),
    )
    search_fields = ("user__username",)
    ordering = ("user__username",)
    template_name = "accounts/access_list.html"

    def get_queryset(self):
        queryset = super().get_queryset().select_related("user").prefetch_related("departments")
        if self.request.GET.get("deleted") != "1":
            queryset = queryset.filter(deleted_at__isnull=True)
        return queryset


@login_required
def access_detail(request, pk):
    """一覧の行の移り先。詳細の画面は作らず、編集の画面へ移る (門は移った先の画面が見る)。"""
    return HttpResponseRedirect(reverse("accounts:access_update", args=[pk]))


class AccessCreateView(BaseFormView):
    """アカウントの発行の入力。

    使う人: 総務 / いつ: 人が入ったとき / 何のために: ログイン ID とパスワードを決め、部署と権限を付ける / 次にすること: ID とパスワードを本人に渡す
    """

    model = Access
    form_class = AccountForm
    service = staticmethod(services.issue_account)
    template_name = "accounts/access_form.html"
    screen_name = "アカウントの発行"
    screen_help = (
        "ログイン ID とパスワードを決め、部署と権限を付けて発行します。"
        "部署は左のメニューに出る画面と開ける画面を、権限は登録・変更・削除できるデータの種類を決めます。"
    )


class AccessUpdateView(BaseFormView):
    """アカウントの編集の入力。

    使う人: 総務 / いつ: 部署や権限が変わったとき / 何のために: 部署と権限を付け外しする / 次にすること: アカウント管理で確かめる
    """

    model = Access
    form_class = AccountForm
    service = staticmethod(services.update_account)
    template_name = "accounts/access_form.html"
    screen_name = "アカウントの編集"
    screen_help = (
        "部署と権限を付け外しします。パスワードは空のままなら変わりません。"
        "使用中を外すと使用停止になり、ログイン中の人も次の画面でログインへ戻ります。"
    )


class AccessDeleteView(BaseFormView):
    """アカウントの削除の確かめ。

    使う人: 総務 / いつ: 人が辞めたとき / 何のために: ログインできなくする / 次にすること: アカウント管理へ戻る
    """

    model = Access
    form_class = AccountDeleteForm
    service = staticmethod(services.delete_account)
    template_name = "accounts/access_delete.html"
    screen_name = "アカウントの削除"
    screen_help = "削除したアカウントではログインできなくなります。記録の名前は残り、同じログイン ID は空きます。"
