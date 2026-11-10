"""docs/connect.md (取引先とのつながり) の「発注の状態」の表を、発注の model の状態と照らし合わせる (標準の library だけ。Django を読まない)。

発注の状態 (purchasing.PurchaseOrder.STATUS_CHOICES) に状態を足したら、docs/connect.md の「発注の状態」の表にも行を足すための試験。
向きはコードから文書 (コードにある状態の表示名が、文書に全部あること)。
src/seizou/purchasing/models.py は Django を import するので、ast で読む (tests/test_docs_readme.py の _conf_defaults と同じ読み方)。
STATUS_CHOICES の組の 1 つ目は名前 (STATUS_DRAFT など) なので literal_eval でなく、組の 2 つ目の字だけを取る。
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "connect.md"
MODELS = ROOT / "src" / "seizou" / "purchasing" / "models.py"
STATUS_COUNT = 9


def _read(path):
    return path.read_text(encoding="utf-8")


def _status_labels():
    """PurchaseOrder の STATUS_CHOICES の表示名 (組の 2 つ目の字) を、並びのまま返す。"""
    tree = ast.parse(_read(MODELS))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "PurchaseOrder":
            for item in node.body:
                if isinstance(item, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "STATUS_CHOICES" for target in item.targets
                ):
                    return [pair.elts[1].value for pair in item.value.elts]
    raise AssertionError("purchasing/models.py の PurchaseOrder に STATUS_CHOICES が無い")


def test_status_labels_are_read():
    labels = _status_labels()
    assert len(labels) == STATUS_COUNT, f"発注の状態の数が {STATUS_COUNT} でない: {labels}"
    assert all(isinstance(label, str) and label for label in labels), f"状態の表示名に字でない物がある: {labels}"


def test_connect_doc_names_every_purchase_order_status():
    text = _read(DOC)
    missing = [label for label in _status_labels() if label not in text]
    assert not missing, f"docs/connect.md に発注の状態の表示名が無い: {missing}"
