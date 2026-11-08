"""stock のテンプレートの tag。読むだけで、表に書かない。

- move_source: 在庫の動き 1 件の「元の記録」の字と、その詳細の URL (作者の決め)。
  在庫の動きの詳細 stock/stockmove_detail.html が、土台の core/detail.html の block basic_extra (基本の表の最後) の中で
  {% move_source object as source %} の形で引き、「元の記録」の行を 1 つ描く。
  元の記録は製造の完了なら指示書、受入なら受入の行 (stock.services の record_finished と record_received が source に渡す物)。
  stock は workorders と purchasing の model を import しない (stock/models.py の頭の決まり)。URL は元の記録の物の
  class (type(source)) から core の model_url で引く。
"""

from django import template

from seizou.core import access
from seizou.core.views import model_url

register = template.Library()


@register.simple_tag(takes_context=True)
def move_source(context, move) -> dict:
    """{% move_source object as source %}。在庫の動き move の元の記録の {"label": 字, "url": URL か None}。

    label = move.source_label() (一覧の「元の記録」の列と同じ字。元の記録が無い払い出しと棚卸しの調整は空の字)。
    url = 元の記録の詳細の URL (指示書 = workorders:workorder_detail、受入 = purchasing:receipt_detail)。
    元の記録が無いとき、詳細の URL が引けないときは None。URL が引けたときだけ、見ている人がその部品を部署で開けるかを
    access.can_open で見て、開けない人 (部署の判定が on で、その部品を部署で使わない人) には None にして字だけで出す。
    詳細の画面は部署を見ないので、押した先で 403 になるから隠すのではない。部署で使わない人には関わる記録を字だけで出す
    今の作り (shipping の deliverynote_billing の請求書・受注の詳細の「流れ」の枠の orders/timeline.py の _linked_url) と、同じ基本の表に link を足す
    作者の決めにそろえるため。字は誰にでも出し、押した先の画面の門は変えない。
    can_open は record=False で呼ぶ (部署の判定が log の会社で、開いていない画面を開いたと部署の記録に書かないため)。
    """
    source = move.source
    label = move.source_label()
    url = None if source is None else model_url(type(source), "detail", source.pk)
    if url is not None:
        request = context.get("request")
        if not access.can_open(getattr(request, "user", None), source._meta.app_label, record=False):
            url = None
    return {"label": label, "url": url}
