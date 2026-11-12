"""docs/operations.md (運用の手引き) の、外への写しと運用の 5 つの節の形を確かめる。

標準の library だけを使い、この試験の中では Django を使わない (BANNED を import する tests/test_wording.py は Django を読む。
tests/test_docs_vps.py と同じ)。
鍵の行の見方・鍵の値に見える字・書かない言い回しは、既にある文書の試験の名前を import して同じ見方で見る (字をここに写さない)。
"""

import ast
import re
from pathlib import Path

from tests.test_docs_phrases import BANNED_PHRASES
from tests.test_docs_readme import SECRET_LIKE, URL, _looks_secret
from tests.test_docs_vps import ENV_LINE, PLACEHOLDER, SECRET_NAME_PARTS
from tests.test_wording import BANNED

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "operations.md"
VPS_DOC = ROOT / "docs" / "vps.md"

# 5 つの節の見出しに含まれる字 (外への写し・残す期間・止まったとき・定期の仕事・土台の寿命)
SECTIONS = ("外への写し", "取引の記録を残す期間", "止まったとき", "定期の仕事と見張り", "土台の寿命")
# 手引きに必ずある字 (残す期間の目安・失う量・cron の時刻帯・外へ写す道具)
WORDS = ("7 年", "RPO", "CRON_TZ", "restic")

# 寿命の表: 出典の URL の欄は https:// を含み、見た日の欄は「未確認」か日付 (2026-11-08 の形)
LIFESPAN_SECTION = "土台の寿命"
SOURCE_COLUMN = "出典の URL"
SEEN_COLUMN = "見た日"
UNCONFIRMED = "未確認"
SEEN_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# vps.md に書かない物: 写し先の名前と restic の URL (出典と外の名前は operations.md にだけ置く)
COPY_DESTINATION_HOST = "r2.cloudflarestorage.com"
RESTIC = "restic"


def _read(path):
    return path.read_text(encoding="utf-8")


def _headings(text):
    return [line.strip() for line in text.splitlines() if line.startswith("## ")]


def _section(text, word):
    """見出し (## ) に word を含む節の行。節は見出しの行から、次の ## の見出しの前まで (### の小節は含む)。"""
    for part in re.split(r"^(?=## )", text, flags=re.MULTILINE):
        lines = part.splitlines()
        if lines and lines[0].startswith("## ") and word in lines[0]:
            return lines
    return []


def _table_rows(lines):
    """| で始まる行を、欄の字の list にする (見出しの行と --- の行を含む)。"""
    return [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines if line.startswith("|")]


def test_operations_has_new_sections():
    headings = _headings(_read(DOC))
    missing = [word for word in SECTIONS if not any(word in heading for heading in headings)]
    assert not missing, f"docs/operations.md の ## の見出しに無い節: {missing}"


def test_operations_has_key_words():
    text = _read(DOC)
    missing = [word for word in WORDS if word not in text]
    assert not missing, f"docs/operations.md に無い字: {missing}"


def test_lifespan_table_rows_have_source_and_seen_date():
    rows = _table_rows(_section(_read(DOC), LIFESPAN_SECTION))
    assert rows, f"「{LIFESPAN_SECTION}」の節に表が無い"
    header = rows[0]
    assert SOURCE_COLUMN in header, f"寿命の表の見出しに「{SOURCE_COLUMN}」が無い"
    assert SEEN_COLUMN in header, f"寿命の表の見出しに「{SEEN_COLUMN}」が無い"
    source, seen = header.index(SOURCE_COLUMN), header.index(SEEN_COLUMN)
    data = rows[2:]
    assert data, "寿命の表に行が無い"

    no_url = [row[0] for row in data if "https://" not in row[source]]
    assert not no_url, f"出典の URL が https:// で無い行: {no_url}"
    bad_seen = [row[0] for row in data if row[seen] != UNCONFIRMED and SEEN_DATE.fullmatch(row[seen]) is None]
    assert not bad_seen, f"見た日が「{UNCONFIRMED}」でも日付でもない行: {bad_seen}"


def test_operations_env_secret_lines_are_placeholders():
    lines = [(name, value.strip()) for name, value in ENV_LINE.findall(_read(DOC))]
    secret_lines = [(name, value) for name, value in lines if any(part in name for part in SECRET_NAME_PARTS)]

    assert secret_lines, "鍵の行の拾い方が、外への写しの節の鍵の行を拾っていない"
    bad = [name for name, value in secret_lines if value and PLACEHOLDER.fullmatch(value) is None]
    assert not bad, f"鍵の行の値が置き字 (<値> の形) でない: {bad}"


def test_operations_has_no_secret_like_values():
    text = URL.sub(" ", _read(DOC))
    found = [run for run in SECRET_LIKE.findall(text) if _looks_secret(run)]
    assert not found, f"docs/operations.md に鍵の値に見える字の並びがある: {[run[:8] + '…' for run in found]}"


def test_operations_has_no_banned_phrases():
    text = _read(DOC)
    for phrase in [*BANNED_PHRASES, BANNED]:
        assert phrase not in text, f"docs/operations.md に「{phrase}」がある"


def test_vps_has_no_copy_destination_or_restic_url():
    text = _read(VPS_DOC)
    assert COPY_DESTINATION_HOST not in text, f"docs/vps.md に {COPY_DESTINATION_HOST} がある (operations.md にだけ置く)"
    restic_urls = [url for url in URL.findall(text) if RESTIC in url.lower()]
    assert not restic_urls, f"docs/vps.md に restic の URL がある (operations.md にだけ置く): {restic_urls}"


