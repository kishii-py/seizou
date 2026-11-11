"""検査の層 (ポカよけ。検査の層の設計の決め)。

画面から入った値が services に渡る前に、その会社のポカよけを順に通す。
入力の土台 (BaseFormView) と受信箱の受け入れの画面が run を呼ぶ。services は呼ばない。

- 止める (mode = "stop"): 検査が文を返したら CheckViolation を投げる。赤の知らせ。保存しない。
- 知らせる (mode = "warn"): 文を CheckWarning として一覧で返す。黄の知らせと「このまま保存」(削除の画面は「このまま削除」、
  取消の画面は「このまま取り消す」。字は入力の土台 core.views.BaseFormView.check_words が選ぶ)。
- 通す (mode = "off"): 呼ばない。

標準のポカよけ (R01・R04・R06・R07・R24・R26・R27・R28・R29・R30・R31・R33) は builtin にあり、mode は会社の設定の値をそのまま使う。
会社のポカよけは、会社のプロジェクトの checks.py が register を呼んで足す (core の道具だけで書く)。
検査は読むだけで、表を書かない (書くのは services)。
"""

from __future__ import annotations

from dataclasses import dataclass

from seizou.core.exceptions import CheckViolation

MODES = ("stop", "warn", "off")


@dataclass(frozen=True)
class CheckWarning:
    code: str
    message: str


class Check:
    """1 つのポカよけ。

    name: 英語の識別子 (会社の検査は会社が付ける)。
    action: 見る操作 (例 "shipping.ship")。
    mode: "stop" / "warn" / "off"。setting_key があれば会社の設定の値を使う。
    run(data, user) -> str | None: 返す文が知らせ。None なら通る。
    """

    name: str = ""
    action: str = ""
    mode: str = "warn"
    setting_key: str | None = None

    def current_mode(self) -> str:
        if self.setting_key:
            from seizou.core.conf import setting

            value = setting(self.setting_key)
        else:
            value = self.mode
        if value not in MODES:
            raise ValueError(f"検査 {self.name} の mode が不正: {value!r}")
        return value

    def run(self, data: dict, user) -> str | None:  # pragma: no cover - 子が実装する
        raise NotImplementedError

    @property
    def code(self) -> str:
        return self.name


_REGISTRY: dict[str, list[Check]] = {}


def register(check: Check) -> Check:
    """検査を登録する。同じ action の同じ name は 1 つだけ (2 回目は置き換える)。"""
    if not check.name or not check.action:
        raise ValueError("検査には name と action が要る")
    checks = _REGISTRY.setdefault(check.action, [])
    for i, existing in enumerate(checks):
        if existing.name == check.name:
            checks[i] = check
            return check
    checks.append(check)
    return check


def registered(action: str) -> list[Check]:
    return list(_REGISTRY.get(action, []))


def setting_key_for(name: str) -> str | None:
    """name の検査の会社の設定の鍵 (例 R01 → over_shipment)。全部の action の登録を見て、最初に当たった物。無ければ None。

    同じ name を複数の action に同じ鍵で登録する (R01 は 1 件の出荷・出荷の編集・一括出荷) ので、最初の 1 つでよい。
    決まりの番号の「?」の説明 (core.views.rule_help) が、この会社の今の設定の行を出すために読む。読むだけ。
    """
    for action_checks in _REGISTRY.values():
        for check in action_checks:
            if check.name == name and check.setting_key:
                return check.setting_key
    return None


def clear(action: str | None = None) -> None:
    """試験のための後始末。"""
    if action is None:
        _REGISTRY.clear()
    else:
        _REGISTRY.pop(action, None)


#: 止めるの赤の知らせの頭の既定 (保存の画面。run の stop_head を渡さない呼び方)
STOP_HEAD = "保存できません。"


def run(action: str, data: dict, user, *, stop_head: str = STOP_HEAD) -> list[CheckWarning]:
    """action に登録された検査を順に通す。

    止めるの検査が文を返したら CheckViolation (文の頭に stop_head を付ける)。知らせるの文は一覧で返す。
    stop_head = 止めるの赤の知らせの頭の字。入力の土台 (BaseFormView.check_words) が、削除の画面は「削除できません。」、
    取消の画面は「取り消せません。」を渡し、保存の画面は「保存できません。」を渡す。渡さない呼び方 (AI の提案の承認 = core.mcp_tools) は「保存できません。」のまま。
    """
    warnings: list[CheckWarning] = []
    for check in registered(action):
        mode = check.current_mode()
        if mode == "off":
            continue
        message = check.run(data, user)
        if not message:
            continue
        if mode == "stop":
            # 止めるの赤の知らせは頭の字 (stop_head。既定は「保存できません。」。削除の画面は「削除できません。」、取消の画面は「取り消せません。」) を付ける
            # (文の型。決め 11-05 09:5x。開発の記録 docs/nights/20261105/N156.md。削除と取消の字は本体の決め 11-11 15:0x)。知らせるの黄は検査の文のまま。
            raise CheckViolation(check.code, stop_head + message)
        warnings.append(CheckWarning(check.code, message))
    return warnings


def preview(action: str, data: dict, user) -> list[tuple[str, CheckWarning]]:
    """action に登録された検査が今の data で返す文を、断らずに集める (読むだけ。表を書かず、run は変えない)。

    登録の順に見て、設定が通す (off) の検査は呼ばない。文を返した検査を (mode, CheckWarning(code, 文)) の並びで返す。mode は "stop" か "warn"。
    止めるの文にも頭 (stop_head) を付けず、CheckViolation を出さない。検査の run が例外を出したとき (数の無い data で呼ぶので、
    会社の checks.py の検査が qty を前提に誤りを出すなど) と、会社の設定の mode が不正なときは、その検査を飛ばす (呼ぶ画面を落とさない)。
    出荷の新規登録の数量の欄の下の字 (shipping.forms.ShipmentForm) と、受注の明細の候補の口の for=shipping
    (orders.views.OrderLineSelectView) が、数を入れる前の明細で呼ぶ。

    訳: 保存の前に、保存の時に検査の層が出す知らせ (数に依らない物) を画面に先に出すため。止めるか知らせるかの判定は run と同じ設定を読む。
    出所 = 精査 Q1 (2026-11-10) の番号 1 と、本体の決め 11-11 14:2x (開発の記録 docs/nights/20261111/N421.md)。変えてよい条件 = 検査の層の mode の作りが変わったとき。
    """
    found: list[tuple[str, CheckWarning]] = []
    for check in registered(action):
        try:
            mode = check.current_mode()
            if mode == "off":
                continue
            message = check.run(data, user)
        except Exception:  # 読むだけの先回り。誤りは保存の時の run が今のとおり出す
            continue
        if message:
            found.append((mode, CheckWarning(check.code, message)))
    return found
