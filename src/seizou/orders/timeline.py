"""受注の詳細の「流れ」の枠の出来事を集める、読むだけの module (決め 11-04 22:0x。N131)。表に書かない。

- 受注から入金までの出来事 (受信した注文・登録・指示書・実績・出荷・納品書・請求書・入金・メモ・取消) を、
  受注の明細から逆向きの関係 (related_name) だけで辿って集める。ほかの部品の model は import しない
  (メモだけは逆向きの関係が無いので、notes.services.note_for で読む)。
- 部品が入っていない会社では、その部品の出来事が無いだけで、枠は開く (関係は getattr で辿り、無ければ空)。
- 金額は Event.amount に分けて持ち、題の字に入れない。見せるかはテンプレートが seizou.hide_money で決める
  (受注の詳細の明細の表の単価と金額と同じ形 = access.can_see_money)。
- 並びは日の昇順。同じ日の中は仕事の順 (KINDS の順)、同じ種類の中は時刻 (日付だけの出来事は 0 時) と明細の番号の順。
  取消の受注は取り消した日時の記録が無いので、「受注を取消」は日時なし (None) で最後に置く。
- 誰が (who) と入口 (via) は、記録に人の列がある出来事と、受注の登録・受信した注文だけ (N163)。入口の字は受注と受信した注文の表示名と、ダッシュボードの INLETS の「seizou で直接」。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from django.apps import apps
from django.utils import timezone

from seizou.core.dashboard import INLETS
from seizou.core.templatetags.seizou_tags import comma
from seizou.core.views import model_url

from .board import BoardRow

#: 出来事の種類。並びの順 (同じ日の中の仕事の順) を兼ねる。
KINDS = (
    "inbox",
    "order",
    "workorder",
    "progress",
    "shipment",
    "delivery_note",
    "invoice",
    "payment",
    "note",
    "cancel",
)
_RANK = {kind: rank for rank, kind in enumerate(KINDS)}
_DIRECT = dict(INLETS)["direct"]  # 注文の入口「seizou で直接」(ダッシュボードの入口の図と取引先の一覧の列と同じ字)

#: 出来事の種類ごとの絵記号 (core の seizou_icons の PATHS にある名前だけ。流れの枠の点に出す。N163)。種類を足すときはここにも足す。
KIND_ICONS = {
    "inbox": "inbox",
    "order": "pencil",
    "workorder": "factory",
    "progress": "activity",
    "shipment": "truck",
    "delivery_note": "clipboard",
    "invoice": "file",
    "payment": "yen",
    "note": "book",
    "cancel": "x",
}

CANCELLED = "cancelled"


@dataclass(frozen=True)
class Event:
    """流れの 1 つの出来事。

    when は日時 (datetime) か日付 (date)、取消の受注だけ None。url はその記録の詳細の画面 (無ければ None)。
    line_no は明細の番号 (受注の全体の物は None)。amount は金額 (無ければ None。テンプレートが隠すかを決める)。
    who は誰が (記録に人の列がある出来事の人の名前か、受信した注文の取引先の名前)、via は注文の入口 (受注の登録と受信した注文だけ)。
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


def events_for(order) -> list[Event]:
    """受注の流れの出来事を、日の昇順に並べて返す (読むだけ)。"""
    events = [*_inbox_events(order), _registered(order)]
    delivery_notes = {}
    invoices = {}
    for line in _all(order, "lines"):
        for workorder in _all(line, "workorders"):
            events += _workorder_events(line, workorder)
        for shipment in _all(line, "shipments"):
            events.append(_shipment_event(line, shipment))
            note = getattr(shipment, "delivery_note", None)
            if note is not None:
                delivery_notes.setdefault(note.pk, note)
            for invoice_line in _all(shipment, "invoice_lines"):
                invoices.setdefault(invoice_line.invoice_id, invoice_line.invoice)
    events += [_delivery_note_event(note) for note in delivery_notes.values()]
    for invoice in invoices.values():
        events.append(_invoice_event(invoice))
        events += [_payment_event(invoice, payment) for payment in _all(invoice, "payments")]
    events += _note_events(order)
    if order.status == order.STATUS_CANCELLED:
        events.append(Event(None, "cancel", "受注を取消"))
    return sorted(events, key=_sort_key)


