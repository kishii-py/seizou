"""知らせの型 (01 の 10.2)。

- RuleError: 親。画面の土台はこれで捕まえ、赤の知らせを出す。入力の値は消さない。
- RuleViolation: 中心の決まり (RULES.md の止める段) に当たった。
- CheckViolation: ポカよけ (検査の層) の「止める」に当たった。

ポカよけの「知らせる」は例外でなく、seizou.core.checks.run が返す CheckWarning の一覧。
承認の段 (理由を書いて通す) は無い (決め 11-02 現物優先)。
"""


class RuleError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class RuleViolation(RuleError):
    """中心の決まりに当たった。通す道は無い。"""


class CheckViolation(RuleError):
    """ポカよけの「止める」に当たった。会社の設定で止めると決めた物だけ。"""
