"""権限の判定の 1 か所 (02 の 7 節)。画面の土台・左のメニュー・横断検索・帳票の口・accounts の部品がここを呼ぶ。

3 つの判定と、それぞれの状態 (会社の設定 access_control。seizou.core.conf.access_control):
  部署 (department): 一覧と入力と盤の画面を、部署の「開ける部品」で開けるか。
  編集 (write): 書く action (検査の層と同じ名前。例 "shipping.ship") を、編集の権限の種類で通すか。
  金額 (money): 単価と金額の値 (MONEY_FIELDS) を見せるか。金額が主な書面と登録 (MONEY_ACTIONS) を通すか。

  | 状態       | 部署                               | 編集                         | 金額                                         |
  | off        | 全部の画面が開き、メニューも全部   | 書く action を見ない         | 単価と金額をそのまま出し、断らない           |
  | log (既定) | 開ける。部署の外の画面を開いたら   | 通す。権限の無い書く action  | そのまま出して通す。金額の無い人が金額のある |
  |            | 記録に 1 行。メニューは全部        | を記録に 1 行                | 画面と断る操作に当たったら記録に 1 行       |
  | on         | 部署の外の一覧と入力の画面は 403。 | 権限の無い書く action を赤の | 単価と金額の値を「—」にし、金額が主な書面と |
  |            | メニューは部署の画面と全員の画面   | 知らせで断る                 | 登録を断る                                   |

- 3 つとも、システム管理者 (is_superuser) とフルコントロールは通る。
- Access の行が無い利用者 (と削除済みの行) は、部署なし・権限なし・金額なし。部署が 1 つも無い人は全部の画面が開く。
- アカウント管理の画面 (ACCOUNT_APPS) と ACCOUNT_ACTIONS は 3 状態の外で、いつも効く (off でも無い人は 403)。
- 記録だけの 1 行は logging の "seizou.access" の WARNING。同じ要求の同じ判定と同じ対象は 1 行だけ
  (利用者の物の __dict__ に控える。利用者の物は要求ごとに作り直されるので、要求をまたいで残らない)。
- accounts の部品が入っていない置き場では、3 つの判定は全部 off と同じ。

core は accounts に頼らない (accounts の表は apps.get_model で関数の中で引く)。画面の土台の module (core の views)
は import しない (土台が access を import するため)。読むだけで、表に書かない。
"""

from __future__ import annotations

import inspect
import logging

from django.apps import apps
from django.db import DatabaseError
from django.shortcuts import render
from django.urls import NoReverseMatch, Resolver404, resolve, reverse

from seizou.core import conf
from seizou.core.exceptions import RuleError

logger = logging.getLogger("seizou.access")

KINDS = ("department", "write", "money")
MODES = ("off", "log", "on")
#: 判定の名前 (記録の 1 行に出す字)
KIND_TITLES = {"department": "部署", "write": "編集の権限", "money": "金額の閲覧"}

# ---------------------------------------------------------------- 編集の権限の名前 (画面・services・種・テストが読む)

#: 種類の名前 → 表示名
KIND_LABELS = {
    "items": "部品マスター",
    "partners": "取引先",
    "processes": "工程",
    "connections": "つながり",
    "manuals": "マニュアル",
    "orders": "受注",
    "workorders": "指示書",
    "shipping": "出荷と納品書",
    "billing": "請求書",
    "purchasing": "発注",
}
#: 種類の中身の注記 (アカウントの入力の画面の check の字に添える)
KIND_NOTES = {
    "items": "部品・標準の工程・図面・取引先ごとの品番",
    "orders": "届いた注文の受け入れと断り・注文書の読み取りを含む",
    "workorders": "生産計画の置き直しを含む",
}
#: まとめの名前 → (表示名, 中の種類)
GROUPS = {
    "master_edit": ("マスター編集", ("items", "partners", "processes", "connections", "manuals")),
    "slip_edit": ("伝票編集", ("orders", "workorders", "shipping", "billing", "purchasing")),
}
#: 権限の名前の並び (画面の並び = まとめ 2 つ → 種類 10)
PERMISSION_NAMES = (*GROUPS, *KIND_LABELS)

