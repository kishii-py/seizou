"""connect のテンプレートタグ。ほかの部品の画面に、受信した注文の入口を出すために使う (読むだけ)。

受注の画面 (orders/order_list.html) は connect の無い会社でも開くので、このタグを読むのは
connect/_inbox_entrance.html の中だけ (order_list.html には {% load %} を書かない)。
受信した注文の詳細 (connect/inbox_detail.html) の「差出人を選ぶ」の form と、その form を出すか (承認できる人か) の判定も置く。
受信した注文の一覧の行と詳細の「同じ注文番号の注文が既に届いています」の知らせの中身も置く (本体の追記 11)。
payload_number: 届いた本文の数の字を Decimal に読む filter。{{ line.qty|payload_number|comma }} で末尾の 0 を落として出す (読めない字はそのまま)。
revision_notice・revision_lines・revision_problem: 変更と取消の版 (中小企業共通 EDI の注文変更の方式 2) の札・明細状態と受注の今の値・受け入れられない訳。
"""

from decimal import Decimal, InvalidOperation

from django import template
from django.db import DatabaseError
from django.urls import reverse

from seizou.core import access
from seizou.core import edi_codes

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
    # 変更と取消の版の行は同じ注文番号で届くので比べない (版の札 = revision_notice を出す)。
    if inbox.revises_id is not None:
        return None
    if hasattr(inbox, "same_order_pk"):
        pk, status = inbox.same_order_pk, inbox.same_order_status
    else:
        order_no = inbox.payload.get("order_no") if isinstance(inbox.payload, dict) else None
        if inbox.partner_id is None or not isinstance(order_no, str) or not order_no:
            return None
        earlier = (
            Inbox.objects.filter(partner_id=inbox.partner_id, pk__lt=inbox.pk, payload__order_no=order_no, revises=None)
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


#: 注文書目的コード → 版の字 (詳細の「注文履歴番号」の行と札)
_PURPOSE_WORDS = {edi_codes.PURPOSE_NEW: "新規", edi_codes.PURPOSE_CHANGE: "変更", edi_codes.PURPOSE_CANCEL: "取消"}


@register.filter
def revision_label(inbox):
    """{{ object|revision_label }}。「第 n 版 (新規)」「第 n 版 (変更)」「第 n 版 (取消)」。"""
    return f"第 {inbox.revision_no} 版 ({_PURPOSE_WORDS.get(inbox.purpose_code, inbox.purpose_code)})"


def _notice(label, pk, status):
    return {
        "label": label,
        "pk": pk,
        "status": dict(Inbox.STATUS_CHOICES).get(status, status),
        "url": reverse("connect:inbox_detail", args=[pk]),
    }


@register.simple_tag
def revision_notice(inbox):
    """変更と取消の版の札の中身 ({% revision_notice row.obj as revision %})。無ければ None。

    変更の版の行は {"label": 「第 n 版の変更」か「第 n 版の取消」, 元の行 (第 1 版) の番号と状態と URL}。
    第 1 版の行は、未処理の後の版があれば {"label": 「第 n 版の変更が届いています」, その版の番号・状態・URL}。
    一覧の行は get_queryset の注釈 (pending_revision_pk・pending_revision_no) と select_related の revises を読み、問い合わせを足さない。
    詳細は注釈が無いので 1 回だけ引く。
    """
    if inbox.revises_id is not None:
        word = "取消" if inbox.purpose_code == edi_codes.PURPOSE_CANCEL else "変更"
        return _notice(f"第 {inbox.revision_no} 版の{word}", inbox.revises_id, inbox.revises.status)
    if hasattr(inbox, "pending_revision_pk"):
        pk, number = inbox.pending_revision_pk, inbox.pending_revision_no
    else:
        later = (
            Inbox.objects.filter(revises=inbox, status=Inbox.STATUS_RECEIVED)
            .order_by("-revision_no", "-pk")
            .values("pk", "revision_no")
            .first()
        )
        pk, number = (later["pk"], later["revision_no"]) if later else (None, None)
    if pk is None:
        return None
    return _notice(f"第 {number} 版の変更が届いています", pk, Inbox.STATUS_RECEIVED)


def _number(value):
    """注文の数の字を Decimal に読む。読めなければ None。"""
    number = payload_number(value)
    return number if isinstance(number, Decimal) else None


@register.simple_tag
def revision_lines(inbox):
    """変更と取消の版の明細の並び ({% revision_lines object as rlines %})。第 1 版の行は None (明細の表は今のまま)。

    1 つ = {"line": 注文の明細, "status": 明細状態の名前, "reason": 明細状態理由の名前, "removed": 取り消した明細か,
    "missing": 受注に無い行か (受け入れると足す), "now_qty"・"now_price"・"now_due": 受注の今の値 (注文と違うときだけ。同じなら None)}。
    受注にある行は、明細状態コードによらず数・単価・納期のどれかが注文と違えば「今」の値を持つ (受け入れると注文の値で上書きするので、上書きされる前に見せる)。
    元の行がまだ受注になっていなければ、受注の値は比べない。
    """
    if inbox.revises_id is None:
        return None
    payload = inbox.payload if isinstance(inbox.payload, dict) else {}
    raw_lines = payload.get("lines") if isinstance(payload.get("lines"), list) else []
    order = inbox.revises.order if inbox.revises.order_id else None
    rows = {row.line_no: row for row in order.lines.all()} if order is not None else {}
    statuses = dict(edi_codes.LINE_STATUS_CHOICES)
    reasons = dict(edi_codes.LINE_REASON_CHOICES)
    result = []
    for raw in raw_lines:
        if not isinstance(raw, dict):
            continue
        code = raw.get("line_status_code", edi_codes.LINE_NEW)
        row = rows.get(services._line_number(raw)) if order is not None else None
        removed = code == edi_codes.LINE_DELETED
        entry = {
            "line": raw,
            "status": statuses.get(code, code) if isinstance(code, str) else "",
            "reason": reasons.get(raw.get("line_status_reason_code"), ""),
            "removed": removed,
            "missing": order is not None and row is None and not removed,
            "now_qty": None,
            "now_price": None,
            "now_due": None,
        }
        if row is not None and not removed:
            qty, price, due = _number(raw.get("qty")), _number(raw.get("unit_price")), raw.get("due_date")
            due_now = row.due_date.isoformat() if row.due_date else None
            if qty is not None and qty != row.qty:
                entry["now_qty"] = row.qty
            if price is not None and price != row.unit_price:
                entry["now_price"] = row.unit_price
            if (due or None) != due_now:
                entry["now_due"] = due_now or "—"
        result.append(entry)
    return result


@register.simple_tag
def revision_problem(inbox):
    """未処理の変更と取消の版を受け入れられない訳の字 ({% revision_problem object as problem %})。受け入れられるか、版でない行は None。

    元の行が未処理なら「先に第 1 版の注文 (受信した注文 <番号>) を受け入れてください。」。ほかは受け口と受け入れと同じ見方 (services._revision_problem)。
    """
    if inbox.revises_id is None or inbox.status != Inbox.STATUS_RECEIVED:
        return None
    base = inbox.revises
    if base.status == Inbox.STATUS_RECEIVED:
        return f"先に第 1 版の注文 (受信した注文 {base.pk}) を受け入れてください。"
    payload = inbox.payload if isinstance(inbox.payload, dict) else {}
    return services._revision_problem(base, payload, inbox.revision_no, inbox.purpose_code)
