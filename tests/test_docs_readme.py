"""README と、README から分けた docs/ の文書・SECURITY・THIRD_PARTY_NOTICES の形を、作品の実物と照らし合わせて確かめる。

README は入口だけにし、節は docs/ の文書に分けた (2026-11-10)。節を見る試験は、分けた先の文書を読む。

標準の library だけを使い、Django を読まない。
設定の既定は src/seizou/core/conf.py を ast で読み (conf.py は Django を import するため)、
同梱物の版は scripts/vendor.toml を tomllib で読む。
"""

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOCS = [
    "README.md",
    "CONTRIBUTING.md",
    "GOVERNANCE.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    # README から分けた文書
    "docs/getting-started.md",
    "docs/plugin.md",
    "docs/screens.md",
    "docs/settings.md",
    "docs/connect.md",
    "docs/rules.md",
    "docs/production.md",
    "docs/development.md",
    "docs/roadmap.md",
    "docs/why.md",
    "docs/hackathon.md",
]

# README から分けた節の置き場 (見出し → 文書)
GETTING_STARTED = "docs/getting-started.md"
PLUGIN = "docs/plugin.md"
SCREENS = "docs/screens.md"
SETTINGS = "docs/settings.md"
ROADMAP = "docs/roadmap.md"
DEVELOPMENT = "docs/development.md"
SECTION_DOCS = {
    "## 前提": GETTING_STARTED,
    "## 選べる設定": SETTINGS,
    "## 取引先とつなぐ": "docs/connect.md",
    "## 決まり": "docs/rules.md",
    "## 会期後の道筋": ROADMAP,
}

# README の選べる設定の鍵 (01 の 9 節の 5 つ)
SETTING_KEYS = [
    "order_form",
    "partial_shipment",
    "over_shipment",
    "over_workorder",
    "over_payment",
    "order_number",
    "production_plan",
    "assistant_model",
    "intake_model",
    "feedback_model",
    "menu",
    "access_control",
]

# THIRD_PARTY_NOTICES の表の名前 → scripts/vendor.toml の項目の名前の頭
VENDOR_NAMES = {
    "Tailwind CSS": "tailwindcss",
    "daisyUI": "daisyui",
    "htmx": "htmx",
    "Tom Select": "tom-select",
    "Noto Sans JP": "noto-sans-jp",
    "jsQR": "jsqr",
}


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def _section(text, heading):
    """見出し (## の行) から次の ## の見出しの前までの行。見出しが無ければ空。"""
    lines = text.splitlines()
    start = None
    for number, line in enumerate(lines):
        if line.strip() == heading:
            start = number + 1
            break
    if start is None:
        return []
    body = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        body.append(line)
    return body


def _table_rows(lines):
    """表の行 (| で始まる行) を、前後の空白と ` を外したセルの並びにする。見出しと区切りの行も含む。"""
    rows = []
    for line in lines:
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        rows.append(cells)
    return rows


