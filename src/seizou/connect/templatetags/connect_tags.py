"""connect のテンプレートタグ。ほかの部品の画面に、受信した注文の入口を出すために使う (読むだけ)。

受注の画面 (orders/order_list.html) は connect の無い会社でも開くので、このタグを読むのは
connect/_inbox_entrance.html の中だけ (order_list.html には {% load %} を書かない)。
受信した注文の詳細 (connect/inbox_detail.html) の「差出人を選ぶ」の form と、その form を出すか (承認できる人か) の判定も置く。
受信した注文の一覧の行と詳細の「同じ注文番号の注文が既に届いています」の知らせの中身も置く (本体の追記 11)。
payload_number: 届いた本文の数の字を Decimal に読む filter。{{ line.qty|payload_number|comma }} で末尾の 0 を落として出す (読めない字はそのまま)。
payload_price_unit: 届いた明細の単価の後ろに添える単価の単位の字 (「 / 式」「 / 個」の「式」「個」)。
reply_line_texts: 受信した注文の詳細の「返した条件」の行 (単価を直して受け入れたときに相手へ返した明細ごとの答え)。
revision_notice・revision_lines・revision_problem: 変更と取消の版 (中小企業共通 EDI の注文変更の方式 2) の札・明細状態と受注の今の値・受け入れられない訳。
chat_panel: 受注・発注・受信した注文の「会話」の枠 (connect/_chat.html) の中身 (会話が使えるか・言葉の並び・書く先・書けるか。C104)。
exchange_body: 送り合った記録の詳細の「中身」の枠の字 (JSON を字下げした字と、送ったとおりの字。金額の閲覧の無い人には金額の鍵を外す)。
received_self_invoice_rows・received_self_invoice_registration_note: 受けた仕入明細の詳細の明細の表の行 (受注の数と自社の出荷済みの数と答えの字) と、
登録番号が自社の登録番号と違うときの黄の 1 行の字 (候補 C111)。
"""

from decimal import Decimal, InvalidOperation

from django import template
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone

from seizou.core import access, edi_codes
from seizou.core.templatetags.seizou_tags import comma, yen
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
    # 変更と取消の版の行は同じ注文番号で届くので比べない (版の札 = revision_notice を出す)。
    if _revises_id(inbox) is not None:
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


def _changed(label, value, ordered):
    """「<見出し> <値>」の字。注文の値と違えば「 (注文 <注文の値>)」を付ける。"""
    return f"{label} {value}" + (f" (注文 {ordered})" if value != ordered else "")


@register.simple_tag
def reply_line_texts(inbox, hide_money=False):
    """{% reply_line_texts object seizou.hide_money as reply_texts %}。単価を直して受け入れた (条件付受諾) 行の、返した明細の答えの字の並び。

    条件付受諾 (CA) の明細ごとに「1 行目 T-100: 単価 ¥24 (注文 ¥25.5)・数量 10・納期 2026-11-25 (注文 2026-11-20)」の形。
    相手の品番と注文の値は、届いた本文 (payload) の同じ line_no の明細から引く (line_no が無い明細は並びの番号)。注文と違う所だけ「(注文 …)」を付ける。
    hide_money が真 (金額の閲覧の無い人) なら単価の所を出さない。受諾 (AP) の明細は並べない。読むだけ。
    """
    payload = inbox.payload if isinstance(inbox.payload, dict) else {}
    raw_lines = payload.get("lines")
    ordered = {}
    for number, raw in enumerate(raw_lines if isinstance(raw_lines, list) else [], start=1):
        if not isinstance(raw, dict):
            continue
        line_no = raw.get("line_no")
        ordered[line_no if isinstance(line_no, int) and not isinstance(line_no, bool) else number] = (number, raw)
    texts = []
    for row in inbox.reply_lines if isinstance(inbox.reply_lines, list) else []:
        if not isinstance(row, dict) or row.get("reply_type") != edi_codes.REPLY_CONDITIONAL:
            continue
        number, raw = ordered.get(row.get("line_no"), (row.get("line_no"), {}))
        part_no = raw.get("part_no") if isinstance(raw.get("part_no"), str) else ""
        parts = []
        if not hide_money:
            parts.append(
                _changed("単価", yen(payload_number(row.get("reply_unit_price"))), yen(payload_number(raw.get("unit_price"))))
            )
        parts.append(_changed("数量", comma(payload_number(row.get("reply_qty"))), comma(payload_number(raw.get("qty")))))
        parts.append(_changed("納期", row.get("reply_due_date") or "—", raw.get("due_date") or "—"))
        texts.append(f"{number} 行目 {part_no}: " + "・".join(parts))
    return texts


def _revises_id(inbox):
    """元の行 (第 1 版) の番号。版の列を持たない物 (断片だけを描く試験の作り物の object) は None (第 1 版と同じ扱い)。"""
    return getattr(inbox, "revises_id", None)


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
    if _revises_id(inbox) is not None:
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
    if _revises_id(inbox) is None:
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
    if _revises_id(inbox) is None or inbox.status != Inbox.STATUS_RECEIVED:
        return None
    base = inbox.revises
    if base.status == Inbox.STATUS_RECEIVED:
        return f"先に第 1 版の注文 (受信した注文 {base.pk}) を受け入れてください。"
    payload = inbox.payload if isinstance(inbox.payload, dict) else {}
    return services._revision_problem(base, payload, inbox.revision_no, inbox.purpose_code)


