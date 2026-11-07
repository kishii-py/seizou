"""connect のテンプレートタグ。ほかの部品の画面に、受信した注文の入口を出すために使う (読むだけ)。

受注の画面 (orders/order_list.html) は connect の無い会社でも開くので、このタグを読むのは
connect/_inbox_entrance.html の中だけ (order_list.html には {% load %} を書かない)。
受信した注文の詳細 (connect/inbox_detail.html) の「差出人を選ぶ」の form と、その form を出すか (承認できる人か) の判定も置く。
受信した注文の一覧の行と詳細の「同じ注文番号の注文が既に届いています」の知らせの中身も置く (本体の追記 11)。
payload_number: 届いた本文の数の字を Decimal に読む filter。{{ line.qty|payload_number|comma }} で末尾の 0 を落として出す (読めない字はそのまま)。
payload_price_unit: 届いた明細の単価の後ろに添える単価の単位の字 (「 / 式」「 / 個」の「式」「個」)。
"""

from decimal import Decimal, InvalidOperation

from django import template
from django.db import DatabaseError
from django.urls import reverse

from seizou.core import access
from seizou.masters.services import PRICE_BASIS_UNIT, PRICE_BASIS_VALUES, price_basis_text

from .. import services
from ..forms import SenderForm
from ..models import Inbox

register = template.Library()


@register.simple_tag
def pending_order_count():
    """未承認 (状態が「届いた」) の受信した注文の件数。ダッシュボードの「受信した注文 (未処理)」と同じ数え方。

    表がまだ無い (migrate の前) などで読めなければ 0。
    """
    try:
        return Inbox.objects.filter(status=Inbox.STATUS_RECEIVED).count()
    except DatabaseError:
        return 0


@register.simple_tag
def sender_form():
    """受信した注文の詳細の「差出人を選ぶ」の form ({% sender_form as sender %})。欄は得意先か両方の取引先の選択欄 1 つ。"""
    return SenderForm()


@register.simple_tag(takes_context=True)
def can_accept_inbox(context):
    """今の利用者が受信した注文を承認できるか ({% can_accept_inbox as can_accept %})。

    差出人を選ぶ form を出すかの判定 (権限の無い人に入口を出さない = 決め 11-04 00:0x)。編集の判定が on のときだけ偽になりうる。
    """
    return access.write_allowed(context["request"].user, access.action_name(services.accept))


@register.simple_tag
def same_order_notice(inbox):
    """同じ取引先から同じ注文番号で前に届いた行 (一番古い 1 つ) の知らせの中身 ({% same_order_notice row.obj as same %})。無ければ None。

    返す物は {"pk": 前の行の番号, "status": 前の行の状態の字, "url": 前の行の詳細の URL}。知らせるだけで、断らない・状態を変えない。
    一覧の行は get_queryset の注釈 (same_order_pk・same_order_status) を読み、問い合わせを足さない。詳細は注釈が無いので 1 回だけ引く。
    取引先の空の行 (差出人の確認待ち) と、注文番号の空の行は比べない。
    """
    if hasattr(inbox, "same_order_pk"):
        pk, status = inbox.same_order_pk, inbox.same_order_status
    else:
        order_no = inbox.payload.get("order_no") if isinstance(inbox.payload, dict) else None
        if inbox.partner_id is None or not isinstance(order_no, str) or not order_no:
            return None
        earlier = (
            Inbox.objects.filter(partner_id=inbox.partner_id, pk__lt=inbox.pk, payload__order_no=order_no)
            .order_by("pk")
            .values("pk", "status")
            .first()
        )
        pk, status = (earlier["pk"], earlier["status"]) if earlier else (None, None)
    if pk is None:
        return None
    return {
        "pk": pk,
        "status": dict(Inbox.STATUS_CHOICES).get(status, status),
        "url": reverse("connect:inbox_detail", args=[pk]),
    }


@register.filter
def payload_number(value):
    """届いた本文の数の字 (例 "50.00") を Decimal に読む。{{ line.qty|payload_number|comma }} で末尾の 0 を落として出す。

    字 (str) と整数 (int) だけを読み、前後の空白は落とす (受け口の services._payload_decimal と同じ読み方。float を通さない)。
    読めない字・有限でない数 (NaN など)・数でない値・None は、そのまま返す (comma は字をそのまま出し、None と空の字は空にする)。
    """
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return value
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return value
    return number if number.is_finite() else value


@register.filter
def payload_price_unit(line):
    """受信した注文の明細の単価の後ろに添える字 (読むだけ)。「¥50,000 / 式」の「式」、「¥250 / 個」の「個」(決め PRICE-1b)。

    明細の price_basis が部品の単位 (unit) か式 (lump) ならその値、鍵が無いか読めない値なら部品の単位として、
    masters の price_basis_text (式なら「式」、ほかは明細の単位の字、空なら「個」) を返す。辞書でなければ空の字。
    読めない値の注文は受け入れの時に断る (connect.services の _payload_price_basis)。ここは見せるだけ。
    """
    if not isinstance(line, dict):
        return ""
    basis = line.get("price_basis")
    if not (isinstance(basis, str) and basis in PRICE_BASIS_VALUES):
        basis = PRICE_BASIS_UNIT
    unit = line.get("unit")
    return price_basis_text(basis, unit if isinstance(unit, str) else "")
