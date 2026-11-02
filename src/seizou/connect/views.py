"""connect の画面と受け口。

画面 (受信箱・受信箱の詳細・受け入れる・断る・つながり) は core の土台を継承する (R15)。
class の中で上書きするのは、約束の名前 (model・ordering・columns・template_name・get_queryset など) だけ。
書く処理は services の accept・reject だけで、土台が検査の層を通してから呼ぶ。

受け口 (receive_order) は相手の会社の機械が呼ぶ口で、画面ではないので core の土台を継承しない関数の view にした。
ログインは要らず、CSRF の対象外で、POST だけを受ける (05 の 5 節)。
確かめと受信箱への登録は services.receive が行う。返事に鍵の値と本文を入れない。
"""

from django.db.models import IntegerField, Subquery, Value
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from seizou.core.views import BaseDetailView, BaseFormView, BaseListView

from . import services
from .forms import AcceptForm, ConnectionForm, RejectForm
from .models import Connection, Inbox


@csrf_exempt
@require_POST
def receive_order(request):
    """注文のデータを受ける。通れば 200 (同じ番号の 2 回目も同じ 200)、断れば 400。"""
    result = services.receive(request.body, request.headers)
    if isinstance(result, str):
        return JsonResponse({"status": "rejected", "message": result}, status=400)
    return JsonResponse({"status": "received", "id": str(result.message_id)}, status=200)


# 受信箱。2 秒ごとに、今の一番新しい行より新しい行だけを取りに行き、表の先頭に足す (05 の 5 節)。
# 取りに行く要素は connect/_inbox_poll.html、返す行の部分は connect/_rows.html。
class InboxListView(BaseListView):
    model = Inbox
    screen_name = "受信箱"
    screen_help = "取引先から届いた注文のデータ。2 秒ごとに新しい行を先頭に足します。受け入れるまで受注にはなりません。"
    columns = ("received_at", "partner", "kind", ("summary", "中身の要約"), "status")
    ordering = ("-received_at", "-pk")
    template_name = "connect/inbox_list.html"

    def get_queryset(self):
        # newest_pk = 受信箱の一番新しい行の番号 (どの行にも同じ値)。行と同じ 1 つの問い合わせで読むので、
        # 返す行と次に添える after がずれない。GET の after (数) があれば、それより新しい行だけにする。
        newest = Inbox.objects.order_by("-pk").values("pk")[:1]
        # total_count = 受信箱の全部の件数 (自動の更新で行を足したとき、上の「全 N 件」も直すため。初見テスト 2 回目の所見)。
        queryset = (
            super()
            .get_queryset()
            .select_related("partner")
            .annotate(newest_pk=Subquery(newest), total_count=Value(Inbox.objects.count(), output_field=IntegerField()))
        )
        try:
            after = int(self.request.GET.get("after", ""))
        except ValueError:
            return queryset
        return queryset.filter(pk__gt=after)


class InboxDetailView(BaseDetailView):
    model = Inbox
    screen_name = "受信箱の詳細"
    screen_help = "届いた注文のデータの中身。明細は相手の品番のまま並べています。"
    template_name = "connect/inbox_detail.html"


class InboxAcceptView(BaseFormView):
    model = Inbox
    form_class = AcceptForm
    service = staticmethod(services.accept)
    screen_name = "注文を受け入れる"
    screen_help = "相手の品番ごとに自社の部品を選んで保存すると、受注ができます。表に無い品番の対応は、その場で表に足します。"
    template_name = "connect/inbox_accept.html"


class InboxRejectView(BaseFormView):
    model = Inbox
    form_class = RejectForm
    service = staticmethod(services.reject)
    screen_name = "注文を断る"
    screen_help = "断る理由を書いて保存します。断った注文は受注になりません。"


# つながりの一覧。鍵の値は出さず、置き場の名前と、値が置いてあるか (済み / まだ) だけを出す。入力の画面は作らない。
class ConnectionListView(BaseListView):
    model = Connection
    screen_name = "つながり"
    screen_help = "取引先ごとの受け口と、鍵の置き場の名前。鍵の値は .env にだけ置き、画面には出しません。"
    columns = ("partner", "direction", "url", "secret_env", ("secret_state", "鍵の設定"))
    ordering = ("partner", "direction")


# つながりの新規登録・詳細・編集。書く処理は services の register_connection・update_connection だけ (R13)。
# 鍵の値の欄は持たず、詳細には値が置いてあるか (済み / まだ) と、.env に足す 1 行の名前の形だけを出す。
class ConnectionCreateView(BaseFormView):
    model = Connection
    form_class = ConnectionForm
    service = staticmethod(services.register_connection)
    screen_name = "つながりの新規登録"
    screen_help = "取引先・向き・受け口の URL・鍵の置き場の名前を入れて保存します。鍵の値はここに入れず、.env に置きます。"


class ConnectionDetailView(BaseDetailView):
    model = Connection
    screen_name = "つながりの詳細"
    screen_help = "取引先との受け口と鍵の置き場の名前。鍵の値は画面に出さず、置いてあるか (済み / まだ) だけを出します。"
    template_name = "connect/connection_detail.html"


class ConnectionUpdateView(BaseFormView):
    model = Connection
    form_class = ConnectionForm
    service = staticmethod(services.update_connection)
    screen_name = "つながりの編集"
    screen_help = "受け口の URL や鍵の置き場の名前を直して保存します。鍵の値はここに入れず、.env に置きます。"