# ---- 版を上げる命令・立てた後に項目を足す手順・Windows の控え・控えの site/ (開発の記録 docs/nights/20261111/N388.md) ----

PRODUCTION_DOC = ROOT / "docs" / "production.md"
BACKUP_PY = ROOT / "src" / "seizou" / "core" / "backup.py"

# 版を上げる 3 段の PyPI の命令は、入れる先の python を名指しする (端末の pip は別の Python に入れるため)
UPGRADE_SECTION = "版を上げる手順"
PYPI_UPGRADE = "uv pip install --python <python> -U seizou"
OLD_PYPI_UPGRADE = "`pip install -U seizou`"
PRODUCTION_DAILY_SECTION = "毎日の運用"
PYTHON_OPTION = "--python"

# 立てた後に会社ごとの項目を足す節 (2 つのファイルと makemigrations)
FIELDS_SECTION = "会社ごとの項目"
FIELDS_WORDS = ("fields/models.py", "ITEM_FIELDS", "makemigrations fields")

# Windows の PC で控えと点検を回す小節 (cron の代わりのタスク スケジューラ。例は実機で未確認)
WINDOWS = "Windows"
WINDOWS_WORDS = ("タスク スケジューラ", "未確認")

# 控えの中身の site/ の行と、backup.py の許しの一覧
BACKUP_SECTION = "毎日の控え"
SITE_COPY = "`site/`"
SITE_LISTS = ("SITE_FILES", "SITE_DIRS")


def _subsection(text, word):
    """見出し (### ) に word を含む小節の行。小節は見出しの行から、次の ## か ### の見出しの前まで。"""
    lines = text.splitlines()
    for start, line in enumerate(lines):
        if line.startswith("### ") and word in line:
            rest = range(start + 1, len(lines))
            end = next((i for i in rest if lines[i].startswith(("## ", "### "))), len(lines))
            return lines[start:end]
    return []


def _backup_site_names():
    """backup.py の SITE_FILES と SITE_DIRS の字。Django を読まないので、ast で定数だけを読む。"""
    names = {}
    for node in ast.parse(BACKUP_PY.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name) and target.id in SITE_LISTS:
            names[target.id] = ast.literal_eval(value)
    return names


def test_upgrade_names_python_for_pypi_install():
    section = "\n".join(_section(_read(DOC), UPGRADE_SECTION))
    assert PYPI_UPGRADE in section, f"「{UPGRADE_SECTION}」の節に {PYPI_UPGRADE} が無い"
    assert OLD_PYPI_UPGRADE not in section, f"「{UPGRADE_SECTION}」の節に、入れる先を名指ししない {OLD_PYPI_UPGRADE} が残っている"

    daily = "\n".join(_section(_read(PRODUCTION_DOC), PRODUCTION_DAILY_SECTION))
    assert PYTHON_OPTION in daily, f"docs/production.md の「{PRODUCTION_DAILY_SECTION}」の節に {PYTHON_OPTION} が無い"


def test_operations_has_section_for_adding_company_fields():
    section = _section(_read(DOC), FIELDS_SECTION)
    assert section, f"docs/operations.md に「{FIELDS_SECTION}」を含む ## の節が無い"
    body = "\n".join(section)
    missing = [word for word in FIELDS_WORDS if word not in body]
    assert not missing, f"「{FIELDS_SECTION}」の節に無い字: {missing}"


def test_operations_has_windows_scheduler_subsection():
    subsection = _subsection(_read(DOC), WINDOWS)
    assert subsection, f"docs/operations.md に「{WINDOWS}」を含む ### の小節が無い"
    body = "\n".join(subsection)
    missing = [word for word in WINDOWS_WORDS if word not in body]
    assert not missing, f"「{WINDOWS}」の小節に無い字: {missing}"


def test_backup_site_line_names_every_site_file_and_dir():
    names = _backup_site_names()
    assert set(names) == set(SITE_LISTS), f"backup.py から読めなかった一覧: {set(SITE_LISTS) - set(names)}"

    lines = [line for line in _section(_read(DOC), BACKUP_SECTION) if line.startswith("- ") and SITE_COPY in line]
    assert len(lines) == 1, f"「{BACKUP_SECTION}」の節に、{SITE_COPY} の箇条が 1 行でない: {len(lines)} 行"
    missing = [name for name in [*names["SITE_FILES"], *names["SITE_DIRS"]] if f"`{name}" not in lines[0]]
    assert not missing, f"「{BACKUP_SECTION}」の {SITE_COPY} の行に、backup.py の一覧の名前が無い: {missing}"


# 点検の節の見出しの字と、点検の一覧に要る字 (点検の行 check_template_overrides の名前。開発の記録 docs/nights/20261111/N420.md)
DOCTOR_SECTION = "立ち上げの確かめ"
TEMPLATE_OVERRIDES = "上書きしたテンプレート"


def test_doctor_list_names_template_overrides():
    section = _section(_read(DOC), DOCTOR_SECTION)
    assert section, f"docs/operations.md に「{DOCTOR_SECTION}」を含む ## の節が無い"
    items = [line for line in section if line.startswith("- ") and TEMPLATE_OVERRIDES in line]
    assert len(items) == 1, f"「{DOCTOR_SECTION}」の節の点検の一覧に「{TEMPLATE_OVERRIDES}」の行が 1 行でない: {len(items)} 行"