def _all(obj, name) -> list:
    """逆向きの関係の全部。部品が入っていなくて関係が無ければ空。"""
    manager = getattr(obj, name, None)
    return list(manager.all()) if manager is not None else []


def _url(obj) -> str | None:
    return model_url(obj.__class__, "detail", obj.pk)


def _qty(qty, line) -> str:
    return f"{comma(qty)} {line.unit}".strip()


def _cancelled(obj) -> str:
    return " (取消)" if getattr(obj, "status", "") == CANCELLED else ""


def _person(user) -> str:
    """記録の人の列の名前 (請求書の入金の行の「記録した人」と同じ字)。人が無ければ空。"""
    return "" if user is None else str(user)


def _inbox_inlet(inbox) -> str:
    """受信した注文の入口の字。seizou どうしなら「seizou で直接」、ほかは受信した注文の入口の表示名 (読み取り)。"""
    return _DIRECT if inbox.source == inbox.SOURCE_DIRECT else inbox.get_source_display()


def _inlet(order) -> str:
    """受注の入口の字。受信した注文から作った受注はその入口、ほかは受注の入口の表示名 (手入力・読み取り)。"""
    inbox = getattr(order, "inbox_message", None) if order.source == order.SOURCE_INBOX else None
    return _inbox_inlet(inbox) if inbox is not None else order.get_source_display()


def _inbox_events(order) -> list[Event]:
    # 受信した注文から作った受注だけ。connect が無いか、手入力の受注なら getattr が None を返す
    # (逆向きの 1 対 1 の「無い」は AttributeError の仲間)。
    inbox = getattr(order, "inbox_message", None)
    if inbox is None:
        return []
    url = _url(inbox)
    sender = inbox.partner.name if inbox.partner_id else inbox.sender_name
    events = [Event(inbox.received_at, "inbox", "注文が届いた", url, who=sender, via=_inbox_inlet(inbox))]
    if inbox.accepted_at is not None:
        events.append(Event(inbox.accepted_at, "inbox", "受信した注文を受け入れ", url))
    return events


def _registered(order) -> Event:
    """受注日の点。題は「受注を登録」。受信した注文から作り、受注日がその payload の相手の発注日と同じ受注だけ「受注日 (相手の発注日)」
    (候補 C08 の 8。C08-2 = a「発注日と同じ日のときだけ変える」)。受注日を編集で別の日に直した後に、違う日を指す字にしないため。
    手入力の受注と、発注日の読めない受信した注文から作った受注は「受注を登録」のまま。日付と種類と入口 (via) は変えない。
    """
    inbox = getattr(order, "inbox_message", None)
    same_day = inbox is not None and inbox.buyer_order_date() == order.order_date
    title = "受注日 (相手の発注日)" if same_day else "受注を登録"
    return Event(order.order_date, "order", title, via=_inlet(order))


def _workorder_events(line, workorder) -> list[Event]:
    url = _url(workorder)
    no = workorder.wo_no
    events = [
        Event(
            workorder.issued_on,
            "workorder",
            f"指示書 {no} を発行 ({_qty(workorder.qty, line)}){_cancelled(workorder)}",
            url,
            line.line_no,
        )
    ]
    for step in _all(workorder, "steps"):
        for record in _all(step, "progress_records"):
            title = f"実績 {step.process.name} {_qty(record.qty, line)} ({no})"
            events.append(Event(record.recorded_at, "progress", title, url, line.line_no, who=_person(record.recorded_by)))
    return events


def _shipment_event(line, shipment) -> Event:
    title = f"出荷 {shipment.shipment_no} ({_qty(shipment.qty, line)})"
    return Event(shipment.shipped_at, "shipment", title, _url(shipment), line.line_no)


