"""稼働の確認の口 (/health/)。監視の道具と nginx が、サーバーが要求に答えるかを見る。

ログインを見ず、session とデータベースを読まず、{"ok": true} だけを返す (中の様子を外に出さない)。
データベースと置き場の確かめは命令 seizou_doctor。
"""

from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe


@never_cache
@require_safe
def health(request):
    """GET と HEAD に 200 と {"ok": true}。ほかの method は 405 (require_safe)。保存させない見出しを付ける。"""
    return JsonResponse({"ok": True})
