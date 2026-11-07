"""権限の判定の 1 か所 (02 の 7 節)。画面の土台・左のメニュー・横断検索・帳票の口・accounts の部品がここを呼ぶ。

3 つの判定と、それぞれの状態 (会社の設定 access_control。seizou.core.conf.access_control):
  部署 (department): 一覧と入力と盤の画面を、部署の「開ける部品」で開けるか。
  編集 (write): 書く action (検査の層と同じ名前。例 "shipping.ship") を、編集の権限の種類で通すか。
  金額 (money): 単価と金額の値 (MONEY_FIELDS) を見せるか。金額が主な書面と登録 (MONEY_ACTIONS) を通すか。

  | 状態       | 部署                               | 編集                         | 金額                                         |
  | off        | 全部の画面が開き、メニューも全部   | 書く action を見ない         | 単価と金額をそのまま出し、断らない           |
  | log (既定) | 開ける。部署の外の画面を開いたら   | 通す。権限の無い書く action  | そのまま出して通す。金額の無い人が金額のある |
  |            | 記録に 1 行。メニューは全部        | を記録に 1 行                | 画面と断る操作に当たったら記録に 1 行       |
  | on         | 部署の外の一覧と入力の画面は 403。 | 権限の無い書く action を赤の | 単価と金額の値を出さず、金額に関わる操作   |
  |            | メニューは部署の画面と全員の画面   | 知らせで断る                 | (MONEY_ACTIONS) を断り、入口も出さない       |

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
    "connections": "取引先との接続",
    "manuals": "マニュアル",
    "orders": "受注",
    "workorders": "指示書",
    "shipping": "出荷と納品書",
    "billing": "請求書",
    "purchasing": "発注",
}
#: 種類の中身の注記 (アカウントの入力の画面の check の字に添える)
KIND_NOTES = {
    "items": "部品・標準の工程・図面・部品の取引先と単価表",
    "orders": "受信した注文の受け入れ・条件付の受け入れ・保留・断り・注文書の読み取りを含む",
    "workorders": "生産計画の置き直しを含む",
    "processes": "工程と会社の休日",
    "purchasing": "仕入明細を作る・送る・取り消す・相手の提案を受け入れるを含む",
    "billing": "受けた仕入明細に答えるを含む",
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
    "items": (
        "masters.create_item",
        "masters.update_item",
        "masters.import_items",
        "masters.restore_drawing_version",
        # 部品の取引先 (C98)。金額が主な登録 (MONEY_ACTIONS) には入れない (決め C98-6 = a。金額を見ない人も選び間違えた行を消せる)。
        "masters.add_partner_item",
        "masters.update_partner_item",
        "masters.delete_partner_item",
        # 単価の行 (C98)。追加と編集と削除は金額が主な登録 (MONEY_ACTIONS) にも入れる (決め C98-5・C98-6 = a)。
        "masters.add_item_price",
        "masters.update_item_price",
        "masters.delete_item_price",
        # 部品の標準の工程の並びを直す 3 つ (C112。決め C19-3)。部品マスターの編集の権限で守る
        "masters.add_item_processes",
        "masters.move_item_process",
        "masters.remove_item_process",
    ),
    "partners": ("masters.create_partner", "masters.update_partner", "masters.import_partners"),
    "processes": ("masters.create_process", "masters.update_process", "masters.create_holiday", "masters.delete_holiday"),
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
        "manuals.add_image",
        "manuals.remove_image",
    ),
    "orders": (
        "orders.create_order",
        "orders.update_order",
        "orders.cancel_order",
        "orders.mark_no_bill",
        "orders.clear_no_bill",
        "connect.accept",
        "connect.reject",
        "connect.accept_with_conditions",
        "connect.hold",
        "intake.extract_order",
        "intake.accept_extracted",
    ),
    "workorders": ("workorders.issue_workorder", "workorders.cancel_workorder", "workorders.plan_step"),
    "shipping": (
        "shipping.ship",
        # 一括出荷 (N124。注文残の盤で選んだ明細をまとめて出荷)
        "shipping.ship_lines",
        "shipping.update_shipment",
        "shipping.create_delivery_note",
        "shipping.cancel_delivery_note",
        # 出荷の知らせを相手に送り直す口 (N55。本体が起こす前に入れる前提)
        "connect.send_shipping_notice",
    ),
    "billing": (
        "billing.create_invoice",
        "billing.cancel_invoice",
        # 請求書の作り直し (同じ締め期間の請求書を取り消して 1 枚に作る。R23)
        "billing.reissue_invoice",
        # 入金 (N56。本体が起こす前に入れる前提)
        "billing.record_payment",
        "billing.cancel_payment",
        "billing.record_outside_payment",
        # 受けた仕入明細に答える (C111。金額を確かめて得意先へ返すのは、請求書と入金を扱う人の仕事)
        "connect.answer_self_invoice",
    ),
    "purchasing": (
        "purchasing.create_purchase_order",
        "purchasing.cancel_purchase_order",
        "connect.send_order",
        "purchasing.resend_purchase_order",  # 発注の送り直し (N96。本体が起こす前に入れる前提)
        "connect.send_acceptance",  # 検収を相手に送り直す口 (N54。本体が起こす前に入れる前提)
        "purchasing.receive",  # 受入 (N42。協力企業管理の 2 つ目の画面)
        "purchasing.change_purchase_order",  # 送った発注の変更 (C96。相手が出荷し終えるまで変更を送る)
        "purchasing.create_self_invoice",  # 仕入明細を作る (C111。受入をまとめて相手へ送る書類)
        "connect.send_self_invoice",  # 仕入明細を相手に送り直す口 (C111)
        "purchasing.cancel_self_invoice",  # 相手が答える前の仕入明細の取消 (C111。取消も相手へ送る)
        "purchasing.accept_self_invoice_proposals",  # 相手の提案を受け入れて修正仕入明細を送る (C111)
    ),
}
#: 書く action → 編集の権限の種類 (action は検査の層と同じ名前 = <service の module の app>.<関数の名前>)
ACTION_KINDS = {action: kind for kind, actions in _KIND_ACTIONS.items() for action in actions}
#: 権限が要らない action (見る・探す・現場の記録)
# 権限の要らない action (見る・探す・現場の記録)。在庫の動き (N42) は現場の記録と同じ扱い
FREE_ACTIONS = frozenset(
    {
        # フィードバック (N59。鍵の無い会社でも誰でも伝えられる = 本体が起こす前に入れる前提)
        "feedback.create_feedback",
        "feedback.update_feedback",
        "progress.record_progress",
        # 実績を直す・消す (C103。権限は実績を入れるのと同じ = PROG-1d)
        "progress.update_progress",
        "progress.delete_progress",
        "attachments.add_attachment",
        "attachments.remove_attachment",
        "stock.record_finished",
        "stock.record_issued",
        "stock.adjust",
        "receiving.create_receiving",  # 入荷 (支給品と預かり品の現場の記録。在庫の動きと同じ扱い)
        # 書式つきのメモ (N114。現場の記録と同じ扱い = ログインした人なら誰でも書ける。本体が起こす前に入れる前提)
        "notes.save_note",
        "notes.add_image",
        # 会話 (C104。受注と発注の 1 件ごとの相手とのやり取り。メモ・添付と同じく編集の権限を問わない = 決め C104-1 (11-07 06:26)。書く画面は connect の部署の門だけが効く)
        "connect.post_chat_message",
        # 人ごとの表示の設定 (N167。本人の画面の見た目だけを変える = ログインした人なら誰でも変えられる)
        "core.set_user_preference",
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
        "feedback.connect_github",  # 「GitHub とつなぐ」(N185)。世話役 = アカウント管理の権限。services も同じ判定で 2 重に断る
        # 会社の情報の変更 (N80。設定の画面の「会社の情報」。門は設定の変更と同じ)
        "core.update_company",
        # 部署を画面から直す口 (N57。本体が起こす前に入れる前提)
        "accounts.create_department",
        "accounts.update_department",
        "accounts.delete_department",
    }
)
#: 金額に関わる操作 (金額のある記録を作る・変える・取り消す) → 断りの字の (書面, 操作)。
#: 金額の閲覧が無い人には、金額の判定が on のとき require_write が断り、write_allowed が偽を返して入口 (can_write の tag・
#: 一覧の「新規登録」・詳細の「編集」) も出さない。
#: 訳: 金額を見られない人は、金額に関わる操作 (金額のある記録を作る・変える・取り消す) をしない。入口も出さない。
#: 何のため: 登録されている金額を見られない人が操作すると、金額を確かめられないまま記録を作るか変えることになり、誤りの元になるため。
#: 出所 = 作者の決め 11-07 21:0x (本人「金額を見れない人は受注処理できなくない？見れないんだからミスの元でしょう」
#: 「金額見れない人は金額に関わる操作はできないよねやっぱり」「登録されている金額見れないんだから危険だよ」)。
#: 変えてよい条件 = 金額を見ずに行っても金額の誤りが起きないと示せる操作 (例: 決め C98-6 の、部品の取引先の行を消す操作) だけを外してよい。
#: 数や物の情報だけを扱う操作 (部品の登録と編集・出荷と一括出荷と出荷の直し・発注の送り直し) は入れない (決め 11-07 21:1x の 6)。
#: 訳: 現場の人が金額の閲覧なしで出荷と受入を続けられるようにするため。金額はこの操作では決まらず、単価から後で決まる。出所 = 作者の決め 11-07 21:1x。
#: 変えてよい条件 = この操作の入力に金額の欄が加わったとき (そのときは決め 4 に従って MONEY_ACTIONS に足す)。
#: 受入 (purchasing.receive) も入れない (決め 11-07 21:2x の 8。訳は purchasing.services.receive の docstring)。
MONEY_ACTIONS = {
    "billing.create_invoice": ("請求書", "作れません"),
    "billing.record_payment": ("入金", "記録できません"),
    # 請求書の外で受け取った入金 (決め C107-3c) は、今の入金の記録と同じ扱い (受け取った金額を入れることは、金額を決めることと同じ重さ)
    "billing.record_outside_payment": ("入金", "記録できません"),
    "orders.create_order": ("受注", "新規登録できません"),
    # 作り直しは請求書を作る操作の別の入口なので、請求書を作るのと同じ扱い
    "billing.reissue_invoice": ("請求書", "作れません"),
    # 単価の行は単価だけを中身に持つ登録 (決め C98-5・C98-6 = a。金額を見られない人が単価を決める形にしない。消すのも単価を決める操作)。
    # 部品の取引先の行の削除は入れない (金額を見ない人も選び間違えた取引先の行を消すため)。
    "masters.add_item_price": ("単価表", "単価の行を足せません"),
    "masters.update_item_price": ("単価表", "単価の行を直せません"),
    "masters.delete_item_price": ("単価表", "単価の行を消せません"),
    # 仕入明細 (C111) は金額が主な書類なので、請求書を作るのと同じ扱い
    "purchasing.create_self_invoice": ("仕入明細", "作れません"),
    "connect.answer_self_invoice": ("仕入明細", "答えられません"),
    # 提案を受け入れるのは仕入明細の金額を直す登録
    "purchasing.accept_self_invoice_proposals": ("仕入明細", "直せません"),
    # --- 決め 4 の続き (11-07 21:1x〜21:2x): 金額のある記録を変える・取り消す操作と、受注と発注を作る操作 ---
    # 受注の変更は単価の欄を出さなくても数の変更で金額が変わる。取消と「請求しない」の印の付け外しは請求の金額が変わる
    "orders.update_order": ("受注", "変更できません"),
    "orders.cancel_order": ("受注", "取り消せません"),
    "orders.mark_no_bill": ("受注", "「請求しない」の印を付けられません"),
    "orders.clear_no_bill": ("受注", "「請求しない」の印を外せません"),
    # 受信した注文の受け入れ (条件付を含む) と、読み取った注文書の受け入れは、受注を作る操作 (受注の新規登録と同じ重さ)
    "connect.accept": ("受信した注文", "受け入れられません"),
    "connect.accept_with_conditions": ("受信した注文", "受け入れられません"),
    "intake.accept_extracted": ("読み取った注文書", "受注にできません"),
    # 請求書と入金の取消は、請求と入金の金額を戻す操作
    "billing.cancel_invoice": ("請求書", "取り消せません"),
    "billing.cancel_payment": ("入金", "取り消せません"),
    # 発注の新規登録 (明細の単価の欄が必須 = 見つけ GR-07。入口ごと出さない)・変更 (数の変更で金額が変わる)・取消
    "purchasing.create_purchase_order": ("発注", "新規登録できません"),
    "purchasing.change_purchase_order": ("発注", "変更できません"),
    "purchasing.cancel_purchase_order": ("発注", "取り消せません"),
    # 納品書を作る (決め 7)。訳: 納品書は単価と金額を刷って得意先へ渡す書類なので、金額を確かめられない人が作ると、誤った金額の書類が外へ出るため。
    # 出所 = 作者の決め 11-07 21:2x (決め 4 の範囲を聞き直した答え)。変えてよい条件 = 納品書から単価と金額を外す会社の設定ができたとき。
    "shipping.create_delivery_note": ("納品書", "作れません"),
    # 納品書の取消も同じ扱い (作者の決め 11-07 23:2x「推奨で」= 作ると取消を同じ扱いにする。入口は tag で隠れていたが、直に開くと保存が通っていた)
    "shipping.cancel_delivery_note": ("納品書", "取り消せません"),
}
# 3 つの表のどれにも無い action は権限が要らない扱い (止めない)。新しい書く画面の足し忘れは
# tests/test_core_access.py の test_every_form_action_is_classified が落とす。

# ---------------------------------------------------------------- 画面の分け方

#: アカウント管理の権限で開く部品 (3 状態の外)
ACCOUNT_APPS = ("accounts",)
#: 部署に関係なく開く部品 (検索・使い方・ダッシュボード・設定は core の画面。notes = 書式つきのメモ。N114)
ALWAYS_OPEN_APPS = ("core", "assistant", "manuals", "attachments", "feedback", "notes")

# ---------------------------------------------------------------- 金額の値

#: 金額の項目の名前 (core の 1 か所の集まり)
MONEY_FIELDS = frozenset(
    {"unit_price", "amount", "standard_price", "total", "subtotal", "tax", "balance", "paid_total", "reply_unit_price"}
)
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


def can_open(user, app, *, record=True, screen=None) -> bool:
    """部署で app の画面を開けるか。accounts はアカウント管理の権限で決める (3 状態の外)。

    screen は開いた画面の名前 (部署の門 screen_denied が渡す。403 の文と同じ名前)。log の記録の行に画面の名前を出す (決め 11-04 14:4x の 3)。
    無いとき・空のとき・部品の名前と同じときは、記録の行は今の字のまま (「<部品>」の画面を開いた)。
    """
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
            # 画面の名前と部品の名前が違うときだけ、部品の名前を括弧で添える (403 の文と同じ選び方。例「受信した注文」の画面 = 「受注管理」の部品)
            what = f"「{screen}」の画面 (「{label}」の部品) を開いた" if screen and screen != label else f"「{label}」の画面を開いた"
            log_only("department", user, what, f"「{label}」を開ける部署")
        return True
    return False


def require_open(user, app, *, what) -> None:
    """部署で app を開けない人を、部署の判定 (mode("department")) に従って断る。画面の門 (screen_denied) を通らずに書く口が使う。

    on は AccessDenied (code「権限」。呼ぶ側が今の断りの形にする)、log は記録に 1 行 (what = 何をしたか) で通す、off は何もしない。
    開けるかの判定は can_open と同じ (アカウント管理の部品はここでは見ない = require_write の ACCOUNT_ACTIONS がいつも断る)。
    訳: AI に質問の窓の「承認」から、部署の外の部品 (例 出荷・受注・指示書) に書かせない。
    何のため: 同じ操作を画面で行うと、入力の画面の部署の門が 403 で止める。承認は画面を通らずに services へ書くので、
    門を通さないと、部署で分けた会社 (部署の判定が on) で、部署の外の人が AI に頼んで出荷や受注を登録できてしまう。
    出所 = Claude Security の走査の候補 11-07 (承認が部署の門を通らずに書く)。
    変えてよい条件 = 部署の判定が入力の画面でも部署を見なくなったとき (そのときは承認もそろえて外す)。
    """
    if app in ACCOUNT_APPS or app in ALWAYS_OPEN_APPS:
        return
    if mode("department") == "off" or not _signed_in(user) or is_unrestricted(user):
        return
    departments = departments_of(user)
    if not departments or any(app in (d.apps or ()) for d in departments):
        return
    label = app_label(app)
    names = "・".join(d.name for d in departments)
    # 字は部署の 403 の画面 (screen_denied) と同じ組み立て (「<部品>」の … です。あなたの部署: …。… 頼んでください。)
    message = f"「{label}」の部品の操作です。あなたの部署: {names}。この操作を使う必要があるときは、アカウント管理の担当者に頼んでください。"
    _follow("department", user, AccessDenied(message), what, f"「{label}」を開ける部署")


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
    3) 金額に関わる操作 (MONEY_ACTIONS) は、金額の閲覧が無い人を mode("money") に従って断る。
       訳: 金額を見られない人は、金額に関わる操作 (金額のある記録を作る・変える・取り消す) をしない。
       何のため: 登録されている金額を見られない人が操作すると、金額を確かめられないまま記録を作るか変えることになり、誤りの元になるため。
       出所 = 作者の決め 11-07 21:0x (本人「金額を見れない人は受注処理できなくない？見れないんだからミスの元でしょう」
       「金額見れない人は金額に関わる操作はできないよねやっぱり」「登録されている金額見れないんだから危険だよ」)。
       変えてよい条件 = 金額を見ずに行っても金額の誤りが起きないと示せる操作 (例: 決め C98-6 の、部品の取引先の行を消す操作) だけを外してよい。
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


def _screen_title(request) -> str:
    """要求の画面の名前 (土台の画面の screen_name)。引けなければ空の字。core の views を import しない (request.resolver_match の view の class から読む)。"""
    match = getattr(request, "resolver_match", None)
    view_class = getattr(getattr(match, "func", None), "view_class", None)
    return str(getattr(view_class, "screen_name", "") or "")


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
    # 画面の名前は 403 の文と、log のときの記録の行の両方に使う (決め 11-04 14:4x の 3)
    screen = _screen_title(request)
    if by_department and not can_open(user, app, screen=screen):
        names = "・".join(d.name for d in departments_of(user)) or "なし"
        label = app_label(app)
        # 画面の名前と部品の名前が違うときだけ、部品の名前を括弧で添える (例「受信した注文」の画面 = 「受注管理」の部品)
        where = f"「{screen}」の画面です (「{label}」の部品)" if screen and screen != label else f"「{label}」の画面です"
        return denied_response(
            request,
            DENIED_HEADING,
            f"{where}。あなたの部署: {names}。"
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
    """require_write の 1) 2) 3) を、例外でなく真偽で返す (記録は書かない)。入口 (ボタンと link) を出すかの判定。

    偽になりうるのは、編集の判定が on のとき (種類の権限が無い) と、金額の判定が on のとき (MONEY_ACTIONS の操作で金額の閲覧が無い) だけ。
    訳: 金額を見られない人は、金額に関わる操作 (金額のある記録を作る・変える・取り消す) をしない。入口も出さない。
    何のため: 登録されている金額を見られない人が操作すると、金額を確かめられないまま記録を作るか変えることになり、誤りの元になるため。
    出所 = 作者の決め 11-07 21:0x (本人「金額を見れない人は受注処理できなくない？見れないんだからミスの元でしょう」
    「金額見れない人は金額に関わる操作はできないよねやっぱり」「登録されている金額見れないんだから危険だよ」)。
    変えてよい条件 = 金額を見ずに行っても金額の誤りが起きないと示せる操作 (例: 決め C98-6 の、部品の取引先の行を消す操作) だけを外してよい。
    """
    if action in ACCOUNT_ACTIONS:
        return can_manage_accounts(user)
    if action in MONEY_ACTIONS and mode("money") == "on" and not has_money(user):
        return False
    kind = ACTION_KINDS.get(action)
    if kind is None or mode("write") != "on":
        return True
    return is_unrestricted(user) or kind in permissions_of(user)


def can_write_model(user, model, verb) -> bool:
    """model の新規登録 (create) か編集 (update) の入口のボタンを出すか。

    URL の名前 <app>:<model>_<verb> の画面の service から action を作り、write_allowed で決める (編集の権限と、金額に関わる操作の金額の閲覧)。
    URL が無い・view に service が無い・action が ACTION_KINDS にも MONEY_ACTIONS にも無いときは真 (隠さない)。
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
