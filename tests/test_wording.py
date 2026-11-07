"""言葉の決まり (CLAUDE.md の「言葉」): 「外注」と書かない。「協力企業」と書く。

本人の決め 11-03「協力企業にしよう」「外注という言葉の使用を禁止しよう」。
取引先の種類の表示名・画面の字・README・skill・作り物の種・テストの全部が対象。
見ない物: migrations (履歴は書き換えない)・docs/ (記録)・CLAUDE.md (決まりそのものが語を名指す)・この試験。

「届いた注文」と書かない。「受信した注文」と書く (本人の決め 11-04 14:4x「受信した注文がいいかな」)。
「釦」と書かない。「ボタン」と書く (本人の決め 11-05「釦という字はかなり分かりにくい ボタンにして」)。
見る物は src/・tests/・examples/・README.md・docs/。見ない物は migrations と docs/nights/ (記録) とこの試験。

できない操作の赤の知らせの文は「<操作>できません。」の形の語 (REFUSAL_HEADS の表。例「保存できません。」「取り消せません。」) で始める
(本人の決め 11-05 09:5x「できない操作の画面で文言を統一した方がいい」。N156)。知らせの描き方は core/_notices.html (小窓などの帯) と core/_toasts.html (右上の知らせ) の 2 か所で、字の並び (1 文目の太字・理由・末尾の番号) は同じ。
"""

import ast
from pathlib import Path

import pytest
from django.template.loader import render_to_string

from seizou.core import checks
from seizou.core.checks import CheckWarning
from seizou.core.exceptions import CheckViolation, RuleViolation
from seizou.core.views import Notice

ROOT = Path(__file__).resolve().parent.parent
BANNED = "外注"
SCAN = ("src/seizou", "README.md", "plugin", "examples", "tests", "scripts", "SECURITY.md", "CONTRIBUTING.md", "GOVERNANCE.md", "AGENTS.md", "RULES.md")
SUFFIXES = {".py", ".html", ".js", ".md", ".yaml", ".yml", ".csv", ".txt", ".toml", ".json"}
SKIP_PARTS = {"migrations", "__pycache__", ".tools", "node_modules"}


def _files():
    for base in SCAN:
        path = ROOT / base
        if path.is_file():
            yield path
            continue
        for file in sorted(path.rglob("*")):
            if file.is_file() and file.suffix in SUFFIXES and not (SKIP_PARTS & set(file.parts)) and file.name != "test_wording.py":
                yield file


def test_banned_word_is_not_used():
    hits = []
    for file in _files():
        text = file.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), 1):
            if BANNED in line:
                hits.append(f"{file.relative_to(ROOT)}:{number}")
    assert not hits, "「外注」は書かない (「協力企業」と書く): " + ", ".join(hits)


RECEIVED_OLD = "届いた注文"
RECEIVED_SCAN = ("src", "README.md", "examples", "tests", "docs")


def _received_files():
    for base in RECEIVED_SCAN:
        path = ROOT / base
        if path.is_file():
            yield path
            continue
        for file in sorted(path.rglob("*")):
            parts = file.relative_to(ROOT).parts
            if parts[:2] == ("docs", "nights") or SKIP_PARTS & set(parts) or file.name == "test_wording.py":
                continue
            if file.is_file() and file.suffix in SUFFIXES:
                yield file


def test_received_orders_word_is_not_used():
    hits = []
    for file in _received_files():
        text = file.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), 1):
            if RECEIVED_OLD in line:
                hits.append(f"{file.relative_to(ROOT)}:{number}")
    assert not hits, "「届いた注文」は書かない (「受信した注文」と書く): " + ", ".join(hits)


BUTTON_OLD = "釦"


def test_button_word_is_not_used():
    """「釦」と書かない。「ボタン」と書く (本人の決め 11-05「釦という字はかなり分かりにくい ボタンにして」)。"""
    hits = []
    for file in _files():
        text = file.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), 1):
            if BUTTON_OLD in line:
                hits.append(f"{file.relative_to(ROOT)}:{number}")
    assert not hits, "「釦」は書かない (「ボタン」と書く): " + ", ".join(hits)


#: 赤の知らせの文の 1 文目の語 (操作 → 字。依頼書 N156 の作る物の 0 の表。本体の選び 11-05 10:5x)
REFUSAL_HEADS = (
    "保存できません。",
    "取り消せません。",
    "削除できません。",
    "送れません。",
    "受け入れられません。",
    "断れません。",
    "発行できません。",
    "受け入れられません。",
    "着手できません。",
    "記録できません。",
    "出荷できません。",
    "読み取れません。",
)
#: 赤の知らせの文 (断りの文) を見る services のファイル → 見ない関数 (画面でない口 = 相手の会社への返事の文を作る関数)
REFUSAL_FILES = {
    "src/seizou/orders/services.py": (),
    "src/seizou/shipping/services.py": (),
    "src/seizou/purchasing/services.py": ("mark_sent", "mark_accepted", "mark_rejected"),
    "src/seizou/billing/services.py": (),
    "src/seizou/workorders/services.py": (),
    "src/seizou/progress/services.py": (),
    # ここから下はほかの部品 (頭を raise の所に足した物)
    "src/seizou/masters/services.py": ("candidates",),
    "src/seizou/stock/services.py": (),
    "src/seizou/attachments/services.py": (),
    "src/seizou/notes/services.py": (),
    "src/seizou/bookmarks/services.py": (),
    "src/seizou/accounts/services.py": (),
    "src/seizou/feedback/services.py": (),
    "src/seizou/manuals/services.py": (),
    "src/seizou/connect/services.py": ("record_inbox", "record_acceptance", "record_shipping_notice", "_payload_date", "_payload_decimal", "_payload_price_basis"),
    "src/seizou/intake/services.py": (
        "extract_order", "extract_to_inbox", "_block_for", "_ask_claude", "_check_reply", "_text", "_number_text", "_date_text",
    ),
    "src/seizou/core/services.py": (),
}
#: raise の外で断りの文を作って返す関数 (raise は変数で渡す)。返す字も頭で始まる
REFUSAL_RETURNS = {
    "src/seizou/billing/services.py": ("cancel_refusal",),
    "src/seizou/shipping/services.py": ("cancel_refusal",),
}
#: REFUSAL_RETURNS の関数の文を入れた変数の名前 (raise ValueError(refusal) の形。文は上の関数で見る)
REFUSAL_LOCALS = ("refusal",)


