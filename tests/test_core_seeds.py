"""種と CSV の字 → 値の決まりの置き場 (seizou.core.seeds)。

作り物のデータの読み込み (seizou.core.demo) が seeds と同じ関数を使うことと、
CSV の取り込み (seizou.core.csvio) が種の読み込みの module と yaml を読まないことを確かめる。
部品の種の読み方 (decimal・guard) と demo の鍵の確かめが、写していた頃と同じ文を出すことも確かめる。
"""

import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from seizou.core import demo, seeds
from seizou.core.exceptions import RuleViolation


def test_demo_uses_the_same_functions_as_seeds():
    assert demo._coerce is seeds.coerce
    assert demo._coerce_ext is seeds.coerce_ext
    assert demo._is_blank is seeds.is_blank
    assert demo._fields_by_label is seeds.fields_by_label


def test_csvio_does_not_import_demo_or_yaml():
    # この試験の process は demo を既に読んでいるので、別の process で import だけをする
    env = {key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"}
    code = "import sys, seizou.core.csvio; print('seizou.core.demo' in sys.modules, 'yaml' in sys.modules)"

    completed = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ["False", "False"]


def test_decimal_reads_number_and_names_unreadable_text():
    assert seeds.decimal("1.5", "数量") == Decimal("1.5")
    assert seeds.decimal(" 2 ", "数量") == Decimal("2")

    for raw in ("たくさん", "NaN"):
        with pytest.raises(ValueError) as error:
            seeds.decimal(raw, "数量")
        assert str(error.value) == f"数量「{raw}」を数として読めません。"


def test_guard_puts_file_and_number_on_rule_violation():
    with pytest.raises(ValueError) as error:
        with seeds.guard("payments.yaml", 1):
            raise RuleViolation("R1", "作り物の決まりに当たりました。")

    assert str(error.value) == "payments.yaml の 1 件目: R1: 作り物の決まりに当たりました。"


def test_demo_check_entry_keys_keeps_its_words():
    path = Path("orders.yaml")

    with pytest.raises(ValueError) as error:
        demo._check_entry_keys(path, 2, "文字", ("備考",), "受注")
    assert str(error.value) == "orders.yaml の 2 件目の受注の形が違います (鍵と値の並びではありません)。"

    with pytest.raises(ValueError) as error:
        demo._check_entry_keys(path, 3, {"備考": "", "色": "", "形": ""}, ("備考",), "受注")
    assert str(error.value) == "orders.yaml の 3 件目の受注の鍵「色」「形」が分かりません。"
