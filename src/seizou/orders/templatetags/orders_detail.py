"""受注の詳細の右の列 (作者の決め。開発の記録 docs/nights/20261105/N150.md) のテンプレートの tag。読むだけで、表に書かない。

- order_aside: 右の列のカードの並び (得意先・日付・金額・基本情報)。土台の詳細の画面は文脈を足す口を持たない (R15) ため、
  受注の詳細の block content が {% order_aside object fields seizou.hide_money as aside %} の形で置いてから block.super を呼び、
  土台の core/detail.html が aside を読んで 2 列にする (流れの枠の {% order_timeline object as events %} と同じ tag の形)。
  aside = {"cards": [カード]}。カード = {"title": 題, "rows": [行]} と、基本情報のカードだけ "help" (枠の「?」の part) と
  "help_label" (「?」の名前)。行は土台の詳細の項目と同じ形 {"label", "value", "master", "file", "money"}。
- 金額の閲覧は seizou.hide_money を読むだけ (access.can_see_money を呼ばない = 金額の閲覧の記録を増やさない)。
- order_head_chips: 受注の詳細の頭の要点の札 (開発の記録 docs/nights/20261105/N163.md)。受注の詳細の block heading が {% order_head_chips object seizou.hide_money as head_chips %}
  の形で置いてから block.super を呼び、土台の core/detail.html が head_chips を読んで題の横に 3 つまで並べる。
- order_line_allocations: 明細の表の行と、行ごとの引当の合計の組 (「預かり」の列。決め 11-06 18:3x。開発の記録 docs/nights/20261106/N184.md)。
- order_next_action: 頭の緑のボタン (その受注で次にする操作 1 つ。「入荷を記録する」「指示書を発行する」「出荷登録」のどれか) の中身
  (決め 11-06 22:1x と、本人の決め 11-12 19:0x)。受注の詳細の block heading が {% order_next_action object ... as next_action %} の形で置き、
  block actions と block edit_button が読む (head_chips と同じ形)。
- order_line_completed_label: 明細の表の残の枡の「完了」「完了 (出さない n)」の印の字 (N400b。完了の印の無い明細は空の字)。
- order_total: 明細の金額の合計 (受注の小窓 orders/_modal.html の「明細の合計」。order_aside の金額のカードと同じ式。受け渡しの seizou-部品の6案-20261107/指示書.md の 6)。
- order_revision_notice: 取引先から取消か変更が届いたまま、受け入れも断りもしていない受注の黄色の 1 行の中身 (依頼書 N417。
  受注の詳細と出荷の新規登録の画面の全体と小窓が {% order_revision_notice object as revision %} の形で置き、orders/_revision_notice.html が出す)。
"""

from decimal import Decimal

from django import template
from django.apps import apps
from django.urls import NoReverseMatch, reverse

from seizou.core import edi_codes
from seizou.core.templatetags.seizou_tags import comma, weekday_ja, yen

from ..models import Order, OrderLine

register = template.Library()

_ZERO = Decimal("0")


def _row(label, value=None, master=None, money=False) -> dict:
    """カードの 1 行 (土台の詳細の項目と同じ形)。master があれば小窓を開く字になる。money が真なら値の頭に ¥ を付ける
    (core/detail.html の yen_if の引数。全部の行が鍵を持たないと、テンプレートが VariableDoesNotExist で落ちる)。"""
    return {"label": label, "value": value, "master": master, "file": None, "money": money}


def _label(order, name) -> str:
    """項目の名 (基本情報の字と同じ verbose_name)。"""
    return str(order._meta.get_field(name).verbose_name)


