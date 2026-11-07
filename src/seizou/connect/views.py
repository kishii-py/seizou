"""connect の画面と受け口。

画面 (受信した注文・受信した注文の詳細・受け入れる・断る・取引先との接続) は core の土台を継承する (R15)。
class の中で上書きするのは、約束の名前 (model・ordering・columns・template_name・get_queryset など) だけ。
書く処理は services の accept・reject だけで、土台が検査の層を通してから呼ぶ。

受け口 (receive_order) は相手の会社の機械が呼ぶ口で、画面ではないので core の土台を継承しない関数の view にした。
ログインは要らず、CSRF の対象外で、POST だけを受ける (05 の 5 節)。
確かめと受信した注文への登録 (注文回答なら元の発注に当てる) は services.receive が行う。返事に鍵の値と本文を入れない。
"""

from django.db.models import Case, IntegerField, OuterRef, Subquery, Value, When
from django.db.models.fields.json import KT
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from seizou.core.views import BaseDetailView, BaseFormView, BaseListView
from seizou.masters.services import active_partners

from . import services
from .forms import AcceptForm, ConnectionForm, RejectForm
from .models import Connection, Inbox


def _sender_choices():
    """InboxListView の取引先の絞りの候補。取引先の種類が得意先か両方で、使用中の物だけ (差出人を選ぶ欄と同じ種類。N135 の active_partners)。"""
    partner = Inbox._meta.get_field("partner").related_model
    rows = active_partners().filter(role__in=(partner.ROLE_CUSTOMER, partner.ROLE_BOTH))
    return [(row.pk, str(row)) for row in rows]


@csrf_exempt
@require_POST
def receive_order(request):
    """注文のデータを受ける。通れば 200 (同じ番号の 2 回目も同じ 200)、断れば 400。"""
    result = services.receive(request.body, request.headers)
    if isinstance(result, str):
        return JsonResponse({"status": "rejected", "message": result}, status=400)
    return JsonResponse({"status": "received", "id": str(result.message_id)}, status=200)