def _conf_defaults():
    """src/seizou/core/conf.py の DEFAULTS の辞書を、import せずに読む。"""
    tree = ast.parse(_read("src/seizou/core/conf.py"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "DEFAULTS" for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("conf.py に DEFAULTS が無い")


def _as_doc_value(value):
    """既定の値を README の書き方にする (真偽は YAML の true / false)。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def test_readme_has_new_sections():
    for heading, name in SECTION_DOCS.items():
        headings = {line.strip() for line in _read(name).splitlines() if line.startswith("## ")}
        assert heading in headings, f"{name} に見出し「{heading}」が無い"


def test_readme_links_every_split_doc():
    """README (入口) から、分けた文書の全部へ link がある。"""
    text = _read("README.md")
    for name in DOCS:
        if name.startswith("docs/"):
            assert f"]({name})" in text, f"README に {name} への link が無い"


def test_readme_settings_table_matches_defaults():
    rows = _table_rows(_section(_read(SETTINGS), "## 選べる設定"))
    assert rows, f"{SETTINGS} の選べる設定に表が無い"
    header = rows[0]
    assert "設定の鍵" in header and "既定" in header, "選べる設定の表の見出しに「設定の鍵」と「既定」が無い"
    key_column = header.index("設定の鍵")
    default_column = header.index("既定")
    table = {row[key_column]: row[default_column] for row in rows[2:]}
    defaults = _conf_defaults()
    for key in SETTING_KEYS:
        assert key in table, f"選べる設定の表に {key} が無い"
        assert table[key] == _as_doc_value(defaults[key]), (
            f"{key} の既定が conf.py と違う ({SETTINGS}: {table[key]}、conf.py: {defaults[key]!r})"
        )


def test_readme_links_rules_md():
    assert re.search(r"\]\(RULES\.md\)", _read("README.md")), "README に RULES.md への link が無い"


def test_readme_after_period_is_five_lines_or_less():
    body = _section(_read(ROADMAP), "## 会期後の道筋")
    items = [line for line in body if line.startswith("- ")]
    assert items, f"{ROADMAP} の会期後の道筋に箇条が無い"
    assert len(items) <= 5, f"会期後の道筋の箇条が {len(items)} 行ある (5 行まで)"


def test_readme_uses_based_on_wording():
    assert "項目を元にした" in _read("README.md"), "README に「項目を元にした」が無い"


def test_third_party_versions_match_vendor_toml():
    vendor = tomllib.loads(_read("scripts/vendor.toml"))["item"]
    rows = _table_rows(_section(_read("THIRD_PARTY_NOTICES.md"), "## 同梱する物"))
    assert rows, "THIRD_PARTY_NOTICES の同梱する物に表が無い"
    header = rows[0]
    name_column = header.index("名前")
    version_column = header.index("版")
    table = {row[name_column]: row[version_column] for row in rows[2:]}
    for doc_name, prefix in VENDOR_NAMES.items():
        matched = [item for item in vendor if item["name"].startswith(prefix)]
        assert matched, f"vendor.toml に {prefix} の項目が無い"
        versions = {item["version"] for item in matched}
        assert len(versions) == 1, f"vendor.toml の {prefix} の項目の版がそろっていない: {sorted(versions)}"
        assert doc_name in table, f"THIRD_PARTY_NOTICES の表に {doc_name} が無い"
        assert table[doc_name] == matched[0]["version"], (
            f"{doc_name} の版が vendor.toml と違う (文書: {table[doc_name]}、vendor.toml: {matched[0]['version']})"
        )


def test_security_names_env_file_and_partner_secret():
    text = _read("SECURITY.md")
    assert ".env" in text, "SECURITY に .env が無い"
    assert "SEIZOU_PARTNER_" in text, "SECURITY に SEIZOU_PARTNER_ が無い"


# 鍵の値に見える字の並び: base64 の字が 32 字以上続き、英大文字・英小文字・数字が混ざる物。
# URL は外してから探す (URL の道の部分は base64 の字だけで長く続くことがあるため)。
SECRET_LIKE = re.compile(r"[A-Za-z0-9+/]{32,}={0,2}")
URL = re.compile(r"https?://\S+")


def _looks_secret(run):
    return (
        re.search(r"[A-Z]", run) is not None
        and re.search(r"[a-z]", run) is not None
        and re.search(r"[0-9]", run) is not None
    )


def test_no_secret_like_values_in_docs():
    for name in DOCS:
        text = URL.sub(" ", _read(name))
        found = [run for run in SECRET_LIKE.findall(text) if _looks_secret(run)]
        assert not found, f"{name} に鍵の値に見える字の並びがある: {[run[:8] + '…' for run in found]}"


# ---- 画面の一覧・プラグインの表・E2E・依存の確かめ (今夜ほかのレーンが画面を足しても落ちない形) ----
# README に書いた物が実物にある、までを見る (部分集合)。実物の全部を README に並べさせない。


def test_readme_has_plugin_screen_and_flow_sections():
    for heading, name in [("## Claude Code のプラグイン", PLUGIN), ("## 画面の一覧", SCREENS), ("## 業務の流れ", SCREENS)]:
        headings = {line.strip() for line in _read(name).splitlines() if line.startswith("## ")}
        assert heading in headings, f"{name} に見出し「{heading}」が無い"
    flow = {line.strip() for line in _section(_read(SCREENS), "## 業務の流れ")}
    for heading in ["### 生産計画", "### 注文書の読み取り", "### 使い方", "### 業務マニュアル", "### CSV の取り込み"]:
        assert heading in flow, f"{SCREENS} の「業務の流れ」に小節「{heading}」が無い"


# urls.py の MENU と、core の CORE_MENU の入口は、どちらも ("<字>", "<URL の名前>") の形で書いてある
MENU_ENTRY = re.compile(r'\("([^"]+)", "')


def _menu_labels_in_source():
    labels = set()
    for path in (ROOT / "src" / "seizou").glob("*/urls.py"):
        labels.update(MENU_ENTRY.findall(path.read_text(encoding="utf-8")))
    labels.update(MENU_ENTRY.findall(_read("src/seizou/core/context_processors.py")))
    return labels


def test_readme_menu_entries_exist_in_source():
    rows = _table_rows(_section(_read(SCREENS), "## 画面の一覧"))
    assert rows, f"{SCREENS} の画面の一覧に表が無い"
    header = rows[0]
    assert "入口" in header, "画面の一覧の表の見出しに「入口」が無い"
    column = header.index("入口")
    entries = [row[column] for row in rows[2:]]
    assert entries, "画面の一覧の表に行が無い"
    known = _menu_labels_in_source()
    for entry in entries:
        assert entry in known, f"画面の一覧の入口「{entry}」が、urls.py の MENU と CORE_MENU に無い"


def test_readme_skill_table_matches_plugin_skills():
    rows = _table_rows(_section(_read(PLUGIN), "## Claude Code のプラグイン"))
    assert rows, f"{PLUGIN} のプラグインの節に表が無い"
    documented = set()
    for row in rows[2:]:
        documented.update(re.findall(r"^/seizou:([a-z]+)$", row[0]))
    on_disk = {path.parent.name for path in (ROOT / "plugin" / "skills").glob("*/SKILL.md")}
    assert documented == on_disk, f"表の命令 {sorted(documented)} と plugin/skills の skill {sorted(on_disk)} が違う"


def test_readme_e2e_env_names_are_in_conftest():
    conftest = _read("tests/e2e/conftest.py")
    names = set()
    for name in [DEVELOPMENT, "CONTRIBUTING.md"]:
        names.update(re.findall(r"SEIZOU_E2E[A-Z_]*", _read(name)))
    assert "SEIZOU_E2E" in names, f"{DEVELOPMENT} に環境変数 SEIZOU_E2E の説明が無い"
    for name in sorted(names):
        assert name in conftest, f"{name} が tests/e2e/conftest.py に無い"


def test_third_party_lists_anthropic_with_locked_version():
    lock = tomllib.loads(_read("uv.lock"))
    locked = [package["version"] for package in lock["package"] if package["name"] == "anthropic"]
    assert len(locked) == 1, f"uv.lock の anthropic の項目が {len(locked)} 個ある (1 個のはず)"
    rows = _table_rows(_section(_read("THIRD_PARTY_NOTICES.md"), "## Python の依存 (同梱しない)"))
    assert rows, "THIRD_PARTY_NOTICES の Python の依存に表が無い"
    name_column = rows[0].index("名前")
    found = [row for row in rows[2:] if row[name_column] == "anthropic"]
    assert found, "THIRD_PARTY_NOTICES の Python の依存の表に anthropic の行が無い"
    assert locked[0] in " ".join(found[0]), f"anthropic の行に uv.lock の版 {locked[0]} が無い"


def _project_dependency(name):
    """pyproject.toml の dependencies から、名前 (大文字と小文字を区別しない) が合う字を 1 つ返す。"""
    dependencies = tomllib.loads(_read("pyproject.toml"))["project"]["dependencies"]
    found = [
        dependency
        for dependency in dependencies
        if re.match(r"[A-Za-z0-9_.-]+", dependency).group(0).lower() == name.lower()
    ]
    assert len(found) == 1, f"pyproject.toml の dependencies に {name} が {len(found)} 個ある (1 個のはず)"
    return found[0]


def _locked_django_version():
    lock = tomllib.loads(_read("uv.lock"))
    locked = [package["version"] for package in lock["package"] if package["name"] == "django"]
    assert len(locked) == 1, f"uv.lock の django の項目が {len(locked)} 個ある (1 個のはず)"
    return locked[0]


def test_security_names_django_lower_bound_of_pyproject():
    django = _project_dependency("Django")
    assert django in _read("SECURITY.md"), f"SECURITY.md に pyproject の Django の字 {django} が無い"


def test_third_party_lists_django_with_locked_version():
    version = _locked_django_version()
    rows = _table_rows(_section(_read("THIRD_PARTY_NOTICES.md"), "## Python の依存 (同梱しない)"))
    assert rows, "THIRD_PARTY_NOTICES の Python の依存に表が無い"
    name_column = rows[0].index("名前")
    found = [row for row in rows[2:] if row[name_column] == "Django"]
    assert found, "THIRD_PARTY_NOTICES の Python の依存の表に Django の行が無い"
    assert version in " ".join(found[0]), f"Django の行に uv.lock の版 {version} が無い"


def test_locked_django_has_security_release_of_2026_11_03():
    version = _locked_django_version()
    assert tuple(int(part) for part in version.split(".")) >= (6, 1, 3), (
        f"uv.lock の Django が {version} (2026-11-03 の安全の修正の 6.1.3 より前)"
    )


def test_security_names_requests_and_pyyaml_lower_bounds():
    security = _read("SECURITY.md")
    for name in ["requests", "PyYAML"]:
        dependency = _project_dependency(name)
        assert ">=" in dependency, f"pyproject の {dependency} に下限 (>=) が無い"
        assert dependency in security, f"SECURITY.md に pyproject の {name} の字 {dependency} が無い"


def _intake_api_key_env():
    """src/seizou/intake/services.py の API_KEY_ENV の字を、import せずに読む。"""
    tree = ast.parse(_read("src/seizou/intake/services.py"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "API_KEY_ENV" for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("intake/services.py に API_KEY_ENV が無い")


def test_security_and_readme_name_intake_api_key_env():
    env_name = _intake_api_key_env()
    for name in ["SECURITY.md", "README.md"]:
        assert env_name in _read(name), f"{name} に読み取りの鍵の環境変数 {env_name} が無い"


MCP_HEADING = "## 手元の Claude Code から操作する (MCP)"


def test_readme_mcp_section_tools_exist_in_source():
    text = _read(PLUGIN)
    headings = [line.strip() for line in text.splitlines() if line.startswith("## ")]
    assert MCP_HEADING in headings, f"{PLUGIN} に見出し「{MCP_HEADING}」が無い"
    plugin_index = headings.index("## Claude Code のプラグイン")
    assert headings[plugin_index + 1] == MCP_HEADING, (
        f"「{MCP_HEADING}」が「## Claude Code のプラグイン」のすぐ次の見出しでない (次は {headings[plugin_index + 1]})"
    )
    rows = _table_rows(_section(text, MCP_HEADING))
    assert rows, f"{PLUGIN} の MCP の節に表が無い"
    assert rows[0] == ["道具", "何をするか", "読む / 書く"], f"MCP の節の表の見出しが違う: {rows[0]}"
    tools = rows[2:]
    assert len(tools) >= 12, f"MCP の節の表の道具が {len(tools)} 行しか無い (12 行以上)"
    source = _read("src/seizou/core/mcp_tools.py")
    for row in tools:
        name = row[0]
        assert f"def {name}(" in source, f"MCP の節の道具 {name} が src/seizou/core/mcp_tools.py に無い"
        assert row[2] in ("読む", "書く"), f"MCP の節の道具 {name} の「読む / 書く」が {row[2]}"


# 相対の link (http で始まらない物) の先。# より後ろ (節の印) は見ない
RELATIVE_LINK = re.compile(r"\]\((?!https?:|mailto:|#)([^)#\s]+)(?:#[^)]*)?\)")


def test_relative_links_in_docs_exist():
    """README と分けた文書の相対の link の先が、リポジトリに在る (README を分けた時に link が切れないように)。"""
    for name in DOCS:
        base = (ROOT / name).parent
        for target in RELATIVE_LINK.findall(_read(name)):
            assert (base / target).exists(), f"{name} の link の先 {target} が無い"
