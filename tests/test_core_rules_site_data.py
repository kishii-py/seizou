"""決まりの検査が、会社の置き場の data のフォルダを歩かないことの試験。

data のフォルダは、置き場の控え (backups/)・練習の写し (practice/)・collectstatic の写し (staticfiles/)・
上げたファイル (media/)・締め出しと AI の回数の数 (cache/) の 5 つ。渡したフォルダのすぐ下にあるときだけ歩かない。
出所 = 精査 Q4 (預かる人の運用と版を上げる稽古。2026-11-10) のつまずく所 1。
控え (backups/<日時>/site/) に残った古い写しの違反で、開発の設定の全部の画面が 500 になり、点検も「直す」のまま残った。

見る物:
(a) 渡したフォルダのすぐ下の 5 つのフォルダの中を読まない (違反にも走査の本数にも入れない)。
(b) 控えの中の migrations の場所を決まり (R16) に渡さない。
(c) 同じ名前でも深い所 (会社の app の中) は今のとおり読む。
(d) 直に渡したフォルダは、名前にかかわらず中を読む。
(e) 5 つの名前が、data のフォルダを作る側の字 (控え・練習・雛形の settings・締め出しと AI の回数の既定の道) とずれていない。
(f) 起動の検査 (check_at_startup) と点検 (doctor.check_rules) の道でも、控えの違反を数えない。
字と道は作り物。
"""

import re
from pathlib import Path

import pytest

import seizou
import seizou.core.rules as rules
from seizou.core import backup, doctor, practice
from seizou.core.rules import check
from seizou.core.rules import engine

SEIZOU_DIR = Path(seizou.__file__).parent

#: R14 の違反の 1 行 (tests/test_core_rules.py の RAW_LINE と同じ形)
RAW_LINE = "def heavy(Item):\n    return Item.objects.raw('SELECT 1')\n"

#: R18 の違反の 1 行 (hx-headers に X-CSRFToken)
HEADER_HTML = """<div hx-post="/x/" hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'>送る</div>\n"""

#: 控えの中の、置き場のテンプレートの古い写し
BACKUP_TEMPLATE = Path("backups", "20261110-224142", "site", "templates", "q4", "r18.html")

#: 控えのほかの 4 つの data のフォルダに置く .py
OTHER_DATA_FILES = (
    Path("practice", "x", "services.py"),
    Path("staticfiles", "x.py"),
    Path("media", "x.py"),
    Path("cache", "x.py"),
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class FolderRule:
    """場所だけを受ける見本の決まり (tests/test_core_rules.py の migrations の試験と同じ形)。"""

    CODE = "R99"
    MESSAGE = "見本の決まり"
    FIX = "見本の直し方"

    def __init__(self):
        self.seen = []

    @staticmethod
    def applies(path):
        return True

    @staticmethod
    def find(tree):
        return []

    def find_in_dir(self, path):
        self.seen.append(path)
        return [1]


def test_site_data_folders_right_under_the_given_folder_are_not_walked(tmp_path):
    """(a) すぐ下の 5 つのフォルダの違反は数えず、置き場の services.py の 1 件だけを出す。"""
    _write(tmp_path / BACKUP_TEMPLATE, HEADER_HTML)
    for relative in OTHER_DATA_FILES:
        _write(tmp_path / relative, RAW_LINE)
    source = _write(tmp_path / "services.py", RAW_LINE)

    result = check([tmp_path])

    assert [(v.code, v.path) for v in result.violations] == [("R14", str(source))]
    assert result.scanned == 1


def test_migrations_in_backup_are_not_given_to_rules(tmp_path):
    """(b) 控えの中の migrations の場所は決まりに渡さず、置き場の migrations の場所は今のとおり渡す。"""
    copied = tmp_path / "backups" / "20261110-224142" / "site" / "fields" / "migrations"
    _write(copied / "0001_initial.py", RAW_LINE)
    own = tmp_path / "fields" / "migrations"
    _write(own / "0001_initial.py", RAW_LINE)
    rule = FolderRule()

    result = engine.run([tmp_path], [rule])

    assert rule.seen == [own]
    assert [(v.code, v.path) for v in result.violations] == [("R99", str(own))]


def test_same_names_deeper_inside_apps_are_still_walked(tmp_path):
    """(c) 会社の app の中の media/ と cache/ は今のとおり読み、違反を出す。"""
    in_orders = _write(tmp_path / "orders" / "media" / "x.py", RAW_LINE)
    in_fields = _write(tmp_path / "fields" / "cache" / "x.py", RAW_LINE)

    result = check([tmp_path])

    assert sorted((v.code, v.path) for v in result.violations) == sorted([("R14", str(in_orders)), ("R14", str(in_fields))])
    assert result.scanned == 2


def test_site_data_folder_given_directly_is_walked(tmp_path):
    """(d) 控えのフォルダを直に渡したときは、中の違反を出す。"""
    copied = _write(tmp_path / BACKUP_TEMPLATE, HEADER_HTML)

    result = check([tmp_path / "backups"])

    assert [(v.code, v.path, v.line) for v in result.violations] == [("R18", str(copied), 1)]
    assert result.scanned == 1


def test_names_match_where_site_data_folders_are_made():
    """(e) 5 つの名前が、控え・練習・雛形の settings・締め出しと AI の回数の既定の道の字と同じ。"""
    assert backup.BACKUPS_DIR in engine.SITE_DATA_DIRS
    assert practice.PRACTICE_DIR in engine.SITE_DATA_DIRS

    template = (SEIZOU_DIR / "scaffold" / "settings.py.tmpl").read_text(encoding="utf-8")
    for setting, expected in (("STATIC_ROOT", "staticfiles"), ("MEDIA_ROOT", "media")):
        found = re.findall(rf'^{setting} = BASE_DIR / "([^"]+)"$', template, re.M)
        assert found == [expected], setting
        assert expected in engine.SITE_DATA_DIRS

    for module, leaf in (("login_guard.py", "login"), ("ai.py", "ai")):
        text = (SEIZOU_DIR / "core" / module).read_text(encoding="utf-8")
        found = re.findall(rf'Path\(base\) / "([^"]+)" / "{leaf}"', text)
        assert found == ["cache"], module
    assert "cache" in engine.SITE_DATA_DIRS


@pytest.fixture
def clear_last_result(monkeypatch):
    monkeypatch.setattr(rules, "_last_result", None)


def test_startup_and_doctor_do_not_count_old_copy_in_backup(settings, tmp_path, clear_last_result):
    """(f) 置き場に控えの違反だけがあるとき、起動の検査は違反 0 件、点検の行は良い。"""
    settings.DEBUG = True
    settings.BASE_DIR = tmp_path
    _write(tmp_path / BACKUP_TEMPLATE, HEADER_HTML)

    result = rules.check_at_startup()

    assert result is rules.last_result()
    assert result.violations == ()
    assert result.scanned == check([SEIZOU_DIR]).scanned

    rows = doctor.check_rules()

    assert rows
    assert all(row.ok for row in rows)
    assert rows[0].detail.startswith("違反 0 件")