# 受信した注文。2 秒ごとに、今の一番新しい行より新しい行だけを取りに行き、表の先頭に足す (05 の 5 節)。
# 取りに行く要素は connect/_inbox_poll.html、返す行の部分は connect/_rows.html (1 行は connect/_inbox_row.html)。
# 並びは状態が「未処理」の行を上に、その中と残りは受信日時の新しい順。
class InboxListView(BaseListView):
    """受信した注文の一覧。

    使う人: 営業 / いつ: 取引先 (seizou どうし) から注文が届いたとき / 何のために: 受信した注文を確かめて受注にする / 次にすること: 行の操作の「受け入れる」か「断る」へ
    """

    model = Inbox
    screen_name = "受信した注文"
    screen_help = (
        "取引先 (seizou どうし) から受信した注文と、見張るフォルダに置いた注文書の読み取り (列「入口」で分かる)。行の操作の「受け入れる」で受注にすると生産管理ボードに出ます。"
        "差出人が当たらない読み取りは黄の「差出人の確認待ち」で、詳細で差出人を選んでから受け入れます。2 秒ごとに新しい行を先頭に足します。受け入れるまで受注にはなりません。"
    )
    columns = ("received_at", "partner", "source", ("summary", "中身の要約"), "status")
    filters = (
        {"name": "status", "label": "状態", "kind": "choice", "field": "status"},
        {"name": "partner", "label": "取引先", "kind": "lookup", "field": "partner", "choices": _sender_choices},
        {"name": "received", "label": "受信日", "kind": "date_range", "field": "received_at"},
    )
    ordering = (
        Case(When(status=Inbox.STATUS_RECEIVED, then=Value(0)), default=Value(1), output_field=IntegerField()),
        "-received_at",
        "-pk",
    )
    template_name = "connect/inbox_list.html"

    def get_queryset(self):
        # newest_pk = 受信した注文の一番新しい行の番号 (どの行にも同じ値)。行と同じ 1 つの問い合わせで読むので、
        # 返す行と次に添える after がずれない。GET の after (数) があれば、それより新しい行だけにする。
        newest = Inbox.objects.order_by("-pk").values("pk")[:1]
        # total_count = 絞りの後の件数 (絞りが無ければ全部。自動の更新で行を足したとき、上の「全 N 件」も直すため。初見テスト 2 回目の所見)。
        # order は状態の列の受注番号の link のため。
        queryset = (
            super()
            .get_queryset()
            .select_related("partner", "order", "revises")
            .annotate(newest_pk=Subquery(newest), total_count=Value(super().get_queryset().count(), output_field=IntegerField()))
        )
        # same_order_pk・same_order_status = 同じ取引先から同じ注文番号で前に届いた行 (一番古い 1 つ) の番号と状態
        # (本体の追記 11。状態の列の黄の知らせ)。行と同じ 1 つの問い合わせで読む。取引先の空の行と、注文番号が空か null の行は比べない
        # (空の字は JSON の鍵の比べ方で除く。KT の字と "" を比べると SQLite が JSON として読めずに落ちる)。
        earlier = (
            Inbox.objects.annotate(order_no_text=KT("payload__order_no"))
            .filter(partner_id=OuterRef("partner_id"), order_no_text=OuterRef("order_no_text"), pk__lt=OuterRef("pk"))
            # 変更と取消の版の行 (revises がある行) は同じ注文番号で届くので比べない (版の札を出す)。
            .filter(revises=None)
            .exclude(payload__order_no="")
            .exclude(payload__order_no=None)
            .order_by("pk")
        )
        queryset = queryset.annotate(order_no_text=KT("payload__order_no")).annotate(
            same_order_pk=Subquery(earlier.values("pk")[:1]),
            same_order_status=Subquery(earlier.values("status")[:1]),
        )
        # pending_revision_pk・pending_revision_no = 第 1 版の行への未処理の後の版 (一番新しい 1 つ) の番号と版
        # (状態の列の版の札 = connect_tags の revision_notice)。行と同じ 1 つの問い合わせで読む。
        pending = Inbox.objects.filter(revises=OuterRef("pk"), status=Inbox.STATUS_RECEIVED).order_by("-revision_no", "-pk")
        queryset = queryset.annotate(
            pending_revision_pk=Subquery(pending.values("pk")[:1]),
            pending_revision_no=Subquery(pending.values("revision_no")[:1]),
        )
        try:
            after = int(self.request.GET.get("after", ""))
        except ValueError:
            return queryset
        # shown_count = 自動の更新で行を足した後に表に出ている行の数 (下の「N 件中 1–M 件」の M。connect/_rows.html が oob で入れ替える)。
        # 足す前に出ていた行 = 1 ページの大きさと after までの行の数 (全体の件数 − after より新しい行の数) の小さい方。足す行 = 新しい行の数 (1 ページまで)。
        # どの行にも同じ値 (total_count と同じ形。get_context_data は R15 で上書きしないので、ここで annotate する)。
        newer = queryset.filter(pk__gt=after)
        per = self.get_paginate_by(None)
        total = super().get_queryset().count()
        new_count = newer.count()
        shown = min(per, max(total - new_count, 0)) + min(new_count, per)
        return newer.annotate(shown_count=Value(shown, output_field=IntegerField()))


class InboxDetailView(BaseDetailView):
    """受信した注文の詳細。

    使う人: 営業 / いつ: 受信した注文の中身を確かめるとき / 何のために: 相手の品番のままの明細を見る / 次にすること: 「受け入れる」か「断る」へ
    """

    model = Inbox
    screen_name = "受信した注文の詳細"
    screen_help = "受信した注文のデータの中身。明細は相手の品番のまま並べています。"
    template_name = "connect/inbox_detail.html"