def _module_values(tree):
    """モジュールの頭の「名前 = 式」(断りの文の定数)。"""
    return {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
    }


def _head(node, names):
    """文の式の頭の字。字・f-string・定数の名前・足し算の左・.format を辿る。辿れなければ None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        return _head(node.values[0], names)
    if isinstance(node, ast.FormattedValue):
        return _head(node.value, names)
    if isinstance(node, ast.Name) and node.id in names:
        return _head(names[node.id], names)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _head(node.left, names)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return _head(node.func.value, names)
    return None


def _refusal_heads(path, skip):
    """(行, 関数の名前, 文の頭) を返す。raise ValueError(文) と raise RuleViolation(番号, 文) の文を見る。"""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    names = _module_values(tree)
    found = {}
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef) or func.name in skip:
            continue
        for node in ast.walk(func):
            if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and isinstance(node.exc.func, ast.Name)):
                continue
            index = {"ValueError": 0, "RuleViolation": 1}.get(node.exc.func.id)
            if index is None or len(node.exc.args) <= index:
                continue
            message = node.exc.args[index]
            if isinstance(message, ast.Name) and message.id in REFUSAL_LOCALS:
                continue
            found.setdefault(node.lineno, (func.name, _head(message, names)))
    for func in ast.walk(tree):
        if isinstance(func, ast.FunctionDef) and func.name in REFUSAL_RETURNS.get(path, ()):
            for node in ast.walk(func):
                if isinstance(node, ast.Return) and node.value is not None:
                    head = _head(node.value, names)
                    if head:
                        found.setdefault(node.lineno, (func.name, head))
    return [(line, func, head) for line, (func, head) in sorted(found.items())]


def test_refusal_messages_start_with_head():
    """赤の知らせの文 (services の断り) は REFUSAL_HEADS の語 (「保存できません。」「取り消せません。」など) のどれかで始まる。

    本人の決め 11-05 09:5x (「できない操作の画面で文言を統一した方がいい」)。文の型は「<操作>できません。<理由>。<先にすること>。」。
    見るのは REFUSAL_FILES の services だけ (画面でない口の関数は外す)。
    """
    bad = []
    for path, skip in REFUSAL_FILES.items():
        for line, func, head in _refusal_heads(path, skip):
            if head is None or not head.startswith(REFUSAL_HEADS):
                bad.append(f"{path}:{line} ({func}) {head!r}")
    assert not bad, "断りの文の頭が REFUSAL_HEADS の語のどれでもない: " + ", ".join(bad)


def test_notice_puts_head_first_and_rule_number_last():
    """知らせの描き方 (core/_notices.html。N156): 1 文目を太字で先頭に、見出しの語は出さず、決まりの番号は末尾に小さく出す。"""
    number = '<span class="font-mono text-xs text-base-content/50">{}</span>'
    html = render_to_string(
        "core/_notices.html",
        {"notice": RuleViolation("R05", "保存できません。作り物の理由です。作り物の先にすることです。"), "warnings": []},
    )
    assert '<span class="font-semibold">保存できません。</span> 作り物の理由です。作り物の先にすることです。 ' + number.format("R05") in html
    assert html.index("保存できません。") < html.index("R05") < html.index("<button")

    html = render_to_string("core/_notices.html", {"notice": Notice("入力", "取り消せません。作り物の理由です。"), "warnings": []})
    assert '<span class="font-semibold">取り消せません。</span> 作り物の理由です。</span>' in html
    assert "入力" not in html
    assert "font-mono" not in html

    html = render_to_string(
        "core/_notices.html",
        {"notice": None, "warnings": [CheckWarning("R01", "作り物の状況の文です。作り物の数の文です。")]},
    )
    assert '<span class="font-semibold">作り物の状況の文です。</span> 作り物の数の文です。 ' + number.format("R01") in html
    assert "<button" not in html


class _StopCheck(checks.Check):
    name = "T56"
    action = "tests.wording"
    mode = "stop"

    def run(self, data, user):
        return "作り物の状況の文です。作り物の先にすることです。"


def test_check_stop_message_starts_with_save_head():
    """ポカよけの「止める」の赤の文は「保存できません。」+ 検査の文 (checks.run の 1 か所。N156)。"""
    checks.clear("tests.wording")
    checks.register(_StopCheck())
    try:
        with pytest.raises(CheckViolation) as caught:
            checks.run("tests.wording", {}, user=None)
    finally:
        checks.clear("tests.wording")
    assert caught.value.code == "T56"
    assert caught.value.message == "保存できません。作り物の状況の文です。作り物の先にすることです。"