def _delivery_note_event(note) -> Event:
    return Event(note.issued_on, "delivery_note", f"納品書 {note.note_no} を発行{_cancelled(note)}", _url(note))


def _invoice_event(invoice) -> Event:
    title = f"請求書 {invoice.invoice_no} を発行{_cancelled(invoice)}"
    return Event(invoice.issued_on, "invoice", title, _url(invoice), amount=invoice.total)


def _payment_event(invoice, payment) -> Event:
    title = f"請求書 {invoice.invoice_no} に入金{_cancelled(payment)}"
    return Event(payment.received_on, "payment", title, _url(payment), amount=payment.amount, who=_person(payment.recorded_by))


def _note_events(order) -> list[Event]:
    if not apps.is_installed("seizou.notes"):
        return []
    from seizou.notes import services as notes_services

    note = notes_services.note_for(order)
    if note.pk is None or notes_services.is_blank(note.body_html):
        return []
    return [Event(note.updated_at, "note", "メモを更新", who=_person(note.updated_by))]


def _sort_key(event: Event) -> tuple:
    if event.when is None:
        return (1, date.max, 0, time.min, 0)
    if isinstance(event.when, datetime):
        moment = timezone.localtime(event.when) if timezone.is_aware(event.when) else event.when
        day, clock = moment.date(), moment.time()
    else:
        day, clock = event.when, time.min
    return (0, day, _RANK[event.kind], clock, event.line_no or 0)


# --- 段の帯 (案 4-A。N148) --------------------------------------------------------------

#: 受注の明細 1 行の段の名前 (並びの順)。帯のボタンの名詞の表 (決め 11-04 17:4x) に合わせた字。字を変えるときはここだけ。
STAGES = (
    "受注",
    "指示",
    "着手",
    "完了",
    "出荷",
    "納品",
    "請求",
    "入金",
)


@dataclass(frozen=True)
class Stage:
    """帯の 1 段。no は 1 から。

    done は今の段より前で全部の明細が通った段、skipped は今の段より前で通っていない明細がある段 (C06)、
    current は今の段。先の段はどれも偽。
    """

    no: int
    name: str
    done: bool
    current: bool
    skipped: bool = False


@dataclass(frozen=True)
class StageBand:
    """受注の段の帯。current は受注の今の段 (明細のうち一番遅れている段。1 から)。

    summary は明細ごとに段が違うときの 1 文 (同じなら None)。
    """

    current: int
    stages: tuple[Stage, ...]
    summary: str | None = None

    @property
    def current_name(self) -> str:
        return STAGES[self.current - 1]


def reached_stages(line) -> frozenset[int]:
    """受注の明細 1 行が実際に通った段の集まり (1〜8 の数。読むだけ。C06)。

    受注 = いつも / 指示 = 取消でない指示書がある / 着手 = 実績が 1 件でもある取消でない指示書がある
    (BoardRow.started_count。指示書の進みの状態の着手済か完了。C27) / 完了 = 全部の指示書の最後の工程に完了の印の付いた実績がある
    (生産管理ボードの「進捗」の「完了」と同じ = BoardRow.all_done。PROG-1a) / 出荷 = 出荷がある /
    納品 = 出荷が取消でない納品書に入った / 請求 = 出荷が取消でない請求書に入った / 入金 = その請求書に取消でない入金がある。
    例: 指示書の無い明細の出荷は {1, 5}、指示書を発行して未着手のまま出荷は {1, 2, 5}。BoardRow は 1 回だけ作る。
    """
    board = BoardRow(line)
    reached = {1}
    if board.workorders:
        reached.add(2)
    if board.started_count:
        reached.add(3)
    if board.all_done:
        reached.add(4)
    shipments = _all(line, "shipments")
    if shipments:
        reached.add(5)
    if any(_is_live(getattr(s, "delivery_note", None)) for s in shipments):
        reached.add(6)
    invoices = {il.invoice_id: il.invoice for s in shipments for il in _all(s, "invoice_lines") if _is_live(il.invoice)}
    if invoices:
        reached.add(7)
    if any(_is_live(p) for invoice in invoices.values() for p in _all(invoice, "payments")):
        reached.add(8)
    return frozenset(reached)