# ---------------------------------------------------------------- action の分け方

_KIND_ACTIONS = {
    "items": ("masters.create_item", "masters.update_item", "masters.import_items", "masters.restore_drawing_version"),
    "partners": ("masters.create_partner", "masters.update_partner", "masters.import_partners"),
    "processes": ("masters.create_process", "masters.update_process"),
    "connections": ("connect.register_connection", "connect.update_connection"),
    "manuals": (
        "manuals.create_folder",
        "manuals.create_manual",
        "manuals.update_manual",
        "manuals.save_as_version",
        "manuals.restore_version",
        "manuals.update_folder",
        "manuals.move_folder",
        "manuals.protect_folder",
        "manuals.delete_folder",
        "manuals.move_manual",
        "manuals.protect_manual",
        "manuals.delete_manual",
    ),
    "orders": (
        "orders.create_order",
        "orders.update_order",
        "orders.cancel_order",
        "connect.accept",
        "connect.reject",
        "intake.extract_order",
        "intake.accept_extracted",
    ),
    "workorders": ("workorders.issue_workorder", "workorders.cancel_workorder", "workorders.plan_step"),
    "shipping": ("shipping.ship", "shipping.update_shipment", "shipping.create_delivery_note", "shipping.cancel_delivery_note"),
    "billing": (
        "billing.create_invoice",
        "billing.cancel_invoice",
        # 入金 (N56。本体が起こす前に入れる前提)
        "billing.record_payment",
        "billing.cancel_payment",
    ),
    "purchasing": (
        "purchasing.create_purchase_order",
        "purchasing.cancel_purchase_order",
        "connect.send_order",
        "connect.send_acceptance",  # 検収を相手に送り直す口 (N54。本体が起こす前に入れる前提)
        "purchasing.receive",  # 受入 (N42。協力企業管理の 2 つ目の画面)
    ),
}
#: 書く action → 編集の権限の種類 (action は検査の層と同じ名前 = <service の module の app>.<関数の名前>)
ACTION_KINDS = {action: kind for kind, actions in _KIND_ACTIONS.items() for action in actions}
#: 権限が要らない action (見る・探す・現場の記録)
# 権限の要らない action (見る・探す・現場の記録)。在庫の動き (N42) は現場の記録と同じ扱い
FREE_ACTIONS = frozenset(
    {
        "progress.record_progress",
        "attachments.add_attachment",
        "attachments.remove_attachment",
        "stock.record_finished",
        "stock.record_issued",
        "stock.adjust",
    }
)
#: アカウント管理の門で守る action (3 状態の外)。core.set_setting は設定の画面からの変更 (N48) の action で、
#: 画面の門 (can_manage_accounts) と同じ判定で 2 重に断る (依頼書 N48 の迷った事 2 = 本体の決め)
ACCOUNT_ACTIONS = frozenset(
    {
        "accounts.issue_account",
        "accounts.update_account",
        "accounts.delete_account",
        "core.set_setting",
        # 部署を画面から直す口 (N57。本体が起こす前に入れる前提)
        "accounts.create_department",
        "accounts.update_department",
        "accounts.delete_department",
    }
)
#: 金額が主な登録 → (書面, 操作)
MONEY_ACTIONS = {
    "billing.create_invoice": ("請求書", "作れません"),
    "billing.record_payment": ("入金", "記録できません"),
    "orders.create_order": ("受注", "新規登録できません"),
}
# 3 つの表のどれにも無い action は権限が要らない扱い (止めない)。新しい書く画面の足し忘れは
# tests/test_core_access.py の test_every_form_action_is_classified が落とす。

# ---------------------------------------------------------------- 画面の分け方

#: アカウント管理の権限で開く部品 (3 状態の外)
ACCOUNT_APPS = ("accounts",)
#: 部署に関係なく開く部品 (検索・使い方・ダッシュボード・設定は core の画面)
ALWAYS_OPEN_APPS = ("core", "assistant", "manuals", "attachments")

# ---------------------------------------------------------------- 金額の値

