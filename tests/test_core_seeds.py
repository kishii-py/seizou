"""種と CSV の字 → 値の決まりの置き場 (seizou.core.seeds)。

作り物のデータの読み込み (seizou.core.demo) が seeds と同じ関数を使うことと、
CSV の取り込み (seizou.core.csvio) が種の読み込みの module と yaml を読まないことを確かめる。
"""

import os
import subprocess
import sys

from seizou.core import demo, seeds


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
