"""決まりの検査の枠 (seizou.core.rules) と管理コマンド seizou_check のテスト。

枠のテストは check を直に呼び、Django の設定に頼らない。
管理コマンドと起動用の関数のテストだけが試験用の settings を使う。
"""

import os
import subprocess
import sys
import time
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

import seizou
import seizou.core.rules as rules
from seizou.core.rules import check
from seizou.core.rules.engine import run

FIXTURES = Path(__file__).parent / "fixtures" / "rules"
SEIZOU_DIR = Path(seizou.__file__).parent

RAW_LINE = "def heavy(Item):\n    return Item.objects.raw('SELECT 1')\n"


def _line_of(path: Path, text: str) -> int:
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if text in line:
            return number
    raise AssertionError(f"{path} に {text} が無い")


# 黙らせ方


def test_numbered_mark_silences_and_is_counted():
    result = check([FIXTURES / "ignore"])

    assert result.silenced == 1


def test_mark_without_number_does_not_silence():
    source = FIXTURES / "ignore" / "services.py"
    result = check([FIXTURES / "ignore"])

    assert [(v.code, v.line) for v in result.violations] == [
        ("R14", _line_of(source, "# seizou: ignore 番号の無い印")),
    ]


def test_mark_on_the_line_above_silences_but_other_number_does_not(tmp_path):
    source = tmp_path / "selectors.py"
    source.write_text(
        "def heavy(Item):\n"
        "    # seizou: ignore[R14] 作り物の見本。直上の行の印\n"
        "    first = Item.objects.raw('SELECT 1')\n"
        "    second = Item.objects.raw('SELECT 2')  # seizou: ignore[R15] 番号が違う\n"
        "    return first, second\n",
        encoding="utf-8",
    )

    result = check([source])

    assert result.silenced == 1
    assert [(v.code, v.line) for v in result.violations] == [("R14", 4)]


def test_mark_at_the_end_of_the_line_above_does_not_reach_the_next_line(tmp_path):
    source = tmp_path / "selectors.py"
    source.write_text(
        "def heavy(Item):\n"
        "    first = Item.objects.raw('SELECT 1')  # seizou: ignore[R14] この行だけ\n"
        "    second = Item.objects.raw('SELECT 2')\n"
        "    return first, second\n",
        encoding="utf-8",
    )

    result = check([source])

    assert result.silenced == 1
    assert [v.line for v in result.violations] == [3]


# 歩き方


def test_empty_folder_scans_zero_files(tmp_path):
    result = check([tmp_path])

    assert result.scanned == 0
    assert result.violations == ()


def test_package_scan_is_clean_and_fast():
    started = time.perf_counter()
    result = check([SEIZOU_DIR])
    elapsed = time.perf_counter() - started

    assert elapsed < 10
    assert result.violations == ()
    assert result.silenced == 0
    assert result.scanned >= 1


def test_one_file_is_checked_within_one_second():
    started = time.perf_counter()
    check([SEIZOU_DIR / "core" / "views.py"])

    assert time.perf_counter() - started < 1


def test_migrations_tests_and_library_folders_are_not_walked(tmp_path):
    for folder in ("migrations", "tests", ".venv", "site-packages", "node_modules", ".git"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "module.py").write_text(RAW_LINE, encoding="utf-8")
    (tmp_path / "services.py").write_text(RAW_LINE, encoding="utf-8")

    result = check([tmp_path])

    assert result.scanned == 1
    assert [Path(v.path).name for v in result.violations] == ["services.py"]


def test_migrations_folder_location_is_given_to_rules_without_reading_it(tmp_path):
    migrations = tmp_path / "orders" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "0001_initial.py").write_text(RAW_LINE, encoding="utf-8")
    seen = []

    class FolderRule:
        CODE = "R99"
        MESSAGE = "見本の決まり"
        FIX = "見本の直し方"

        @staticmethod
        def applies(path):
            return True

        @staticmethod
        def find(tree):
            return []

        @staticmethod
        def find_in_dir(path):
            seen.append(path)
            return [1]

    result = run([tmp_path], [FolderRule])

    assert seen == [migrations]
    assert result.scanned == 0
    assert [(v.code, v.path, v.line) for v in result.violations] == [("R99", str(migrations), 1)]