@register.simple_tag
def order_aside(order, fields, hide_money=False) -> dict:
    """受注の詳細の右の列。

    得意先 (名前は小窓を開く字・取引先コード)・日付 (受注日・相手の発注日・明細の一番早い納期)・金額 (明細の金額の合計。明細があり、
    金額を見られる人 = hide_money が偽のときだけ)・基本情報 (土台の項目 fields から得意先と受注日を除いた物)。
    相手の発注日は、受信した注文から作った受注で、その payload の発注日が読めるときだけ出す (作者の決め)。
    受注日は編集で直せるので、行の名「受注日」は変えずに行を分ける (直した後も両方の字が正しい)。
    受信した注文は逆向きの 1 対 1 (inbox_message) で引き、connect の model を import しない (orders/timeline.py と同じ引き方)。
    """
    customer = order.customer
    lines = list(order.lines.all())
    dues = sorted(line.due_date for line in lines if line.due_date)
    moved = (_label(order, "customer"), _label(order, "order_date"))
    inbox = getattr(order, "inbox_message", None)
    buyer_date = inbox.buyer_order_date() if inbox is not None else None
    date_rows = [_row(_label(order, "order_date"), order.order_date)]
    if buyer_date is not None:
        date_rows.append(_row("相手の発注日", buyer_date))
    date_rows.append(_row("一番早い納期", dues[0] if dues else None))
    cards = [
        {"title": _label(order, "customer"), "rows": [_row("名前", master=customer), _row("取引先コード", customer.code)]},
        {"title": "日付", "rows": date_rows},
    ]
    if lines and not hide_money:
        total = sum((line.amount for line in lines), Decimal("0"))
        cards.append({"title": "金額", "rows": [_row("明細の合計", total, money=True)]})
    cards.append(
        {
            "title": "基本情報",
            "rows": [field for field in fields if field["label"] not in moved],
            "help": "basic",
            "help_label": "基本情報の説明",
        }
    )
    return {"cards": cards}


def _revision_target_order(target):
    """受注か、受注の明細か、受注の明細の番号 (字か数) から受注を読む。どれでもなければ None。

    明細の番号は、その明細を 1 回引いて受注を読む (無ければ None)。番号でない字 (空・小数・英字) と真偽の値は表を読まずに None。
    """
    if isinstance(target, Order):
        return target
    if isinstance(target, OrderLine):
        return target.order
    if isinstance(target, bool) or not isinstance(target, (int, str)) or not str(target).isdecimal():
        return None
    line = OrderLine.objects.select_related("order").filter(pk=int(target)).first()
    return line.order if line is not None else None


@register.simple_tag
def order_revision_notice(target):
    """受注の画面の上の黄色の 1 行 (知らせの段 5 の状態) の中身 ({% order_revision_notice object as revision %})。出さないときは None。

    target は受注か、受注の明細か、受注の明細の番号 (出荷の新規登録の form.order_line.value)。どれでもなければ None。
    受注の受信した注文 (逆向きの 1 対 1 の inbox_message。無ければ None) の後の版 (revisions) のうち、状態が未処理の物で
    版の番号が一番大きい物を 1 回の問い合わせで引く。受け入れた版と断った版は出さない (受け入れた版は受注に入っていて、
    断った版は受注が前の版のままで正しい)。保留を答えた版は状態が未処理のままなので出す。
    返す物 = {"text": 字, "url": その版の受信した注文の詳細の URL (名前が引けなければ None)}。字は目的のコードで「取消」と「変更」を選び、金額を入れない。
    connect の model を import しない (状態の値は受信した注文の行から読む。order_aside と同じ引き方)。読むだけで、表に書かない。

    訳: 取引先から取消か変更が届いたまま、受け入れも断りもしていない受注を、受注の詳細と出荷の入力で分かるようにし、
    取り消された物を作ったり出荷したりしないため。止めない (保存の前の確認も足さない。本人の決め 11-11 12:5x の考え方)。
    出所 = 本体の決め 11-11 14:2x (精査 Q3 の T7・M6)。変えてよい条件 = 届いた版を受注の画面で受け入れる形を足すとき。
    """
    order = _revision_target_order(target)
    inbox = getattr(order, "inbox_message", None) if order is not None else None
    if inbox is None:
        return None
    later = (
        inbox.revisions.filter(status=inbox.STATUS_RECEIVED)
        .order_by("-revision_no", "-pk")
        .values("pk", "revision_no", "purpose_code")
        .first()
    )
    if later is None:
        return None
    word = "取消" if later["purpose_code"] == edi_codes.PURPOSE_CANCEL else "変更"
    try:
        url = reverse("connect:inbox_detail", args=[later["pk"]])
    except NoReverseMatch:
        url = None
    return {
        "text": (
            f"取引先から第 {later['revision_no']} 版の{word}が届いています。"
            "受け入れるまで、この受注は前の版の中身のままです。受信した注文の詳細で、受け入れるか断ってください。"
        ),
        "url": url,
    }