#: 金額の項目の名前 (core の 1 か所の集まり)
MONEY_FIELDS = frozenset({"unit_price", "amount", "standard_price", "total", "subtotal", "tax", "balance", "paid_total"})
#: 金額の閲覧が無い人に見せる字
MASK = "—"

CONTACT = "要るときはアカウント管理の担当者に頼んでください。"
ACCOUNT_MESSAGE = "アカウント管理の権限がありません。" + CONTACT
DENIED_HEADING = "この画面は開けません"
DOCUMENT_HEADING = "この書面は開けません"

_ROW = "_seizou_access"
_LOGGED = "_seizou_access_logged"


class AccessDenied(RuleError):
    """権限の判定で断った。入力の土台が赤の知らせにする (code は字「権限」)。"""

    CODE = "権限"

    def __init__(self, message: str):
        super().__init__(self.CODE, message)


# ---------------------------------------------------------------- 状態と行


def mode(kind) -> str:
    """判定 kind (department / write / money) の状態 (off / log / on)。accounts の部品が無ければ off。"""
    if kind not in KINDS:
        raise KeyError(f"知らない判定: {kind}")
    if not apps.is_installed("seizou.accounts"):
        return "off"
    return conf.access_control(kind)


def _signed_in(user) -> bool:
    return user is not None and bool(getattr(user, "is_authenticated", False))


def access_for(user):
    """利用者の Access の行 (部署を prefetch)。無い・削除済み・未ログイン・表が読めないときは None。

    1 要求で 1 回だけ読み、利用者の物に控える (None も控える)。
    """
    if not _signed_in(user):
        return None
    store = user.__dict__
    if _ROW in store:
        return store[_ROW]
    row = None
    try:
        model = apps.get_model("accounts", "Access")
    except LookupError:
        model = None
    if model is not None:
        try:
            row = (
                model._default_manager.prefetch_related("departments")
                .filter(user_id=user.pk, deleted_at__isnull=True)
                .first()
            )
        except DatabaseError:
            row = None
    store[_ROW] = row
    return row


def is_unrestricted(user) -> bool:
    """システム管理者 (is_superuser) か、フルコントロール。"""
    if not _signed_in(user):
        return False
    if getattr(user, "is_superuser", False):
        return True
    row = access_for(user)
    return bool(row is not None and row.full_control)


def expand(names) -> frozenset:
    """権限の名前の並びを、まとめを中の種類に開いた種類の集まりにする (知らない名前は飛ばす)。"""
    kinds: set[str] = set()
    for name in names or ():
        if name in GROUPS:
            kinds.update(GROUPS[name][1])
        elif name in KIND_LABELS:
            kinds.add(name)
    return frozenset(kinds)


def permission_label(name: str) -> str:
    """権限の名前 (まとめか種類) の表示名。"""
    if name in GROUPS:
        return GROUPS[name][0]
    return KIND_LABELS.get(name, name)


def group_of(kind: str) -> str:
    """種類を含むまとめの名前。"""
    return next(group for group, (_label, kinds) in GROUPS.items() if kind in kinds)


def permissions_of(user) -> frozenset:
    """行の編集の権限を開いた種類の集まり (行が無ければ空)。"""
    row = access_for(user)
    if row is None:
        return frozenset()
    return expand(row.write_permissions)


def departments_of(user) -> list:
    """生きている行の部署の並び (行が無ければ空)。"""
    row = access_for(user)
    if row is None:
        return []
    return list(row.departments.all())


def has_money(user) -> bool:
    """金額の閲覧を持つか (状態は見ない)。システム管理者・フルコントロール・行の see_money。"""
    if is_unrestricted(user):
        return True
    row = access_for(user)
    return bool(row is not None and row.see_money)


def can_manage_accounts(user) -> bool:
    """アカウント管理を使えるか (3 状態の外。いつも効く)。"""
    if not _signed_in(user):
        return False
    if getattr(user, "is_superuser", False):
        return True
    row = access_for(user)
    return bool(row is not None and (row.manage_accounts or row.full_control))