def test_file_given_directly_is_read_whatever_its_place(tmp_path):
    source = tmp_path / "tests" / "migrations" / "test_module.py"
    source.parent.mkdir(parents=True)
    source.write_text(RAW_LINE, encoding="utf-8")

    result = check([source])

    assert result.scanned == 1
    assert [v.code for v in result.violations] == ["R14"]


def test_syntax_error_is_skipped_and_not_counted(tmp_path):
    (tmp_path / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    (tmp_path / "fine.py").write_text("VALUE = 1\n", encoding="utf-8")

    result = check([tmp_path])

    assert result.scanned == 1
    assert result.violations == ()


def test_same_file_through_two_paths_is_scanned_once(tmp_path):
    source = tmp_path / "services.py"
    source.write_text(RAW_LINE, encoding="utf-8")

    result = check([tmp_path, source])

    assert result.scanned == 1
    assert len(result.violations) == 1


def test_import_and_check_work_without_django_settings():
    env = {key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"}
    code = (
        "import sys, seizou.core.rules as r; "
        "res = r.check([sys.argv[1]]); "
        "print(res.scanned, len(res.violations))"
    )

    completed = subprocess.run(
        [sys.executable, "-c", code, str(FIXTURES / "r14" / "bad")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ["4", "5"]


# 管理コマンド (試験用の settings を使う)


def _run_command(*paths):
    out = StringIO()
    try:
        call_command("seizou_check", *[str(p) for p in paths], stdout=out)
    except CommandError as error:
        return error.returncode, out.getvalue()
    return 0, out.getvalue()


def test_command_fails_on_violations_and_prints_each_line():
    source = FIXTURES / "r15" / "bad" / "views.py"

    code, output = _run_command(FIXTURES / "r15" / "bad")

    assert code != 0
    line = _line_of(source, "class OrderListView(")
    assert f"R15 {source}:{line} 画面は core の土台の画面" in output
    assert "直し方: " in output
    assert output.splitlines()[-1] == "合計: 違反 2 件、黙らせた 0 件、走査 1 本"


def test_command_fails_when_nothing_is_scanned(tmp_path):
    code, output = _run_command(tmp_path)

    assert code != 0
    assert output.splitlines()[-1] == "合計: 違反 0 件、黙らせた 0 件、走査 0 本"


def test_command_passes_on_clean_code():
    code, output = _run_command(SEIZOU_DIR)

    assert code == 0
    assert output.splitlines()[-1].startswith("合計: 違反 0 件、黙らせた 0 件、走査 ")


def test_command_summary_shows_silenced_count():
    code, output = _run_command(FIXTURES / "ignore")

    assert code != 0
    assert output.splitlines()[-1] == "合計: 違反 1 件、黙らせた 1 件、走査 1 本"


# 起動用の関数 (試験用の settings を使う)


@pytest.fixture
def clear_last_result(monkeypatch):
    monkeypatch.setattr(rules, "_last_result", None)


def test_startup_does_nothing_when_debug_is_false(settings, clear_last_result):
    settings.DEBUG = False

    assert rules.check_at_startup() is None
    assert rules.last_result() is None


def test_startup_checks_package_and_base_dir_when_debug(settings, tmp_path, clear_last_result):
    settings.DEBUG = True
    settings.BASE_DIR = tmp_path
    (tmp_path / "services.py").write_text(RAW_LINE, encoding="utf-8")

    result = rules.check_at_startup()

    assert result is rules.last_result()
    assert [Path(v.path) for v in result.violations] == [tmp_path / "services.py"]
    assert result.scanned == check([SEIZOU_DIR]).scanned + 1


def test_startup_without_base_dir_checks_package_only(settings, clear_last_result):
    settings.DEBUG = True
    del settings.BASE_DIR

    result = rules.check_at_startup()

    assert result.violations == ()
    assert result.scanned == check([SEIZOU_DIR]).scanned


def test_startup_does_not_raise_when_the_check_itself_fails(settings, monkeypatch, clear_last_result):
    settings.DEBUG = True

    def broken(paths):
        raise RuntimeError("見本の誤り")

    monkeypatch.setattr(rules, "check", broken)

    assert rules.check_at_startup() is None
    assert rules.last_result() is None