@register.simple_tag
def order_total(order) -> Decimal:
    """明細の金額の合計 ({% order_total object as total %})。order_aside の金額のカードと同じ式 (読むだけ)。金額を見せるかは呼ぶ側 (seizou.hide_money) が決める。"""
    return sum((line.amount for line in order.lines.all()), _ZERO)


@register.simple_tag
def order_head_chips(order, hide_money=False) -> list[dict]:
    """受注の詳細の頭の要点の札 (開発の記録 docs/nights/20261105/N163.md)。札 = {"label", "value"} (値は字)。土台の core/detail.html が head_chips を読んで題の横に並べる。

    得意先 (名前)・一番早い納期 (明細の納期の一番早い日に曜日の 1 字を添える = 作者の決め (開発の記録 docs/nights/20261104/N132.md)。無ければ札を出さない)・合計 (明細の金額の合計。頭に ¥。明細があり、
    金額を見られる人 = hide_money が偽のときだけ)。右の列のカード (order_aside) は残す (札はその要約)。
    """
    lines = list(order.lines.all())
    dues = sorted(line.due_date for line in lines if line.due_date)
    chips = [{"label": _label(order, "customer"), "value": order.customer.name}]
    if dues:
        chips.append({"label": "一番早い納期", "value": f"{dues[0]:%Y-%m-%d} ({weekday_ja(dues[0])})"})
    if lines and not hide_money:
        chips.append({"label": "合計", "value": yen(sum((line.amount for line in lines), Decimal("0")))})
    return chips


def _allocation_model():
    """引当の表 (Allocation)。receiving の部品が無ければ None (預かりの値は 0。orders/board.py の _shipment_model の形)。"""
    try:
        return apps.get_model("receiving", "Allocation")
    except LookupError:
        return None


@register.simple_tag
def order_line_allocations(order) -> list[tuple]:
    """受注の詳細の明細の行と、行ごとの引当の合計の組の並び [(明細, 引当の合計)] (「預かり」の列。決め 11-06 18:3x。開発の記録 docs/nights/20261106/N184.md)。

    明細は order.lines.all() (画面の prefetch の写し) の順。引当の合計は receiving.services.annotate_allocated を
    明細の queryset に 1 回当てて読む (明細の数だけ SQL を出さない)。receiving の部品が無ければ全部 0 で、問い合わせをしない。
    読むだけで、表に書かない。
    """
    lines = list(order.lines.all())
    if not lines or _allocation_model() is None:
        return [(line, _ZERO) for line in lines]
    from seizou.receiving.services import annotate_allocated

    totals = dict(
        annotate_allocated(OrderLine.objects.filter(order=order).order_by()).values_list("pk", "allocated_total")
    )
    return [(line, totals.get(line.pk) or _ZERO) for line in lines]


#: 受注の詳細の頭の緑のボタンの操作 (決める順)。1 つ = (BoardRow.next_action の値, ボタンの字, 絵記号, 行を選ぶメニューの見出し)
_NEXT_ACTIONS = (
    ("receive", "入荷を記録する", "download", "入荷を記録する行を選ぶ"),
    ("issue", "指示書を発行する", "factory", "指示書を発行する行を選ぶ"),
    ("ship", "出荷登録", "truck", "出荷する行を選ぶ"),
)