def stage_of(line) -> int:
    """受注の明細 1 行の今の段 (1〜8) = 届いている一番先の段 (読むだけ。reached_stages の最大)。

    指示書が無くても出荷があれば「出荷」(在庫から出した明細)。段の読み方は reached_stages。
    """
    return max(reached_stages(line))


def stage_band(order) -> StageBand | None:
    """受注の段の帯。取消の受注は None (帯を出さない)。明細が無ければ「受注」の段。

    今の段 = 明細ごとの今の段の最小。今の段より前の段は、全部の明細が通った段だけ done (✓)、
    通っていない明細が 1 行でもある段は skipped (C06。行ごとの違いは帯の下の 1 文の外なので、✓ を控える側に倒す)。
    """
    if order.status == order.STATUS_CANCELLED:
        return None
    of_reached = [reached_stages(line) for line in _all(order, "lines")]
    of_lines = [max(reached) for reached in of_reached]
    current = min(of_lines, default=1)
    stages = []
    for no, name in enumerate(STAGES, start=1):
        done = no < current and all(no in reached for reached in of_reached)
        stages.append(Stage(no, name, done, no == current, skipped=no < current and not done))
    return StageBand(current, tuple(stages), _summary(of_lines))


def _is_live(obj) -> bool:
    """記録があり、取消でないか。"""
    return obj is not None and getattr(obj, "status", "") != CANCELLED


def _summary(of_lines) -> str | None:
    """明細ごとに段が違うときの 1 文「明細 3 行のうち、出荷 1 行・着手 2 行」(進んだ段から)。同じなら None。"""
    if len(set(of_lines)) < 2:
        return None
    counts = Counter(of_lines)
    parts = [f"{STAGES[no - 1]} {counts[no]} 行" for no in sorted(counts, reverse=True)]
    return f"明細 {len(of_lines)} 行のうち、{'・'.join(parts)}"


# --- 取消の確かめの「この受注に付いている物」(confirm_print-01。初見テスト R5 の候補 C59) ----------------------

#: 付いている物の群の種類と名前。並びの順 (受注の流れの順) を兼ねる。
LINKED_KINDS = (
    ("workorder", "指示書"),
    ("shipment", "出荷"),
    ("delivery_note", "納品書"),
    ("invoice", "請求書"),
)


@dataclass(frozen=True)
class LinkedDoc:
    """付いている物の 1 件。no は番号の字、url は詳細の画面 (部署でその部品を開けない人には None)。

    has_progress は実績が 1 件でも入っている指示書か (指示書だけ。ほかは偽)。
    """

    no: str
    url: str | None = None
    has_progress: bool = False


@dataclass(frozen=True)
class LinkedGroup:
    """付いている物の 1 つの群 (指示書・出荷・納品書・請求書)。docs は番号の昇順で、空の群は作らない。"""

    kind: str
    label: str
    docs: tuple[LinkedDoc, ...]

    @property
    def has_progress(self) -> bool:
        """実績の入った指示書が群の中にあるか (テンプレートが「実績の入った指示書は取り消せません。」を出すかを決める)。"""
        return any(doc.has_progress for doc in self.docs)