@register.simple_tag
def chat_panel(target, user):
    """会話の枠の中身 (読むだけ)。target は発注・受注・受信した注文のどれか。

    返す物は {"reason": 使えない訳の字 (空 = 使える), "partner_name": 取引先の名前, "rows": [{"message", "header"}] (古い順), "target_kind", "target_id", "can_write"}。
    受注は元の受信した注文を書く先にする (受信した注文の無い受注は reason が「結び付いていない」の字なので欄は押せない)。
    header は吹き出しの上の字: 相手から届いた物は「<取引先の名前> <書いた人の名前 (空なら出さない)> <書いた日時>」、自社が書いた物は「<書いた人の名前> <書いた日時>」。
    can_write は会話に書く画面の部署の門と同じ判定 (access.can_open の connect。記録の行は書かない = record=False)。
    """
    peer = services.chat_peer(target)
    partner_name = peer.partner.name if peer.partner is not None else ""
    rows = []
    # 添付は言葉の数だけ問い合わせが増えないよう、まとめて引く (吹き出しの中の添付の行 = connect/_chat_files.html)。
    for message in services.chat_messages_for(target).prefetch_related("files"):
        written = f"{timezone.localtime(message.written_at):%Y-%m-%d %H:%M}"
        if message.direction == message.DIRECTION_RECEIVED:
            names = [partner_name, message.author_name]
        else:
            names = [message.author_name]
        header = " ".join([name for name in names if name] + [written])
        rows.append({"message": message, "header": header})
    if peer.purchase_order is not None:
        target_kind, target_id = "purchase_order", peer.purchase_order.pk
    elif peer.inbox is not None:
        target_kind, target_id = "inbox", peer.inbox.pk
    else:
        target_kind, target_id = "", ""
    return {
        "reason": peer.reason,
        "partner_name": partner_name,
        "rows": rows,
        "target_kind": target_kind,
        "target_id": target_id,
        "can_write": access.can_open(user, "connect", record=False),
    }


def _without_money(value):
    """JSON の値から、金額の名前 (access.is_money_name) の鍵を鍵ごと外した写し (入れ子の辞書と並びも)。"""
    if isinstance(value, dict):
        return {key: _without_money(item) for key, item in value.items() if not access.is_money_name(key)}
    if isinstance(value, list):
        return [_without_money(item) for item in value]
    return value


@register.simple_tag
def exchange_body(exchange, hide_money=False):
    """{% exchange_body object seizou.hide_money as body %}。送り合った記録の詳細の「中身」の枠の字 (読むだけ)。

    返す物は {"pretty": JSON を字下げした字 (鍵の名前と並びはそのまま), "raw": 送ったとおり・受けたとおりの字}。
    金額の閲覧の無い人 (hide_money が真) には、金額の名前の鍵を鍵ごと外した字にし (決め 11-05 09:4x の「項目そのものを出さない」)、
    送ったとおりの字は出さない (空の字)。JSON として読めない中身は、字のまま pretty に出す (金額の閲覧の無い人には出さない = 両方空)。
    中身の字を書き換えて保存することはしない (表示のための写しだけ)。
    """
    import json

    body = exchange.body or ""
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    if data is None:
        return {"pretty": "", "raw": ""} if hide_money else {"pretty": body, "raw": ""}
    if hide_money:
        return {"pretty": json.dumps(_without_money(data), ensure_ascii=False, indent=2), "raw": ""}
    return {"pretty": json.dumps(data, ensure_ascii=False, indent=2), "raw": body}


def _received_answer_text(line, hide_money=False):
    """受けた仕入明細の明細の自社の答えの字 (コードは出さない)。答えが無ければ空の字。"""
    code = line.reply_type
    if not code:
        return ""
    if code == edi_codes.SI_REPLY_PROPOSED:
        parts = []
        if line.reply_qty is not None and line.reply_qty != line.qty:
            parts.append(f"数 {comma(line.qty)} → {comma(line.reply_qty)}")
        if not hide_money and line.reply_unit_price is not None and line.reply_unit_price != line.unit_price:
            parts.append(f"単価 {yen(line.unit_price)} → {yen(line.reply_unit_price)}")
        return "提案" + (": " + "・".join(parts) if parts else "")
    if code == edi_codes.SI_REPLY_REJECTED:
        reason = dict(edi_codes.SI_REASON_CHOICES).get(line.reply_reason_code, "")
        return "確認できない" + (f": {reason}" if reason else "")
    return dict(edi_codes.SI_REPLY_LINE_TYPES).get(code, code)


@register.simple_tag
def received_self_invoice_rows(received, hide_money=False):
    """受けた仕入明細の詳細の明細の表の行。1 行 = {line・order_line・shipped・answer}。

    shipped は受注の明細の自社の出荷済みの数 (orders.services.shipped_qty。照らす元)。answer は自社の答えの字。
    """
    rows = []
    for line in received.lines.all():
        rows.append(
            {
                "line": line,
                "order_line": line.order_line,
                "shipped": services.shipped_qty(line.order_line),
                "answer": _received_answer_text(line, hide_money),
            }
        )
    return rows


@register.simple_tag
def received_self_invoice_registration_note(received):
    """仕入明細の登録番号が自社の登録番号 (core.Company.registration_no) と違うときの黄の 1 行の字。同じか、どちらかが空なら空の字。"""
    from seizou.core.services import company_or_none

    own = getattr(company_or_none(), "registration_no", "") or ""
    written = received.supplier_registration_no or ""
    if not own or not written or own == written:
        return ""
    return (
        f"仕入明細の登録番号 ({written}) が自社の登録番号 ({own}) と違います。"
        "違っていれば、その明細を「確認できない」(訳は「その他」) で答えてください。"
    )
