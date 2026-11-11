"""検査の層 (ポカよけ。検査の層の設計の決め)。

画面から入った値が services に渡る前に、その会社のポカよけを順に通す。
入力の土台 (BaseFormView) と受信箱の受け入れの画面が run を呼ぶ。services は呼ばない。

- 止める (mode = "stop"): 検査が文を返したら CheckViolation を投げる。赤の知らせ。保存しない。
- 知らせる (mode = "warn"): 文を CheckWarning として一覧で返す。黄の知らせと「このまま保存」。
- 通す (mode = "off"): 呼ばない。

標準のポカよけ (R01・R04・R06・R07・R24・R26・R28) は builtin にあり、mode は会社の設定の値をそのまま使う。
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


def run(action: str, data: dict, user) -> list[CheckWarning]:
    """action に登録された検査を順に通す。

    止めるの検査が文を返したら CheckViolation。知らせるの文は一覧で返す。
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
            # 止めるの赤の知らせは「保存できません。」を頭に付ける (文の型。決め 11-05 09:5x。開発の記録 docs/nights/20261105/N156.md)。知らせるの黄は検査の文のまま。
            raise CheckViolation(check.code, "保存できません。" + message)
        warnings.append(CheckWarning(check.code, message))
    return warnings