def app_label(app: str) -> str:
    """部品の表示名 (例「受注」)。知らない部品は名前のまま。"""
    try:
        return str(apps.get_app_config(app).verbose_name)
    except LookupError:
        return app


# ---------------------------------------------------------------- 記録だけ


def log_only(kind, user, what, need=None) -> None:
    """記録だけの 1 行。「記録だけ (<判定の名前>): <ログイン ID> が <何を> (<要る物>)」。同じ要求の同じ判定と同じ対象は 1 行。"""
    if _signed_in(user):
        logged = user.__dict__.setdefault(_LOGGED, set())
        key = (kind, what)
        if key in logged:
            return
        logged.add(key)
        login_id = user.get_username()
    else:
        login_id = "(ログインしていない人)"
    logger.warning("記録だけ (%s): %s が %s (%s)", KIND_TITLES[kind], login_id, what, need or KIND_TITLES[kind])


def _follow(kind, user, exc: AccessDenied, what: str, need: str) -> None:
    """判定に当たったときに、状態に従う (on は断る、log は記録に 1 行、off は何もしない)。"""
    state = mode(kind)
    if state == "on":
        raise exc
    if state == "log":
        log_only(kind, user, what, need)


# ---------------------------------------------------------------- 3 つの判定


def can_open(user, app, *, record=True) -> bool:
    """部署で app の画面を開けるか。accounts はアカウント管理の権限で決める (3 状態の外)。"""
    if app in ACCOUNT_APPS:
        return can_manage_accounts(user)
    if app in ALWAYS_OPEN_APPS:
        return True
    state = mode("department")
    if state == "off" or not _signed_in(user) or is_unrestricted(user):
        return True
    departments = departments_of(user)
    if not departments or any(app in (d.apps or ()) for d in departments):
        return True
    if state == "log":
        if record:
            label = app_label(app)
            log_only("department", user, f"「{label}」の画面を開いた", f"「{label}」を開ける部署")
        return True
    return False


def write_message(kind: str) -> str:
    label = KIND_LABELS[kind]
    group = GROUPS[group_of(kind)][0]
    return f"{label}の編集の権限がありません。「{label}」か、それを含む「{group}」の権限が要ります。{CONTACT}"


def money_message(document: str, operation: str) -> str:
    return f"{document}は金額が主な内容のため、金額の閲覧の権限が無い方は{operation}。{CONTACT}"


def require_write(user, action) -> None:
    """書く action を通すか。断るときは AccessDenied (入力の土台が赤の知らせにする)。

    1) アカウント管理の action は、アカウント管理を使えない人をいつも断る。
    2) 編集の権限の種類がある action は、種類の権限が無い人を mode("write") に従って断る。
    3) 金額が主な登録は、金額の閲覧が無い人を mode("money") に従って断る。
    """
    if action in ACCOUNT_ACTIONS and not can_manage_accounts(user):
        raise AccessDenied(ACCOUNT_MESSAGE)
    kind = ACTION_KINDS.get(action)
    if kind is not None and mode("write") != "off" and not is_unrestricted(user) and kind not in permissions_of(user):
        group = GROUPS[group_of(kind)][0]
        _follow(
            "write",
            user,
            AccessDenied(write_message(kind)),
            f"{action} を保存しようとした",
            f"「{KIND_LABELS[kind]}」か「{group}」の権限",
        )
    money = MONEY_ACTIONS.get(action)
    if money is not None and mode("money") != "off" and not has_money(user):
        _follow("money", user, AccessDenied(money_message(*money)), f"{action} を保存しようとした", "金額の閲覧")


def can_see_money(user, *, record=True, what="単価と金額を見た") -> bool:
    """単価と金額を見せるか。log では見せて (record が真なら) 記録に 1 行、on では見せない。"""
    state = mode("money")
    if state == "off" or has_money(user):
        return True
    if state == "log":
        if record:
            log_only("money", user, what, "金額の閲覧")
        return True
    return False


def hides_money(user) -> bool:
    """テンプレートが金額の枡を隠す印 (seizou.hide_money)。未ログインは偽。記録は書かない。"""
    return _signed_in(user) and not can_see_money(user, record=False)