@register.simple_tag
def order_next_action(order, can_receive, can_issue, can_ship, receiving_url, issue_url, ship_url):
    """受注の詳細の頭の緑のボタン (その伝票で次にする操作 1 つ) の中身。出す物が無ければ None (土台の「編集」が緑になる)。

    返す物 = {"key", "label" (ボタンの字), "icon", "menu_title" (2 行以上の時の行を選ぶメニューの見出し), "url" (小窓の入口。?order_line=<pk> を付ける),
    "lines" (その操作の明細。行番号の順)}。
    決め方 (本人の決め 11-12 19:0x): 入荷を記録する → 指示書を発行する → 出荷登録 の順に、当たる明細が 1 つでもあり、その人が行える
    (書く権限があり、部品が入っている = URL が空でない) 最初の操作。明細ごとの次の操作は board の BoardRow.next_action (生産管理ボードの判断と同じ見方)。
    出荷登録は、今までどおり残のある明細の全部を並べる (入荷か指示書が要る明細も、その操作を行えない人は出荷を選べる = 作者の決め 11-06 22:1x の形)。
    取消の受注は None。権限と URL は呼ぶ側のテンプレートが can_write と url の tag で渡す (明細の ⋮ のメニューと同じ答え)。
    order.lines.all() は画面の prefetch の写し (annotate_remaining の残・引当の合計・在庫数つき) なので、問い合わせを増やさない。読むだけ。
    訳: 受注を入れた直後に次にするのは指示書の発行 (入荷が要る会社は入荷) なのに、緑が「出荷登録」で、押すと止まる会社があった。
    出所 = 本人の決め 11-12 19:0x・精査「事務の 1 人の 1 日」の 9。変えてよい条件 = 本人が緑のボタンの決め方を変えたとき。
    """
    if order.status == order.STATUS_CANCELLED:
        return None
    open_lines = [line for line in order.lines.all() if line.remaining_total > 0]
    allowed = {"receive": bool(can_receive and receiving_url), "issue": bool(can_issue and issue_url), "ship": bool(can_ship and ship_url)}
    urls = {"receive": receiving_url, "issue": issue_url, "ship": ship_url}
    by_action = {}
    for line in open_lines:
        by_action.setdefault(line.board.next_action, []).append(line)
    for key, label, icon, menu_title in _NEXT_ACTIONS:
        lines = open_lines if key == "ship" else by_action.get(key, [])
        if lines and allowed[key]:
            return {"key": key, "label": label, "icon": icon, "menu_title": menu_title, "url": urls[key], "lines": lines}
    return None


@register.simple_tag
def order_line_completed_label(line) -> str:
    """受注の詳細の明細の表の残の枡の「完了」の印の字 (N400b。本人の決め 11-11 08:4x・09:3x。RULES.md R25)。

    完了の印が偽なら空の字 (印を出さない)。完了の明細で、出荷済みの数 (画面の prefetch の写しの shipped_total) が数量より少なければ
    「完了 (出さない n)」(n = 数量 − 出荷済み = orders.services.unfilled_qty と同じ式)、数量以上なら「完了」。
    数は prefetch の写し (annotate_remaining の shipped_total と completed) から出し、明細ごとに問い合わせを増やさない。
    写しの無い明細 (shipped_total を持たない) だけ orders.services.unfilled_qty で読む。読むだけで、表に書かない。
    """
    if not line.completed:
        return ""
    shipped = getattr(line, "shipped_total", None)
    if shipped is None:
        from ..services import unfilled_qty

        unfilled = unfilled_qty(line)
    else:
        unfilled = line.qty - (shipped if isinstance(shipped, Decimal) else Decimal(str(shipped)))
    if unfilled > 0:
        return f"完了 (出さない {comma(unfilled)})"
    return "完了"