def linked_for(order, user) -> list[LinkedGroup]:
    """受注の取消の確かめに並べる、この受注から作った物の群 (読むだけ。表に書かない)。

    辿り方は events_for と同じ逆向きの関係だけ (受注の明細 → 指示書・出荷 → 納品書・請求書)。納品書と請求書は pk で重ねない。
    並べるのは取消でない物だけ (出荷は状態の列が無いので全部)。群は指示書 → 出荷 → 納品書 → 請求書、群の中は番号の昇順。
    全部が 0 件なら空の list。明細と付いている物は _linked_lines が関係ごとに 1 回だけ読む。
    """
    workorders, shipments, notes, invoices = {}, {}, {}, {}
    for line in _linked_lines(order):
        for workorder in _all(line, "workorders"):
            workorders[workorder.pk] = workorder
        for shipment in _all(line, "shipments"):
            shipments[shipment.pk] = shipment
            note = getattr(shipment, "delivery_note", None)
            if note is not None:
                notes.setdefault(note.pk, note)
            for invoice_line in _all(shipment, "invoice_lines"):
                invoices.setdefault(invoice_line.invoice_id, invoice_line.invoice)
    found = {
        "workorder": [_linked(w, w.wo_no, user, _has_progress(w)) for w in workorders.values() if _is_live(w)],
        "shipment": [_linked(s, s.shipment_no, user) for s in shipments.values()],
        "delivery_note": [_linked(n, n.note_no, user) for n in notes.values() if _is_live(n)],
        "invoice": [_linked(i, i.invoice_no, user) for i in invoices.values() if _is_live(i)],
    }
    return [
        LinkedGroup(kind, label, tuple(sorted(found[kind], key=lambda doc: doc.no)))
        for kind, label in LINKED_KINDS
        if found[kind]
    ]


def _linked_lines(order) -> list:
    """受注の明細を、付いている物と一緒に読む (prefetch。読むだけ)。

    関係ごとに 1 回だけ読み、明細と記録の数で問い合わせを増やさない (受注を取り消す画面の問い合わせの上限 =
    tests/test_core_screen_queries.py の LIMITS の orders:order_cancel)。出荷は納品書と、請求の明細は請求書と一緒に読む。
    辿る関係は linked_for と同じ。部品が入っていなくて関係が無ければ、その関係は読まない (_all と同じ扱い)。
    """
    from django.db.models import Prefetch  # 頭の import を足すと、ほかの候補が行番号で引く行がずれるので、ここで読む

    manager = getattr(order, "lines", None)
    if manager is None:
        return []
    lookups = []
    workorder_path = _reachable(manager.model, ("workorders", "steps", "progress_records"))
    if workorder_path:
        lookups.append(workorder_path)
    shipment = _related_model(manager.model, "shipments")
    if shipment is not None:
        lookups.append(Prefetch("shipments", queryset=shipment._default_manager.select_related("delivery_note")))
        invoice_line = _related_model(shipment, "invoice_lines")
        if invoice_line is not None:
            queryset = invoice_line._default_manager.select_related("invoice")
            lookups.append(Prefetch("shipments__invoice_lines", queryset=queryset))
    return list(manager.prefetch_related(*lookups))


def _related_model(model, name):
    """model の関係 name の相手の model。部品が入っていなくて関係が無ければ None。"""
    from django.core.exceptions import FieldDoesNotExist

    try:
        return model._meta.get_field(name).related_model
    except FieldDoesNotExist:
        return None


def _reachable(model, names) -> str:
    """model から names の関係を順に辿れる所までの prefetch の字 (例 workorders__steps)。1 つ目から無ければ空。"""
    path = []
    for name in names:
        model = _related_model(model, name)
        if model is None:
            break
        path.append(name)
    return "__".join(path)


def _linked(obj, no, user, has_progress=False) -> LinkedDoc:
    return LinkedDoc(no, _linked_url(obj, user), has_progress)


def _linked_url(obj, user) -> str | None:
    """詳細の画面の URL。部署でその部品を開けない人には None (link にせず番号の字だけを出す)。

    部署で開けない画面へ移るボタンを出さない決め (決め 11-04 12:5x。core の tag can_write と同じ判定 = access.can_open。記録は書かない)。
    """
    from seizou.core import access  # 頭の import を足すと、ほかの候補が行番号で引く行がずれるので、ここで読む

    if not access.can_open(user, obj._meta.app_label, record=False):
        return None
    return _url(obj)


def _has_progress(workorder) -> bool:
    """指示書の工程に実績が 1 件でも入っているか (_workorder_events と同じ辿り方。progress の部品が無ければ偽)。"""
    return any(_all(step, "progress_records") for step in _all(workorder, "steps"))
