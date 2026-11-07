"""パスワードの決まりの表 (seizou.core.passwords.VALIDATORS。依頼書 N354)。

表は Django を読まず、雛形の settings.py の AUTH_PASSWORD_VALIDATORS と、accounts.services の確かめが同じ表を読むこと。
settings に表を置くと、changepassword が呼ぶのと同じ validate_password が短い字やよくある語を断ること。
点検の「パスワードの決まり」の行が、表が空なら直すを出し、本番の条件のときだけ出ること。
利用者とパスワードの字は作り物だけを使う。
"""

import ast
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError

import seizou.scaffold
from seizou.accounts import services
from seizou.core import doctor, passwords
from seizou.core.passwords import VALIDATORS

#: 雛形の settings.py に足す 1 行 (古い置き場に足す手順と点検の直し方と同じ字)
IMPORT_LINE = "from seizou.core.passwords import VALIDATORS as AUTH_PASSWORD_VALIDATORS"
#: 4 つの確かめに通る作り物の字 (8 字以上・語でない・数字だけでない)
PASSES = "kumo-tests-2611"


def _class_path(validator) -> str:
    cls = type(validator)
    return f"{cls.__module__}.{cls.__qualname__}"


def test_table_module_imports_no_django():
    tree = ast.parse(Path(passwords.__file__).read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")

    assert not [name for name in names if name == "django" or name.startswith("django.")]


def test_scaffold_settings_reads_table():
    text = (Path(seizou.scaffold.__file__).parent / "settings.py.tmpl").read_text(encoding="utf-8")
    lines = text.splitlines()

    assert lines.count(IMPORT_LINE) == 1
    # import の並びの PARTS の次の行 (古い置き場に足す手順と同じ場所)
    assert lines[lines.index(IMPORT_LINE) - 1] == "from seizou.parts import PARTS"


def test_settings_table_refuses_short_and_common_like_changepassword(settings):
    settings.AUTH_PASSWORD_VALIDATORS = VALIDATORS
    user = get_user_model()(username="sakuhin-tester")

    assert len(password_validation.get_default_password_validators()) == 4
    for weak in ("1", "password"):
        with pytest.raises(ValidationError):
            password_validation.validate_password(weak, user)
    password_validation.validate_password(PASSES, user)


def test_accounts_validators_follow_table():
    made = services.password_validators()

    assert [_class_path(v) for v in made] == [row["NAME"] for row in VALIDATORS]
    assert made[0].min_length == 8


# ---------------------------------------------------------------- 点検の「パスワードの決まり」の行


def test_doctor_row_fixes_empty_table(settings):
    settings.AUTH_PASSWORD_VALIDATORS = []
    [row] = doctor.check_password_validators()

    assert (row.ok, row.label) == (False, "パスワードの決まり")
    assert "seizou.core.passwords" in row.fix

    settings.AUTH_PASSWORD_VALIDATORS = VALIDATORS
    assert doctor.check_password_validators() == [doctor.Row(True, "パスワードの決まり", "確かめ 4 個")]


@pytest.mark.django_db
def test_doctor_row_only_in_production(settings):
    settings.AUTH_PASSWORD_VALIDATORS = VALIDATORS

    assert [row.label for row in doctor.run(production=True)].count("パスワードの決まり") == 1
    assert "パスワードの決まり" not in [row.label for row in doctor.run(production=False)]