class InboxAcceptView(BaseFormView):
    """注文を受け入れる入力。

    使う人: 営業 / いつ: 受信した注文を受注にすると決めたとき / 何のために: 相手の品番を自社の部品に対応させて受注を作る / 次にすること: 保存すると生産管理ボードに出る
    """

    model = Inbox
    form_class = AcceptForm
    service = staticmethod(services.accept)
    screen_name = "注文を受け入れる"
    screen_help = "相手の品番ごとに自社の部品を選んで保存すると、受注ができます。表に無い品番の対応は、その場で表に足します。"
    template_name = "connect/inbox_accept.html"


class InboxRejectView(BaseFormView):
    """注文を断る入力。

    使う人: 営業 / いつ: 受信した注文を受けないと決めたとき / 何のために: 理由を書いて断る (相手に返す) / 次にすること: 受信した注文の一覧へ戻る
    """

    model = Inbox
    form_class = RejectForm
    service = staticmethod(services.reject)
    screen_name = "注文を断る"
    screen_help = "断る理由を書いて保存します。断った注文は受注になりません。"
    template_name = "connect/inbox_reject.html"


# 取引先との接続の一覧。鍵の値は出さず、置き場の名前と、値が置いてあるか (済み / まだ) だけを出す。入力の画面は作らない。
class ConnectionListView(BaseListView):
    """取引先との接続の一覧。

    使う人: 総務 / いつ: 取引先と seizou どうしでつなぐとき / 何のために: 受け口の URL と鍵の置き場の名前を持つ / 次にすること: 「新規登録」の後に .env に鍵を置く
    """

    model = Connection
    screen_name = "取引先との接続"
    screen_help = "取引先ごとの受け口と、鍵の置き場の名前。鍵の値は .env にだけ置き、画面には出しません。"
    columns = ("direction", "partner", "url", "secret_env", ("secret_state", "鍵の設定"))
    ordering = ("partner", "direction")


# 取引先との接続の新規登録・詳細・編集。書く処理は services の register_connection・update_connection だけ (R13)。
# 鍵の値の欄は持たず、詳細には値が置いてあるか (済み / まだ) と、.env に足す 1 行の名前の形だけを出す。
class ConnectionCreateView(BaseFormView):
    """取引先との接続の新規登録の入力。

    使う人: 総務 / いつ: 新しい取引先とつなぐとき / 何のために: 受け口と鍵の置き場の名前を登録する / 次にすること: .env に鍵を置き、詳細で済みを確かめる
    """

    model = Connection
    form_class = ConnectionForm
    service = staticmethod(services.register_connection)
    screen_name = "取引先との接続の新規登録"
    screen_help = "取引先・向き・受け口の URL・鍵の置き場の名前を入れて保存します。鍵の値はここに入れず、.env に置きます。"


class ConnectionDetailView(BaseDetailView):
    """取引先との接続の詳細。

    使う人: 総務 / いつ: 鍵の設定が済んでいるかを確かめるとき / 何のために: 受け口と鍵の置き場の名前と、済みかまだかを見る / 次にすること: 「編集」へ
    """

    model = Connection
    screen_name = "取引先との接続の詳細"
    screen_help = "取引先との受け口と鍵の置き場の名前。鍵の値は画面に出さず、置いてあるか (済み / まだ) だけを出します。"
    template_name = "connect/connection_detail.html"


class ConnectionUpdateView(BaseFormView):
    """取引先との接続の編集の入力。

    使う人: 総務 / いつ: 受け口の URL や鍵の置き場の名前が変わったとき / 何のために: 接続の項目を直す / 次にすること: 詳細で確かめる
    """

    model = Connection
    form_class = ConnectionForm
    service = staticmethod(services.update_connection)
    screen_name = "取引先との接続の編集"
    screen_help = "受け口の URL や鍵の置き場の名前を直して保存します。鍵の値はここに入れず、.env に置きます。"
