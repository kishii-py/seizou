"""connect の受け口 (相手の会社の機械が呼ぶ口)。

画面ではないので core の土台を継承しない関数の view にした。
ログインは要らず、CSRF の対象外で、POST だけを受ける (05 の 5 節)。
確かめと受信箱への登録は services.receive が行う。返事に鍵の値と本文を入れない。
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import services


@csrf_exempt
@require_POST
def receive_order(request):
    """注文のデータを受ける。通れば 200 (同じ番号の 2 回目も同じ 200)、断れば 400。"""
    result = services.receive(request.body, request.headers)
    if isinstance(result, str):
        return JsonResponse({"status": "rejected", "message": result}, status=400)
    return JsonResponse({"status": "received", "id": str(result.message_id)}, status=200)