def is_money_name(name: str) -> bool:
    """項目の名前 (__ で区切った最後) が金額の項目か。"""
    return str(name).rsplit("__", 1)[-1] in MONEY_FIELDS


# ---------------------------------------------------------------- 画面と帳票の門


def denied_response(request, heading: str, text: str):
    """403 の画面 (core/access_denied.html)。"""
    return render(request, "core/access_denied.html", {"heading": heading, "text": text}, status=403)


def screen_denied(request, app, *, by_department):
    """画面の門。開けないときは 403 の応答、開けるときは None。未ログインは None (ログインへ移すのは LoginRequiredMixin)。"""
    user = getattr(request, "user", None)
    if not _signed_in(user):
        return None
    if app in ACCOUNT_APPS:
        if can_manage_accounts(user):
            return None
        return denied_response(
            request,
            DENIED_HEADING,
            "「アカウント管理」の画面です。アカウント管理の権限が要ります。" + CONTACT,
        )
    if by_department and not can_open(user, app):
        names = "・".join(d.name for d in departments_of(user)) or "なし"
        return denied_response(
            request,
            DENIED_HEADING,
            f"「{app_label(app)}」の画面です。あなたの部署: {names}。"
            "この画面を使う必要があるときは、アカウント管理の担当者に頼んでください。",
        )
    return None


def refuse_money_document(request, document):
    """金額が主な書面の門。金額の閲覧が無い人 (on) には 403 の応答、開けるときは None。"""
    if can_see_money(request.user, what=f"{document}を開いた"):
        return None
    return denied_response(
        request,
        DOCUMENT_HEADING,
        f"{document}は金額が主な内容のため、金額の閲覧の権限が無い方は開けません。{CONTACT}",
    )


# ---------------------------------------------------------------- 土台が使う小さな道具


def takes_hide_money(form_class) -> bool:
    """form が hide_money= を受けるか (__init__ が hide_money の引数を名指ししている物だけ)。"""
    if form_class is None:
        return False
    try:
        parameters = inspect.signature(form_class.__init__).parameters
    except (TypeError, ValueError):
        return False
    return "hide_money" in parameters


def without_totals(rows):
    """最近の受注の並びの total を None にした写し (None はそのまま)。"""
    if rows is None:
        return None
    return [{**row, "total": None} for row in rows]


def visible_menu(user, items):
    """左のメニューの入口のうち、開ける物だけ。未ログインはアカウント管理の入口だけを外す。記録は書かない。"""
    if not _signed_in(user):
        return [item for item in items if item["app"] not in ACCOUNT_APPS]
    return [item for item in items if can_open(user, item["app"], record=False)]


def action_name(service) -> str:
    """service の関数から action の名前を作る (入力の土台の get_action と同じ形)。"""
    module = service.__module__.split(".")
    app = module[-2] if len(module) >= 2 else module[-1]
    return f"{app}.{service.__name__}"


def write_allowed(user, action) -> bool:
    """require_write の 1) と 2) を、例外でなく真偽で返す (記録は書かない。編集の判定が on のときだけ偽になりうる)。"""
    if action in ACCOUNT_ACTIONS:
        return can_manage_accounts(user)
    kind = ACTION_KINDS.get(action)
    if kind is None or mode("write") != "on":
        return True
    return is_unrestricted(user) or kind in permissions_of(user)


def can_write_model(user, model, verb) -> bool:
    """model の新規登録 (create) か編集 (update) の入口のボタンを出すか。

    URL の名前 <app>:<model>_<verb> の画面の service から action を作り、write_allowed で決める。
    URL が無い・view に service が無い・action の種類が ACTION_KINDS に無いときは真 (隠さない)。
    """
    meta = model._meta
    args = (1,) if verb == "update" else ()
    try:
        match = resolve(reverse(f"{meta.app_label}:{meta.model_name}_{verb}", args=args))
    except (NoReverseMatch, Resolver404):
        return True
    service = getattr(getattr(match.func, "view_class", None), "service", None)
    if service is None:
        return True
    return write_allowed(user, action_name(service))
