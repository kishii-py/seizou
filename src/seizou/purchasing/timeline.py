"""発注の詳細の「流れ」の枠の出来事を集める、読むだけの module (N139。受注の流れ orders.timeline と同じ形)。表に書かない。

- 発注から受入と検収までの出来事 (登録・注文を送った・注文回答・出荷の知らせ・受入・検収・取消) を、
  発注と明細からの逆向きの関係 (related_name) だけで辿って集める。ほかの部品の model は import しない
  (送った注文は connect の Outbox の outbox_messages、出荷の知らせは connect の ShippingNotice の shipping_notices)。
- connect が入っていない会社では、送った注文と出荷の知らせの出来事が無いだけで、枠は開く (関係は getattr で辿り、無ければ空)。
- 検収は発注の側では受入の列 (Receipt.acceptance_sent_at = 相手の受け口が受け取った日時) で、送れていない物は出来事にしない。
- 金額は Event.amount に分けて持ち、題の字に入れない。見せるかはテンプレートが seizou.hide_money で決める
  (発注の詳細の明細の表の単価と金額と同じ形 = access.can_see_money)。
- 並びは日の昇順。同じ日の中は仕事の順 (KINDS の順)、同じ種類の中は時刻 (日付だけの出来事は 0 時) と明細の番号の順。
  取消の発注は取り消した日時の記録が無いので、「発注を取消」は日時なし (None) で最後に置く。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from django.utils import timezone

from seizou.core.templatetags.seizou_tags import comma
from seizou.core.views import model_url

#: 出来事の種類。並びの順 (同じ日の中の仕事の順) を兼ねる。
KINDS = (
    "order",
    "sent",
    "reply",
    "shipping_notice",
    "receipt",
    "acceptance",
    "cancel",
)
_RANK = {kind: rank for rank, kind in enumerate(KINDS)}

#: 出来事の種類ごとの絵記号 (core の seizou_icons の PATHS にある名前だけ。流れの枠の点に出す。N163)。種類を足すときはここにも足す。
KIND_ICONS = {
    "order": "pencil",
    "sent": "external",
    "reply": "inbox",
    "shipping_notice": "truck",
    "receipt": "box",
    "acceptance": "check",
    "cancel": "x",
}

#: 送った注文の相手の返事のうち、相手の受け口が受け取った印 (connect.services._deliver の 200 の字)。
RECEIVED = "受け取った"


@dataclass(frozen=True)
class Event:
    """流れの 1 つの出来事。

    when は日時 (datetime) か日付 (date)、取消の発注だけ None。url はその記録の詳細の画面 (無ければ None)。
    line_no は明細の番号 (発注の全体の物は None)。amount は金額 (無ければ None。テンプレートが隠すかを決める)。
    who は誰が (記録に人の列がある出来事の人の名前。発注では受入だけ)、via は注文の入口 (発注の流れでは使わない。受注の流れと同じ形)。
    """

    when: date | datetime | None
    kind: str
    title: str
    url: str | None = None
    line_no: int | None = None
    amount: Decimal | None = None
    who: str = ""
    via: str = ""

    @property
    def has_time(self) -> bool:
        """時刻まである出来事か (テンプレートが日時の字の形を選ぶ)。"""
        return isinstance(self.when, datetime)

    @property
    def icon(self) -> str:
        """流れの枠の点の絵記号の名前 (KIND_ICONS)。"""
        return KIND_ICONS[self.kind]


def events_for(purchase_order) -> list[Event]:
    """発注の流れの出来事を、日の昇順に並べて返す (読むだけ)。"""
    events = [Event(purchase_order.order_date, "order", "発注を登録")]
    events += [_sent_event(outbox) for outbox in _all(purchase_order, "outbox_messages")]
    if purchase_order.replied_at is not None:
        events.append(_reply_event(purchase_order))
    for line in _all(purchase_order, "lines"):
        events += [_notice_event(line, notice) for notice in _all(line, "shipping_notices")]
        for receipt in _all(line, "receipts"):
            events += _receipt_events(line, receipt)
    if purchase_order.status == purchase_order.STATUS_CANCELLED:
        events.append(Event(None, "cancel", "発注を取消"))
    return sorted(events, key=_sort_key)


def _all(obj, name) -> list:
    """逆向きの関係の全部。部品が入っていなくて関係が無ければ空。"""
    manager = getattr(obj, name, None)
    return list(manager.all()) if manager is not None else []


def _url(obj) -> str | None:
    return model_url(obj.__class__, "detail", obj.pk)


def _qty(qty, line) -> str:
    return f"{comma(qty)} {line.unit}".strip()


def _person(user) -> str:
    """記録の人の列の名前 (受注の流れの orders.timeline と同じ字)。人が無ければ空。"""
    return "" if user is None else str(user)


def _sent_event(outbox) -> Event:
    # 送った注文の詳細の画面は無い (送った記録は発注の詳細のタブ)。model_url は None を返す。
    if outbox.response == RECEIVED:
        title = "注文を送った (相手が受け取った)"
    else:
        title = f"注文を送れなかった ({outbox.response})"
    return Event(outbox.sent_at, "sent", title, _url(outbox))


def _reply_event(purchase_order) -> Event:
    # 注文回答の結果の列は無い。断りは理由が要る (connect の断るの form と services) ので、断りの理由か状態で見分ける。
    # 断られた後に取り消した発注 (取消) と、断られた後に全部を受け入れた発注 (納品された) も断りとして出す。
    rejected = purchase_order.status in (purchase_order.STATUS_REJECTED, purchase_order.STATUS_CANCELLED)
    if rejected or purchase_order.reply_note:
        title = f"相手に断られた (理由: {purchase_order.reply_note})" if purchase_order.reply_note else "相手に断られた"
    else:
        title = "相手が受け入れた"
    return Event(purchase_order.replied_at, "reply", title)


def _notice_event(line, notice) -> Event:
    # 出荷の知らせの詳細の画面は無い (発注の詳細の明細の表の「出荷の知らせ」の列)。model_url は None を返す。
    title = f"出荷の知らせ {notice.shipment_no} ({_qty(notice.qty, line)})"
    return Event(notice.shipped_at, "shipping_notice", title, _url(notice), line.line_no)


def _receipt_events(line, receipt) -> list[Event]:
    url = _url(receipt)
    qty = _qty(receipt.qty, line)
    who = _person(receipt.received_by)
    events = [Event(receipt.received_on, "receipt", f"受入 ({qty})", url, line.line_no, receipt.amount, who=who)]
    if receipt.acceptance_sent_at is not None:
        events.append(Event(receipt.acceptance_sent_at, "acceptance", f"検収を送った (受入 {qty})", url, line.line_no))
    return events


def _sort_key(event: Event) -> tuple:
    if event.when is None:
        return (1, date.max, 0, time.min, 0)
    if isinstance(event.when, datetime):
        moment = timezone.localtime(event.when) if timezone.is_aware(event.when) else event.when
        day, clock = moment.date(), moment.time()
    else:
        day, clock = event.when, time.min
    return (0, day, _RANK[event.kind], clock, event.line_no or 0)


# --- 段の帯 (案 4-A の発注。N151。受注の段の帯 = orders.timeline の STAGES・Stage・StageBand と同じ形) ---------------

#: 発注の段の名前 (並びの順)。流れの出来事の種類 (KINDS の order・sent・reply・shipping_notice・receipt・acceptance) に当たる名詞。
#: 字を変えるときはここだけ。
STAGES = (
    "発注",
    "送付",
    "回答",
    "出荷の知らせ",
    "受入",
    "検収",
)


@dataclass(frozen=True)
class Stage:
    """帯の 1 段。no は 1 から (受注の帯 = orders.timeline.Stage と同じ意味。C06)。

    done は今の段より前で全部の明細が通った段、skipped は今の段より前で通っていない明細がある段
    (発注では、先の段でも届かない検収を含む = _acceptance_unreachable)、current は今の段。
    """

    no: int
    name: str
    done: bool
    current: bool
    skipped: bool = False


@dataclass(frozen=True)
class StageBand:
    """発注の段の帯。current は発注の今の段 (明細のうち一番遅れている段。1 から)。

    summary は明細ごとに段が違うときの 1 文 (同じなら None)。
    """

    current: int
    stages: tuple[Stage, ...]
    summary: str | None = None

    @property
    def current_name(self) -> str:
        return STAGES[self.current - 1]


#: 検収の段の番号 (届かない検収の横線を付ける段)。
ACCEPTANCE = STAGES.index("検収") + 1


def reached_stages(line) -> frozenset[int]:
    """発注の明細 1 行が実際に通った段の集まり (1〜6 の数。読むだけ。C06)。

    送付と回答は発注の全体で決まる段 (_order_reached)、出荷の知らせ・受入・検収は明細ごとの段 (_line_reached)。
    例: 相手へ送らずに頼んで注文回答の写しがあり、出荷の知らせと受入がある明細は {1, 3, 4, 5}。
    """
    purchase_order = line.purchase_order
    return _order_reached(purchase_order, _all(purchase_order, "outbox_messages")) | _line_reached(line)


def stage_of(line) -> int:
    """発注の明細 1 行の今の段 (1〜6) = 届いている一番先の段 (読むだけ。reached_stages の最大)。

    送付と回答が無くても受入があれば「受入」(相手へ送らずに頼んだ発注)。
    """
    return max(reached_stages(line))


def stage_band(purchase_order) -> StageBand | None:
    """発注の段の帯。取消の発注は None (帯を出さない)。発注の全体の段と送った注文の行は 1 回だけ読む。

    今の段 = 明細ごとの今の段の最小 (明細が無ければ発注の全体の段)。今の段より前の段は、全部の明細が通った段だけ done (✓)、
    通っていない明細が 1 行でもある段は skipped (受注の帯と同じ決まり。C06-4 = b)。
    先の段の検収は、届かない検収 (_acceptance_unreachable) なら skipped。
    """
    if purchase_order.status == purchase_order.STATUS_CANCELLED:
        return None
    outbox = _all(purchase_order, "outbox_messages")
    head = _order_reached(purchase_order, outbox)
    of_reached = [head | _line_reached(line) for line in _all(purchase_order, "lines")]
    of_lines = [max(reached) for reached in of_reached]
    current = min(of_lines, default=max(head))
    passed = of_reached or [head]
    unreachable = _acceptance_unreachable(purchase_order, outbox)
    stages = []
    for no, name in enumerate(STAGES, start=1):
        done = no < current and all(no in reached for reached in passed)
        skipped = (no < current and not done) or (no == ACCEPTANCE and no > current and unreachable)
        stages.append(Stage(no, name, done, no == current, skipped=skipped))
    return StageBand(current, tuple(stages), _summary(of_lines))


def _order_reached(purchase_order, outbox) -> frozenset[int]:
    """発注の全体で通った段の集まり (1〜3 の数)。outbox は送った注文の行の並び (呼ぶ側が 1 回だけ読む)。

    発注 = いつも。回答 = 注文回答の日時がある (相手が受け入れたも断られたも回答)。送付 = 状態が「送った」か、
    送った注文を相手の受け口が受け取った (送れなかった物は数えない)。注文回答の後は状態が変わるので、送った注文の行で読む
    (流れの枠の「注文を送った (相手が受け取った)」と同じ読み)。
    """
    reached = {1}
    if purchase_order.status == purchase_order.STATUS_SENT or any(row.response == RECEIVED for row in outbox):
        reached.add(2)
    if purchase_order.replied_at is not None:
        reached.add(3)
    return frozenset(reached)


def _line_reached(line) -> frozenset[int]:
    """明細ごとに通った段の集まり (4〜6 の数。どれも無ければ空 = 発注の全体の段に任せる)。

    出荷の知らせ = 相手の出荷の知らせがある。受入 = 受入がある。検収 = 相手に届いた検収がある (届いていない物は数えない)。
    """
    receipts = _all(line, "receipts")
    reached = set()
    if _all(line, "shipping_notices"):
        reached.add(4)
    if receipts:
        reached.add(5)
    if any(receipt.acceptance_sent_at is not None for receipt in receipts):
        reached.add(6)
    return frozenset(reached)


def _acceptance_unreachable(purchase_order, outbox) -> bool:
    """届かない検収か (C06-4 = b のこの版の読み)。送った注文の行が 1 つも無く、状態が「下書き」でない発注で真。

    受入の後に検収を送るのは送った注文の行がある発注だけ (purchasing.services の receive と _is_sent_order と同じ読み)。
    作った後に送る道は「下書き」の間だけ出る「再送」なので、「下書き」を過ぎて送った注文の行が無い発注は検収に届かない。
    「下書き」のままの発注は後で送る見込みが残るので偽 (早すぎる印を避ける)。送れなかった行だけがある発注も、
    送り直しで届く見込みがあるので偽。
    """
    return not outbox and purchase_order.status != purchase_order.STATUS_DRAFT


def _summary(of_lines) -> str | None:
    """明細ごとに段が違うときの 1 文「明細 3 行のうち、受入 1 行・回答 2 行」(進んだ段から)。同じなら None。"""
    if len(set(of_lines)) < 2:
        return None
    counts = Counter(of_lines)
    parts = [f"{STAGES[no - 1]} {counts[no]} 行" for no in sorted(counts, reverse=True)]
    return f"明細 {len(of_lines)} 行のうち、{'・'.join(parts)}"
